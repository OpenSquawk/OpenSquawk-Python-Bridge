# OpenSquawk Bridge

OpenSquawk Bridge connects your flight simulator to
[opensquawk.de](https://opensquawk.de). The app runs as a small desktop window,
streams simulator telemetry to OpenSquawk, and opens the push-to-talk radio page
for ATC.

You can run the app directly from source. For regular users, build a clickable
Windows `.exe` or macOS `.app`.

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

## Build A Clickable App

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
build.py               Build script for .exe/.app/Linux bundle
build.bat              Windows build
build.sh               macOS/Linux build
tests/                 Tests
```

## Tests

```bash
python -m pytest
```
