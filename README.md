# OpenSquawk Bridge

OpenSquawk Bridge connects your flight simulator to
[opensquawk.de](https://opensquawk.de). The app runs as a small desktop window,
streams simulator telemetry to OpenSquawk, and opens the push-to-talk radio page
for ATC.

You can run the app directly from source. For regular users, there is a
self-updating installer that always fetches the latest version from GitHub.

## Install (macOS)

Download one file, open it, done — the app installs and updates itself.

1. Download **OpenSquawk-Bridge-macOS.dmg**.
2. Open the `.dmg` and drag **OpenSquawk Bridge** to `Applications`.
3. The first launch is blocked because the app is unsigned: **right-click the
   app → `Open`**, then confirm `Open` again. (Only needed once.)

On first launch the app sets itself up — this takes about a minute (it downloads
a private Python runtime and the app's dependencies). After that it starts
quickly. On every launch it checks GitHub and updates itself automatically, so
you always run the latest version without reinstalling.

Everything the installer downloads lives under
`~/Library/Application Support/OpenSquawk Bridge/`. Delete that folder to reset
the install; delete `~/.opensquawk-bridge/` to forget your account link.

## Install (Linux)

Same idea: one self-updating file, no `apt install` needed.

1. Download **OpenSquawk-Bridge-linux.sh**.
2. Make it executable and run it:

```bash
chmod +x OpenSquawk-Bridge-linux.sh
./OpenSquawk-Bridge-linux.sh
```

The first launch sets things up (downloads a private Python runtime, the app's
dependencies, and a self-contained Qt webview backend — about a minute and a bit
more download than macOS). It also adds an **OpenSquawk Bridge** entry to your
application menu, so afterwards you can start it like any other app. Every launch
updates itself from GitHub automatically.

Installed files live under `~/.local/share/OpenSquawk Bridge/`. Delete that
folder to reset the install; delete `~/.opensquawk-bridge/` to forget your
account link.

Qt (via PySide6) needs a graphical desktop; it works out of the box on standard
X11/Wayland desktops. Very minimal installs may still miss common system
libraries (e.g. `libnss3`, `libxkbcommon`) — install those from your distro if
the app fails to open a window.

> A Windows installer using the same approach is coming next.

## Features

- Link this device to an OpenSquawk account with a pairing code.
- Open the push-to-talk radio page on this PC, or scan a QR code to use it on a
  phone or tablet.
- Bind a global push-to-talk trigger: keyboard key, key combo, or joystick/HOTAS
  button.
- Choose a simulator source:
  - `Dummy flight` for testing without a simulator.
  - `MSFS 2024`, when the simulator is running.
  - `MSFS 2020`, when the simulator is running.
  - `X-Plane` and `FlightGear` are prepared, but not active yet.
- Show live status and telemetry, including flight phase, radio frequencies,
  squawk, position, speed, and altitude.
- Create flight actions: chains of waits, key presses, and clicks triggered by
  app start, sim detection, aircraft detection, GPS jump, hotkey, or joystick
  button.
- Optionally start with the operating system, so the Bridge is ready after login.

## Requirements

- Python 3.10 or newer.
- Git, if you want to clone the repository.
- Windows, macOS, or Linux.

The UI also needs a webview runtime:

- Windows: Microsoft Edge WebView2 Runtime. It is usually present on Windows 11.
  It may be missing on Windows 10; the app shows a startup message and opens the
  download if needed.
- macOS: WKWebView is built into the system.
- Linux: WebKitGTK/PyGObject, for example on Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-webkit2-4.1
```

### Installing Requirements On Windows

Windows users do not use `apt install`. Install the required tools like this:

1. Install Python from [python.org/downloads/windows](https://www.python.org/downloads/windows/).
   During setup, enable `Add python.exe to PATH`.
2. Install Git for Windows from [git-scm.com/download/win](https://git-scm.com/download/win).
   The default installer options are fine.
3. Open `Command Prompt` or `PowerShell` and check both tools:

```bat
python --version
git --version
```

If `python` is not found, close and reopen the terminal. If it still is not
found, reinstall Python and make sure `Add python.exe to PATH` is enabled.

If the app says Microsoft Edge WebView2 Runtime is missing, install the
`Evergreen Bootstrapper` from Microsoft:
[Download WebView2 Runtime](https://go.microsoft.com/fwlink/p/?LinkId=2124703).

## Run From Source

In the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bridge_app.py
```

On Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bridge_app.py
```

For local backend testing, override the target URL:

```bash
OPENSQUAWK_BASE_URL=http://localhost:3000 python bridge_app.py
```

## Build The macOS Installer (maintainers)

The macOS download is a small **thin launcher** — an unsigned `.app` that
carries no Python and no app code. It downloads the latest source from GitHub at
runtime, builds an isolated environment with [uv](https://astral.sh/uv), and
runs the app (see `installer/mac/bootstrap.py`).

Because of this, you do **not** rebuild the launcher for every app change — just
push to `main` (or publish a GitHub Release) and users get it on their next
launch. Rebuild the launcher only when the launcher/bootstrap itself changes:

```bash
python3 installer/build_launcher.py           # everything this host can build
python3 installer/build_launcher.py --linux   # only the Linux .sh
python3 installer/build_launcher.py --mac      # only the macOS .app + .dmg
```

Outputs in `dist/`:

- `OpenSquawk-Bridge-macOS.dmg` + `OpenSquawk Bridge.app` — macOS only (needs
  `sips`/`iconutil`/`hdiutil`).
- `OpenSquawk-Bridge-linux.sh` — a single self-updating file; plain text
  assembly, so it builds on any host. `bootstrap.py` is embedded into it.

Link the `.dmg` / `.sh` on the website. No signing.

The update channel is: latest GitHub **Release** if any exists, otherwise the
tip of `main`. So publishing a release (`gh release create vX.Y.Z --notes …`)
gates updates once you want that; until then every push to `main` ships.

## Build A Standalone App (PyInstaller)

The build is created for the operating system you run it on. Build the Windows
`.exe` on Windows and the macOS `.app` on macOS.

### macOS Or Linux

```bash
./build.sh
```

Or directly:

```bash
python3 build.py
```

### Windows

Double-click `build.bat`, or run it from Command Prompt:

```bat
build.bat
```

Or directly:

```bat
python build.py
```

The build script installs the required build tools, bundles the files from
`web/`, creates the correct icon format, and runs PyInstaller.

## Where To Find The Finished App

After the build, the output is in `dist/`:

| Operating system | File/folder | How to start |
| --- | --- | --- |
| Windows | `dist\OpenSquawk Bridge.exe` | Double-click the file |
| macOS | `dist/OpenSquawk Bridge.app` | Double-click the app, or drag it to Applications |
| Linux | `dist/OpenSquawk Bridge/` | Run the binary inside the folder |

The builds are currently not signed:

- Windows may show SmartScreen. Choose `More info`, then `Run anyway`.
- macOS may block the first launch. Right-click the `.app`, choose `Open`, then
  confirm `Open` again.

## App Setup

1. Start OpenSquawk Bridge.
2. Click `Open login in browser`.
3. Sign in on opensquawk.de and link the shown pairing code.
4. Return to the app. After linking, the main view appears.
5. Under `Simulator`, choose the source:
   - Use `Dummy flight` for testing.
   - For Microsoft Flight Simulator, choose the running simulator version.
6. Optional: under `System`, enable `Start with operating system`. The app then
   configures autostart for your operating system.
7. Under `Live ATC`, open the radio page on this PC or scan the QR code with a
   second device.
8. Optional: under `Push-to-talk hotkey`, bind a key, key combo, or joystick
   button.

The local configuration is stored here:

```text
~/.opensquawk-bridge/config.json
```

Logging out forgets the local link and creates a new pairing code.

## Project Layout

```text
bridge_app.py          Desktop app, API, autostart, HTTP, background threads
msfs_source.py         MSFS 2020/2024 detection and SimConnect telemetry
simulator.py           Dummy flight for testing without a simulator
actions.py             Flight-action chains, triggers, and execution
web/index.html         UI
web/style.css          Styling
web/app.js             Frontend logic
build.py               PyInstaller build for standalone .exe/.app/Linux bundle
build.bat              Windows build
build.sh               macOS/Linux build
installer/             Self-updating thin launcher (macOS installer)
tests/                 Tests
```

## Tests

```bash
python -m pytest
```
