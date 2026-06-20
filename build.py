#!/usr/bin/env python3
"""Build a clickable OpenSquawk Bridge app for whatever OS you run this on.

    python build.py

Output (in ./dist):
  Windows -> "OpenSquawk Bridge.exe"   single double-clickable file
  macOS   -> "OpenSquawk Bridge.app"   double-click (or drag to Applications)
  Linux   -> "OpenSquawk Bridge/"      run the binary inside

PyInstaller can only build for the OS it runs on, so run this once on each
platform you want to ship. Everything else (installing tools, converting the
icon to .ico/.icns, bundling the web/ assets) is automatic.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
ICON_PNG = WEB / "assets" / "icon.png"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP_NAME = "OpenSquawk Bridge"
BUNDLE_ID = "de.opensquawk.bridge"


def run(cmd: list, **kw) -> None:
    print("\033[36m>\033[0m", " ".join(str(c) for c in cmd))
    subprocess.check_call(cmd, **kw)


def ensure_tools() -> None:
    """Install PyInstaller, Pillow and the app's own deps into this interpreter."""
    run([
        sys.executable, "-m", "pip", "install", "-q", "--upgrade",
        "pyinstaller>=6.0", "pillow>=10.0",
        "-r", str(ROOT / "requirements.txt"),
    ])


def make_icns() -> Path:
    """Build a macOS .icns from the PNG using the native iconutil/sips tools."""
    iconset = BUILD / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512):
        run(["sips", "-z", str(size), str(size), str(ICON_PNG),
             "--out", str(iconset / f"icon_{size}x{size}.png")])
        run(["sips", "-z", str(size * 2), str(size * 2), str(ICON_PNG),
             "--out", str(iconset / f"icon_{size}x{size}@2x.png")])
    icns = BUILD / "icon.icns"
    run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
    return icns


def make_ico() -> Path:
    """Build a Windows .ico from the PNG using Pillow."""
    from PIL import Image

    BUILD.mkdir(parents=True, exist_ok=True)
    ico = BUILD / "icon.ico"
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    Image.open(ICON_PNG).save(ico, sizes=sizes)
    return ico


def main() -> int:
    if not ICON_PNG.exists():
        print(f"error: missing icon at {ICON_PNG}", file=sys.stderr)
        return 1

    ensure_tools()
    BUILD.mkdir(parents=True, exist_ok=True)

    system = platform.system()
    data_sep = ";" if system == "Windows" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME,
        "--add-data", f"{WEB}{data_sep}web",
        # pynput (keyboard hotkey) and pygame (joystick/HOTAS) import their
        # platform backends dynamically, so PyInstaller's static analysis misses
        # them unless we pull in all submodules explicitly.
        "--collect-submodules", "pynput",
        "--collect-submodules", "pygame",
        # SimConnect bundles its own SimConnect.dll and loads it at runtime via
        # LoadLibrary (not an import), so PyInstaller won't see it. --collect-all
        # pulls the package's DLL/data in; without it the packaged build can't
        # connect to MSFS. (Harmless on macOS/Linux — the DLL just never loads.)
        "--collect-all", "SimConnect",
    ]

    if system == "Windows":
        cmd += ["--onefile", "--icon", str(make_ico())]
    elif system == "Darwin":
        cmd += ["--icon", str(make_icns()), "--osx-bundle-identifier", BUNDLE_ID]
    else:  # Linux
        cmd += ["--icon", str(ICON_PNG)]

    cmd.append(str(ROOT / "bridge_app.py"))
    run(cmd)

    print("\n\033[32m✓ Build complete.\033[0m Look in the 'dist' folder:")
    if system == "Windows":
        print(f"  dist\\{APP_NAME}.exe   — double-click to run")
    elif system == "Darwin":
        print(f"  dist/{APP_NAME}.app   — double-click to run")
        print("  (unsigned: first launch may need right-click → Open, see README)")
    else:
        print(f"  dist/{APP_NAME}/       — run the binary inside")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
