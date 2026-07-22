#!/usr/bin/env python3
"""Assemble the macOS thin launcher — "OpenSquawk Bridge.app" — and a .dmg.

    python installer/build_launcher.py

The launcher carries no Python and no app code; it downloads the latest source
from GitHub at runtime (see installer/mac/bootstrap.py). So this only needs to
run again when the *launcher itself* changes — not for every app update.

Output (in ./dist):
  OpenSquawk Bridge.app        the launcher bundle
  OpenSquawk-Bridge-macOS.dmg  the single file to publish / link on the website

This must run on macOS (uses sips / iconutil / hdiutil). No signing is done.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC = ROOT / "installer" / "mac"
ICON_PNG = ROOT / "web" / "assets" / "icon.png"
BUILD = ROOT / "build"
DIST = ROOT / "dist"

APP_NAME = "OpenSquawk Bridge"
LAUNCHER_VERSION = "1.0.0"  # bump only when the launcher/bootstrap changes


def run(cmd: list, **kw) -> None:
    print("\033[36m>\033[0m", " ".join(str(c) for c in cmd))
    subprocess.check_call(cmd, **kw)


def make_icns() -> Path:
    iconset = BUILD / "launcher.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512):
        run(["sips", "-z", str(size), str(size), str(ICON_PNG),
             "--out", str(iconset / f"icon_{size}x{size}.png")])
        run(["sips", "-z", str(size * 2), str(size * 2), str(ICON_PNG),
             "--out", str(iconset / f"icon_{size}x{size}@2x.png")])
    icns = BUILD / "launcher.icns"
    run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
    return icns


def build_app(icns: Path) -> Path:
    app = DIST / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    # Bundle executable (bash launcher).
    launcher = macos / "launcher"
    shutil.copy2(MAC / "launcher.sh", launcher)
    launcher.chmod(0o755)

    # Runtime scripts + icon.
    shutil.copy2(MAC / "bootstrap.py", resources / "bootstrap.py")
    shutil.copy2(icns, resources / "icon.icns")

    # Info.plist from template.
    plist = (MAC / "Info.plist.in").read_text().replace(
        "@LAUNCHER_VERSION@", LAUNCHER_VERSION)
    (contents / "Info.plist").write_text(plist)

    return app


def build_dmg(app: Path) -> Path:
    staging = BUILD / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name)
    # Drag-to-install target.
    os.symlink("/Applications", staging / "Applications")

    dmg = DIST / "OpenSquawk-Bridge-macOS.dmg"
    if dmg.exists():
        dmg.unlink()
    run(["hdiutil", "create",
         "-volname", APP_NAME,
         "-srcfolder", str(staging),
         "-ov", "-format", "UDZO",
         str(dmg)])
    return dmg


def main() -> int:
    if platform.system() != "Darwin":
        print("error: the macOS launcher can only be built on macOS",
              file=sys.stderr)
        return 1
    if not ICON_PNG.exists():
        print(f"error: missing icon at {ICON_PNG}", file=sys.stderr)
        return 1

    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    icns = make_icns()
    app = build_app(icns)
    dmg = build_dmg(app)

    print("\n\033[32m✓ Launcher built.\033[0m Look in the 'dist' folder:")
    print(f"  {dmg.relative_to(ROOT)}   — publish this / link it on the website")
    print(f"  {app.relative_to(ROOT)}   — the launcher bundle itself")
    print("\n  Unsigned: users open it the first time with right-click → Open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
