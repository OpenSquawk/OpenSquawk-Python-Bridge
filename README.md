# OpenSquawk Bridge (Desktop)

A small cross-platform desktop app that links a flight simulator to
[opensquawk.de](https://opensquawk.de) and streams live telemetry to the
OpenSquawk Bridge.

It currently ships a **dummy simulator** (an animated flight from takeoff to
landing) so the whole flow — login, linking, status, live streaming — works
end to end without a real sim attached. Replacing the dummy with a real
SimConnect / X-Plane connector later only means swapping out `simulator.py`.

## What it does

- **Self-generated pairing code** — a 6-character code (A–Z / 0–9) created on
  first run, stored in `~/.opensquawk-bridge/config.json`, sent as
  `x-bridge-token` on every request and shown on the login screen.
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
  app doesn't hold.) Logout stops streaming, forgets the link, and **issues a new
  pairing code** so the previous link can no longer be reused. To also unlink the
  old code server-side, use the website.
- **Simulator picker** — `MSFS 2020` (active), `MSFS 2024` and `X-Plane`
  (*coming soon*, disabled).
- **Sim active switch (dummy)** — turns the dummy flight on. It reports
  `POST /api/bridge/status` and streams raw SimConnect-style telemetry to
  `POST /api/bridge/data` about once per second.
- **Live status** — a traffic light shows `Streaming` (data accepted < 3 s ago),
  `Stalling`, or `Idle`.
- **Live telemetry (collapsible)** — expand the panel to see IAS / ALT / V/S /
  N1 / gear / flaps and a **flight-profile graphic**: the aircraft moves along a
  takeoff → cruise → landing trajectory and highlights the current phase
  (Parked → Taxi → Takeoff → Climb → Cruise → Descent → Approach → Landing →
  Rollout).
- **Push-to-talk hotkey (collapsible)** — bind a key, key combo, or
  joystick/HOTAS button to transmit from anywhere; hold it while flying and the
  radio on this PC records even when the simulator is in front.
- **Flight actions (collapsible)** — replay a chain of *wait / key / click*
  steps, fired automatically when a new flight is detected (sim connected +
  aircraft loaded, once per session) or on a global hotkey / joystick button.
  Add steps by hand or **Record** your real key presses and clicks (the pauses
  between them become wait steps). Clicks use absolute screen coordinates, so
  keep the same window layout you recorded with. Steps and the trigger persist
  in `~/.opensquawk-bridge/config.json`.

The app has two screens: the **login screen** (with the pairing code) when not
linked, and the **main screen** once linked, with a sign-out button in the top-
right corner.

## Requirements

- **Python 3.10+**
- OS webview runtime (already present on all major platforms):
  - **Windows**: Microsoft Edge WebView2 Runtime (preinstalled on Win 11; on
    Win 10 often missing). If missing, the app detects it on startup and points
    you to the download — or grab the "Evergreen Bootstrapper" directly from
    <https://go.microsoft.com/fwlink/p/?LinkId=2124703>.
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

## Building a clickable app (.exe / .app)

One command builds a standalone, double-clickable app for the OS you run it on —
no Python needed by the end user. It installs the build tools, converts the icon
to `.ico`/`.icns`, bundles the `web/` assets, and runs
[PyInstaller](https://pyinstaller.org/):

```bash
python build.py
```

Convenience wrappers do the same thing:

- **Windows**: double-click [`build.bat`](build.bat)
- **macOS / Linux**: run [`./build.sh`](build.sh)

You get, in `dist/`:

| OS | Output | How the user runs it |
|----|--------|----------------------|
| Windows | `OpenSquawk Bridge.exe` | a single file — just double-click |
| macOS | `OpenSquawk Bridge.app` | double-click (or drag to Applications) |
| Linux | `OpenSquawk Bridge/` | run the binary inside |

> PyInstaller can only build for the OS it runs on, so run `build.py` once on each
> platform you want to ship (build the `.exe` on a Windows machine, the `.app` on
> a Mac). On Windows the target machine also needs the Edge WebView2 runtime
> (preinstalled on Win 11).

### Distributing to non-technical users

The build is **not code-signed**, so the OS will warn on first launch:

- **macOS**: right-click the `.app` → **Open** → **Open** (only needed once). To
  share it, zip the `.app`. Proper signing/notarization needs an Apple Developer
  account.
- **Windows**: SmartScreen may show "Windows protected your PC" → **More info** →
  **Run anyway**. Proper signing needs a code-signing certificate.

The icon master lives at [`web/assets/icon.png`](web/assets/icon.png); `build.py`
generates the platform icon from it automatically. When running **from source**
the app also sets the dock/taskbar icon at runtime (macOS via PyObjC, Windows via
the Win32 API), so it never shows the generic Python icon.
