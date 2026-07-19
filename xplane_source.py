"""X-Plane telemetry source via the UDP RREF dataref protocol (developer preview).

Supports X-Plane 10.10 and newer: RREF subscriptions were introduced in 10.10,
and X-Plane 11/12 speak the same wire format (the response header's 5th byte is
"O" on X-Plane 10 and "," on 11+, so we only match the first four bytes).

How it works: on open() we send one RREF subscription per dataref to the sim's
UDP port (49000 by default); X-Plane then pushes (index, float) pairs back to
our socket at the requested rate. sample() drains whatever arrived since the
last tick. UDP is connectionless, so open() cannot verify the sim is running —
instead sample() reports disconnected while no packets arrive and periodically
re-sends the subscriptions so an X-Plane started later picks us up.

Pure helpers (build_rref_request, parse_rref_response, map_datarefs) are
unit-tested without a simulator, mirroring msfs_source.
"""

from __future__ import annotations

import socket
import struct
import time

from msfs_source import PhaseEstimator
from simulator import FlightState

XPLANE_HOST = "127.0.0.1"
XPLANE_PORT = 49000     # X-Plane's default UDP command port
SUBSCRIBE_HZ = 5        # per-dataref update rate requested from the sim
STALE_AFTER = 3.0       # seconds without packets before we report disconnected
RESUBSCRIBE_EVERY = 5.0  # seconds between subscription retries while stale

MS_TO_KT = 1.94384
M_TO_FT = 3.28084

# Datarefs subscribed via RREF, in index order (position = RREF index). All are
# available since X-Plane 10; string datarefs (aircraft name) cannot be read
# over RREF, so the aircraft label is a constant.
_DATAREFS = {
    "ias_kt": "sim/flightmodel/position/indicated_airspeed",
    "tas_ms": "sim/flightmodel/position/true_airspeed",
    "gs_ms": "sim/flightmodel/position/groundspeed",
    "vs_fpm": "sim/flightmodel/position/vh_ind_fpm",
    "alt_ind_ft": "sim/flightmodel/misc/h_ind",
    "alt_true_m": "sim/flightmodel/position/elevation",
    "pitch_deg": "sim/flightmodel/position/theta",
    "n1_1": "sim/flightmodel/engine/ENGN_N1_[0]",
    "n1_2": "sim/flightmodel/engine/ENGN_N1_[1]",
    "eng_running": "sim/flightmodel/engine/ENGN_running[0]",
    "on_ground": "sim/flightmodel/failures/onground_any",
    "gear_handle": "sim/cockpit/switches/gear_handle_status",
    "flap_ratio": "sim/flightmodel/controls/flaprqst",
    "parking_brake": "sim/flightmodel/controls/parkbrake",
    "ap_mode": "sim/cockpit/autopilot/autopilot_mode",
    "com1_10khz": "sim/cockpit/radios/com1_freq_hz",
    "com1_stdby_10khz": "sim/cockpit/radios/com1_stdby_freq_hz",
    "xpdr_code": "sim/cockpit/radios/transponder_code",
    "lat_deg": "sim/flightmodel/position/latitude",
    "lon_deg": "sim/flightmodel/position/longitude",
    "hdg_true_deg": "sim/flightmodel/position/psi",
}
_KEYS_BY_INDEX = list(_DATAREFS)


def build_rref_request(index: int, freq_hz: int, dataref: str) -> bytes:
    """One RREF subscription packet: header + rate + index + 400-byte name."""
    name = dataref.encode("ascii")
    return b"RREF\x00" + struct.pack("<ii", freq_hz, index) + name.ljust(400, b"\x00")


def parse_rref_response(data: bytes) -> dict[int, float]:
    """Decode an RREF push packet into {index: value}.

    Header is 5 bytes: b"RREF" plus one version byte ("O" on X-Plane 10, ","
    on 11+) that we skip. The body is a run of little-endian (int32, float32)
    pairs; a trailing partial record is ignored.
    """
    if len(data) < 5 or data[:4] != b"RREF":
        return {}
    out: dict[int, float] = {}
    body = data[5:]
    for off in range(0, len(body) - 7, 8):
        idx, val = struct.unpack_from("<if", body, off)
        out[idx] = val
    return out


def map_datarefs(v: dict) -> dict:
    """Convert native dataref values into the app's raw telemetry dict.

    Keys and units mirror simulator.DummyFlight.sample().raw exactly. X-Plane
    exposes the flap request as a 0..1 ratio, not a handle detent, so
    flaps_index is approximated on a 0..4 scale.
    """
    ias = float(v["ias_kt"])
    flap_ratio = max(0.0, min(1.0, float(v["flap_ratio"])))
    return {
        "ias_kt": round(ias, 1),
        "tas_kt": round(float(v["tas_ms"]) * MS_TO_KT, 1),
        "groundspeed_kt": round(float(v["gs_ms"]) * MS_TO_KT, 1),
        "vertical_speed_fpm": round(float(v["vs_fpm"]), 0),
        "altitude_ft_indicated": round(float(v["alt_ind_ft"]), 0),
        "altitude_ft_true": round(float(v["alt_true_m"]) * M_TO_FT, 0),
        "pitch_deg": round(float(v["pitch_deg"]), 1),
        "n1_pct": round(float(v["n1_1"]), 1),
        "n1_pct_2": round(float(v["n1_2"]), 1),
        "eng_on": bool(v["eng_running"]),
        "on_ground": bool(v["on_ground"]),
        "gear_handle": bool(v["gear_handle"]),
        "flaps_index": int(round(flap_ratio * 4)),
        "parking_brake": float(v["parking_brake"]) > 0.5,
        # autopilot_mode: 0 = off, 1 = flight director only, 2 = servos on
        "autopilot_master": float(v["ap_mode"]) >= 2,
        # com1_freq_hz is in units of 10 kHz (e.g. 11830 -> 118.30 MHz)
        "com_active_frequency": round(float(v["com1_10khz"]) / 100.0, 3),
        "com_standby_frequency": round(float(v["com1_stdby_10khz"]) / 100.0, 3),
        "transponder_code": int(round(float(v["xpdr_code"]))),
        "latitude_deg": round(float(v["lat_deg"]), 6),
        "longitude_deg": round(float(v["lon_deg"]), 6),
        "heading_deg": round(float(v["hdg_true_deg"]) % 360.0, 1),
    }


class XPlaneSource:
    """Live X-Plane telemetry source (developer preview, X-Plane 10+)."""

    id = "xplane"
    AIRCRAFT = "X-Plane aircraft"

    def __init__(self, host: str = XPLANE_HOST, port: int = XPLANE_PORT) -> None:
        self._addr = (host, port)
        self._sock: socket.socket | None = None
        self._vals: dict[str, float] = {}
        self._last_rx: float | None = None
        self._resub_at: float = 0.0
        self._phase = PhaseEstimator()

    def open(self) -> None:
        """Create the UDP socket and subscribe. Cannot fail on a stopped sim —
        UDP is connectionless, so liveness shows up in sample() instead."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._vals = {}
        self._last_rx = None
        self._subscribe(SUBSCRIBE_HZ)
        self._resub_at = time.monotonic() + RESUBSCRIBE_EVERY

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._subscribe(0)  # rate 0 = unsubscribe
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._vals = {}
        self._last_rx = None

    def _subscribe(self, freq_hz: int = SUBSCRIBE_HZ) -> None:
        if self._sock is None:
            return
        for index, dataref in enumerate(_DATAREFS.values()):
            self._sock.sendto(build_rref_request(index, freq_hz, dataref), self._addr)

    def _drain(self) -> None:
        """Pull every pending UDP packet into self._vals."""
        assert self._sock is not None
        while True:
            try:
                data, _ = self._sock.recvfrom(4096)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            values = parse_rref_response(data)
            if values:
                self._last_rx = time.monotonic()
                for idx, val in values.items():
                    if 0 <= idx < len(_KEYS_BY_INDEX):
                        self._vals[_KEYS_BY_INDEX[idx]] = val

    def read_state(self) -> dict | None:
        """Quicksave is not supported in the developer preview."""
        return None

    def write_state(self, snap: dict) -> None:
        """Quickload is not supported in the developer preview — no-op."""
        return None

    def sample(self) -> FlightState | None:
        if self._sock is None:
            return None
        try:
            self._drain()
        except Exception:
            return None
        now = time.monotonic()
        stale = self._last_rx is None or (now - self._last_rx) > STALE_AFTER
        if stale or len(self._vals) < len(_DATAREFS):
            # No (complete) data — retry the subscription now and then so an
            # X-Plane launched after us starts pushing.
            if now >= self._resub_at:
                try:
                    self._subscribe(SUBSCRIBE_HZ)
                except Exception:
                    pass
                self._resub_at = now + RESUBSCRIBE_EVERY
            return None
        raw = map_datarefs(self._vals)
        phase, progress = self._phase.update(
            on_ground=raw["on_ground"], alt=raw["altitude_ft_indicated"],
            vs=raw["vertical_speed_fpm"], ias=raw["ias_kt"],
            parking_brake=raw["parking_brake"],
        )
        return FlightState(
            raw=raw, phase=phase, progress=progress,
            flight_active=not raw["on_ground"],
            aircraft=self.AIRCRAFT, connected=True,
        )
