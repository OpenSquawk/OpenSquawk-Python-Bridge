# OpenSquawk Bridge (Desktop)

A small cross-platform desktop app that links a flight simulator to
[opensquawk.de](https://opensquawk.de) and streams live telemetry to the
OpenSquawk Bridge.

It currently ships a **dummy simulator** (an animated flight from takeoff to
landing) so the whole flow — login, linking, status, live streaming — works
end to end without a real sim attached. Replacing the dummy with a real
SimConnect / X-Plane connector later only means swapping out `simulator.py`.

## What it does

- **Self-generated device token** — created on first run, stored in
  `~/.opensquawk-bridge/config.json`, sent as `x-bridge-token` on every request.
- **Login** — opens your browser at `…/bridge/connect?token=<token>`; you sign in
  and link the device on the website. The app polls `GET /api/bridge/me` until it
  sees `connected: true`, then shows your name/email.
- **Launch the app (once linked)** — the linked view becomes the main screen. The
  OpenSquawk push-to-talk / recording app lives at `…/pm?token=<token>`. You can:
  - **Open on this PC** — opens it in a browser window next to your simulator.
  - **Scan the QR code** — open it on a phone or tablet; it stays linked to the
    same account via the token. (QR rendering needs the `qrcode` package; it's in
    `requirements.txt`.)
- **Logout** — local only. (The server `/disconnect` needs a browser session the
  app doesn't hold, so logout stops streaming and forgets the link locally; the
  device token is **kept** for a stable identity. To fully unlink, use the
  website.)
- **Simulator picker** — `MSFS 2020` (active), `MSFS 2024` and `X-Plane`
  (*coming soon*, disabled).
- **Sim active switch (dummy)** — turns the dummy flight on. It reports
  `POST /api/bridge/status` and streams raw SimConnect-style telemetry to
  `POST /api/bridge/data` about once per second.
- **Live status** — a traffic light shows `Streaming` (data accepted < 3 s ago),
  `Stalling`, or `Idle`, plus live IAS / ALT / V/S / N1 / gear / flaps.
- **Flight profile** — a graphic shows the aircraft moving along a takeoff →
  cruise → landing trajectory and highlights the current phase
  (Parked → Taxi → Takeoff → Climb → Cruise → Descent → Approach → Landing →
  Rollout).

## Requirements

- **Python 3.10+**
- OS webview runtime (already present on all major platforms):
  - **Windows**: Microsoft Edge WebView2 Runtime (preinstalled on Win 11; on
    Win 10 install it from Microsoft if missing).
  - **macOS**: WKWebView (built in).
  - **Linux**: WebKitGTK + PyGObject, e.g. on Debian/Ubuntu:
    `sudo apt install python3-gi gir1.2-webkit2-4.1`

## Setup & run

```bash
# from this folder
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python bridge_app.py
```

Then in the app:

1. Click **Go to login** → finish linking in the browser.
2. Pick **MSFS 2020**.
3. Flip **Sim active (dummy)** on → watch the live telemetry and the aircraft fly
   the profile, and the stream light turn green.

### Pointing at a different backend

By default it talks to `https://opensquawk.de`. Override for local testing:

```bash
OPENSQUAWK_BASE_URL=http://localhost:3000 python bridge_app.py
```

## Project layout

```
bridge_app.py     # pywebview window + Api class (all HTTP + state, threads)
simulator.py      # dummy animated flight + phase model (swap for real connector)
web/index.html    # UI
web/style.css     # OpenSquawk CI styling (dark gradient, cyan glow, glass cards)
web/app.js        # frontend logic, polls pywebview.api, draws the flight profile
requirements.txt
docs/plans/        # design doc
```

## How the dummy maps to real telemetry

`simulator.py` emits the same raw field names the backend's `data.post.ts`
expects (`ias_kt`, `altitude_ft_indicated`, `n1_pct`, `gear_handle`,
`flaps_index`, `parking_brake`, `on_ground`, `vertical_speed_fpm`, …). A real
SimConnect / X-Plane bridge just needs to produce that same dict — the rest of
the app (HTTP, status, UI) stays unchanged.

## Building a standalone executable (later)

The app is structured to wrap with [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name OpenSquawkBridge \
  --add-data "web:web" \            # Windows: use "web;web"
  bridge_app.py
```

The `web/` folder must ship alongside the binary (the `--add-data` flag bundles
it). On Windows the resulting `.exe` also needs the Edge WebView2 runtime on the
target machine.
