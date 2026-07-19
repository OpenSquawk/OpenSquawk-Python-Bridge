"""FlightGear telemetry source via the built-in HTTP/JSON server (developer preview).

FlightGear must be started with its property webserver enabled, e.g.:

    fgfs --httpd=8080

We then read individual properties from http://127.0.0.1:8080/json/<path>.
No FlightGear-side configuration files are needed (unlike the generic UDP
protocol), which keeps the preview zero-setup beyond the launch flag.

Properties that don't exist on the loaded aircraft (e.g. N1 on a piston
single) come back as None and map to safe defaults, so any aircraft streams.
Pure helpers (map_props, _to_float, _to_bool) are unit-tested without a
simulator, mirroring msfs_source.
"""

from __future__ import annotations

import time

import requests

from msfs_source import PhaseEstimator
from sim_detect import process_running
from simulator import FlightState

# FlightGear's binary is "fgfs" on every OS (fgfs.exe on Windows).
_FG_PROCS = ("fgfs",)

FG_HOST = "127.0.0.1"
FG_PORT = 8080          # matches the suggested launch flag --httpd=8080
REQUEST_TIMEOUT = 1.0   # per-property HTTP timeout, seconds
RECONNECT_AFTER = 5.0   # seconds to wait before retrying a dropped connection

# Property paths fetched each tick, mapped to local keys. Values may be None
# when a property does not exist on the loaded aircraft.
_PROPS = {
    "ias_kt": "/instrumentation/airspeed-indicator/indicated-speed-kt",
    "tas_kt": "/velocities/airspeed-kt",
    "gs_kt": "/velocities/groundspeed-kt",
    "vs_fps": "/velocities/vertical-speed-fps",
    "alt_ind_ft": "/instrumentation/altimeter/indicated-altitude-ft",
    "alt_true_ft": "/position/altitude-ft",
    "pitch_deg": "/orientation/pitch-deg",
    "n1_1": "/engines/engine/n1",
    "n1_2": "/engines/engine[1]/n1",
    "eng_running": "/engines/engine/running",
    "wow": "/gear/gear/wow",
    "gear_down": "/controls/gear/gear-down",
    "flap_ratio": "/controls/flight/flaps",
    "parking_brake": "/controls/gear/brake-parking",
    "ap_alt_lock": "/autopilot/locks/altitude",
    "ap_hdg_lock": "/autopilot/locks/heading",
    "com_active_mhz": "/instrumentation/comm/frequencies/selected-mhz",
    "com_standby_mhz": "/instrumentation/comm/frequencies/standby-mhz",
    "xpdr_code": "/instrumentation/transponder/id-code",
    "lat_deg": "/position/latitude-deg",
    "lon_deg": "/position/longitude-deg",
    "hdg_true_deg": "/orientation/heading-deg",
    "aircraft": "/sim/description",
}


def _to_float(value, default: float = 0.0) -> float:
    """FlightGear's JSON serializes values as strings in some versions."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def map_props(v: dict) -> dict:
    """Convert fetched FlightGear properties into the app's raw telemetry dict.

    Keys and units mirror simulator.DummyFlight.sample().raw exactly. Like
    X-Plane, FlightGear models flaps as a 0..1 ratio, so flaps_index is
    approximated on a 0..4 scale. TAS falls back to IAS when the aircraft
    doesn't publish it.
    """
    ias = _to_float(v.get("ias_kt"))
    tas = _to_float(v.get("tas_kt"), ias) or ias
    flap_ratio = max(0.0, min(1.0, _to_float(v.get("flap_ratio"))))
    ap_engaged = bool(str(v.get("ap_alt_lock") or "").strip()
                      or str(v.get("ap_hdg_lock") or "").strip())
    return {
        "ias_kt": round(ias, 1),
        "tas_kt": round(tas, 1),
        "groundspeed_kt": round(_to_float(v.get("gs_kt")), 1),
        "vertical_speed_fpm": round(_to_float(v.get("vs_fps")) * 60.0, 0),
        "altitude_ft_indicated": round(_to_float(v.get("alt_ind_ft")), 0),
        "altitude_ft_true": round(_to_float(v.get("alt_true_ft")), 0),
        "pitch_deg": round(_to_float(v.get("pitch_deg")), 1),
        "n1_pct": round(_to_float(v.get("n1_1")), 1),
        "n1_pct_2": round(_to_float(v.get("n1_2")), 1),
        "eng_on": _to_bool(v.get("eng_running")),
        "on_ground": _to_bool(v.get("wow")),
        "gear_handle": _to_bool(v.get("gear_down")),
        "flaps_index": int(round(flap_ratio * 4)),
        "parking_brake": _to_float(v.get("parking_brake")) > 0.5,
        "autopilot_master": ap_engaged,
        "com_active_frequency": round(_to_float(v.get("com_active_mhz")), 3),
        "com_standby_frequency": round(_to_float(v.get("com_standby_mhz")), 3),
        "transponder_code": int(_to_float(v.get("xpdr_code"))),
        "latitude_deg": round(_to_float(v.get("lat_deg")), 6),
        "longitude_deg": round(_to_float(v.get("lon_deg")), 6),
        "heading_deg": round(_to_float(v.get("hdg_true_deg")) % 360.0, 1),
    }


def flightgear_running() -> bool:
    """Cheap: is a FlightGear (fgfs) process running?"""
    return process_running(_FG_PROCS)


def httpd_reachable(host: str = FG_HOST, port: int = FG_PORT,
                    timeout: float = 0.4) -> bool:
    """True if FlightGear's property webserver answers — the condition that
    actually matters (a running fgfs without --httpd gives us nothing)."""
    try:
        r = requests.get(f"http://{host}:{port}/json/position",
                         params={"d": "1"}, timeout=timeout)
        return r.ok
    except requests.RequestException:
        return False


def setup_checks(host: str = FG_HOST, port: int = FG_PORT) -> dict:
    """Ordered setup steps for the connect dialog, each with a live ok flag.

    `ready` keys off the property server being reachable — the step users most
    often miss, since FlightGear does not enable it by default.
    """
    httpd = httpd_reachable(host, port)
    proc = httpd or flightgear_running()  # a reachable server implies fgfs runs
    steps = [
        {
            "key": "process", "label": "FlightGear is running", "ok": proc,
            "hint": "Start FlightGear on this computer.",
        },
        {
            "key": "httpd",
            "label": f"Property server enabled on port {port}", "ok": httpd,
            "hint": f"Launch FlightGear with  --httpd={port}  "
                    "(Settings → Additional Settings in the launcher, or the "
                    "command line). This is off by default.",
            "detail": f"http://{host}:{port}",
        },
    ]
    return {"ready": httpd, "steps": steps}


class FlightGearSource:
    """Live FlightGear telemetry source (developer preview)."""

    id = "flightgear"
    # Selecting FlightGear before --httpd is enabled shouldn't hard-fail: keep
    # it selected and connect once the property server comes up.
    tolerant_open = True

    def __init__(self, host: str = FG_HOST, port: int = FG_PORT) -> None:
        self._base = f"http://{host}:{port}"
        self._session: requests.Session | None = None
        self._connected = False
        self._phase = PhaseEstimator()
        self._reconnect_after: float = 0.0

    def open(self) -> None:
        """Connect to a running FlightGear. Raises when the property server is
        unreachable (FlightGear not running, or started without --httpd)."""
        session = requests.Session()
        resp = session.get(f"{self._base}/json/position", params={"d": "1"},
                           timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        self._session = session
        self._connected = True

    def close(self) -> None:
        self._connected = False
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
        self._session = None

    def read_props(self) -> dict:
        """Fetch every property in _PROPS. The live seam (verify against a real
        FlightGear). A missing property yields None; an unreachable server
        raises so sample() can flip to disconnected."""
        assert self._session is not None
        out: dict = {}
        for key, path in _PROPS.items():
            try:
                resp = self._session.get(f"{self._base}/json{path}",
                                         timeout=REQUEST_TIMEOUT)
                out[key] = resp.json().get("value") if resp.ok else None
            except ValueError:
                out[key] = None
        return out

    def read_state(self) -> dict | None:
        """Quicksave is not supported in the developer preview."""
        return None

    def write_state(self, snap: dict) -> None:
        """Quickload is not supported in the developer preview — no-op."""
        return None

    def sample(self) -> FlightState | None:
        if not self._connected:
            if time.monotonic() < self._reconnect_after:
                return None
            try:
                self.close()
                self.open()
            except Exception:
                self._reconnect_after = time.monotonic() + RECONNECT_AFTER
                return None
        try:
            props = self.read_props()
        except Exception:
            self._connected = False
            self._reconnect_after = time.monotonic() + RECONNECT_AFTER
            return None
        raw = map_props(props)
        phase, progress = self._phase.update(
            on_ground=raw["on_ground"], alt=raw["altitude_ft_indicated"],
            vs=raw["vertical_speed_fpm"], ias=raw["ias_kt"],
            parking_brake=raw["parking_brake"],
        )
        aircraft = str(props.get("aircraft") or "").strip() or "FlightGear aircraft"
        return FlightState(
            raw=raw, phase=phase, progress=progress,
            flight_active=not raw["on_ground"],
            aircraft=aircraft, connected=True,
        )
