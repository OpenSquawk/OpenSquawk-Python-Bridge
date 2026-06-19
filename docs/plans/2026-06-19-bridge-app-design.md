# OpenSquawk Bridge — Desktop App Design

Date: 2026-06-19

## Goal
A cross-platform Python desktop app that links a flight simulator to opensquawk.de
via the Bridge token flow, shows live telemetry, and (for now) ships a dummy
simulator that streams a plausible animated flight to the backend.

## Stack
- **pywebview** (native window) + local **HTML/CSS/JS** frontend → matches the
  OpenSquawk CI (dark gradient, cyan glow `#06b6d4`, glass cards). Runs on
  Windows/macOS/Linux, packagable with PyInstaller.
- Python backend exposed to JS via `window.expose` / `pywebview.api`.

## Token / Login flow
1. App generates a random token (32 hex chars) on first run, persisted in
   `~/.opensquawk-bridge/config.json`.
2. "Login" opens the system browser at `…/bridge/connect?token=<token>` and starts
   a poll loop.
3. Poll loop calls `GET /api/bridge/me` with `x-bridge-token` every 2s. When
   `connected: true`, the UI flips to the signed-in state (name/email shown).
4. "Logout" is **local only** — `/disconnect` requires a browser session the app
   does not hold. Logout stops streams and forgets the connection. **The token is
   kept** for a stable device identity.

## Simulator selection
Dropdown: `MSFS 2020` (active) · `MSFS 2024 (coming soon)` · `X-Plane (coming soon)`
(latter two disabled). Selection is sent only as status metadata.

## Status display
- **Account**: Not linked / Linked (name).
- **Simulator**: Disconnected / Connected (driven by the "Sim active" switch — dummy).
- **Live stream**: traffic light — `Streaming` (last `/data` < 3s, green),
  `Stalling` (yellow), `Idle` (grey). Derived from last successful POST.
- Live telemetry values (IAS, ALT, VS, N1, gear, flaps, parking brake).

## Dummy simulator
"Sim active" switch → sets `simConnected=true` via `POST /status` and starts an
animated flight loop generating raw SimConnect fields (`ias_kt`,
`altitude_ft_indicated`, `n1_pct`, `gear_handle`, `flaps_index`, `parking_brake`,
`on_ground`, `vertical_speed_fpm`, …), POSTed to `/data` ~1/s. `flightActive`
toggles when airborne.

## Flight profile visualization
Below the telemetry: an SVG trajectory (ground → climb → cruise → descent →
landing). An aircraft icon moves along the path based on the loop's normalized
`progress` (0..1), and the current **phase** (Parked, Taxi, Takeoff, Climb,
Cruise, Descent, Approach, Landing) is labelled/highlighted.

## Error handling
- Network errors are caught, surfaced as a banner; loops keep retrying.
- `401` on `/me` = not yet linked → stays in "waiting for login".

## Files
```
bridge_app.py     # pywebview start + Api class (HTTP calls + state)
simulator.py      # dummy flight loop + phase model
web/index.html    # UI
web/style.css     # CI styling
web/app.js        # frontend logic, polls pywebview.api
requirements.txt
README.md         # setup, run, PyInstaller build notes
```
