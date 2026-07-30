#!/usr/bin/env python3
"""Assemble the OpenSquawk Bridge thin launchers (macOS + Linux).

    python installer/build_launcher.py            # everything this host can build
    python installer/build_launcher.py --linux    # only the Linux .sh
    python installer/build_launcher.py --mac       # only the macOS .app + .dmg

The launchers carry no Python and no app code; they download the latest source
from GitHub at runtime (see installer/bootstrap.py). So this only needs to run
again when a *launcher itself* changes — not for every app update.

Output (in ./dist):
  OpenSquawk Bridge.app          macOS launcher bundle          (macOS host only)
  OpenSquawk-Bridge-macOS.dmg    macOS single file to publish   (macOS host only)
  OpenSquawk-Bridge-linux.sh     Linux single file to publish   (any host)
  OpenSquawk-Bridge-windows.cmd  Windows single file to publish (any host)

The macOS artifacts need sips / iconutil / hdiutil, so they build on macOS only.
The Linux and Windows artifacts are plain text assembly (bootstrap.py is
embedded) and build on any host. Nothing is signed.
"""

from __future__ import annotations

import argparse
import base64
import os
import platform
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "installer"
MAC = INSTALLER / "mac"
LINUX = INSTALLER / "linux"
WINDOWS = INSTALLER / "windows"
BOOTSTRAP = INSTALLER / "bootstrap.py"
ICON_PNG = ROOT / "web" / "assets" / "icon.png"
BUILD = ROOT / "build"
DIST = ROOT / "dist"

APP_NAME = "OpenSquawk Bridge"
LAUNCHER_VERSION = "1.0.2"  # bump only when the launcher/bootstrap changes


def run(cmd: list, **kw) -> None:
    print("\033[36m>\033[0m", " ".join(str(c) for c in cmd))
    subprocess.check_call(cmd, **kw)


# --------------------------------------------------------------------------- #
# Linux (any host)
# --------------------------------------------------------------------------- #

def build_linux() -> Path:
    template = (LINUX / "launcher-template.sh").read_text()
    bootstrap = BOOTSTRAP.read_text()
    if "OPENSQUAWK_BOOTSTRAP_EOF" in bootstrap:
        raise RuntimeError("bootstrap.py collides with the heredoc marker")
    script = template.replace("@BOOTSTRAP@", bootstrap)

    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / "OpenSquawk-Bridge-linux.sh"
    out.write_text(script)
    out.chmod(out.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return out


# --------------------------------------------------------------------------- #
# Windows (any host)
# --------------------------------------------------------------------------- #

def build_windows() -> Path:
    template = (WINDOWS / "launcher-template.cmd").read_text()
    b64 = base64.b64encode(BOOTSTRAP.read_bytes()).decode("ascii")
    block = "\n".join(textwrap.wrap(b64, 120))
    script = template.replace("@BASE64@", block)

    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / "OpenSquawk-Bridge-windows.cmd"
    # CRLF so the batch file is well-formed on Windows.
    out.write_bytes(script.replace("\n", "\r\n").encode("utf-8"))
    return out


# --------------------------------------------------------------------------- #
# macOS (Darwin host only)
# --------------------------------------------------------------------------- #

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


def build_mac_app(icns: Path) -> Path:
    app = DIST / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    launcher = macos / "launcher"
    shutil.copy2(MAC / "launcher.sh", launcher)
    launcher.chmod(0o755)

    shutil.copy2(BOOTSTRAP, resources / "bootstrap.py")
    shutil.copy2(icns, resources / "icon.icns")

    plist = (MAC / "Info.plist.in").read_text().replace(
        "@LAUNCHER_VERSION@", LAUNCHER_VERSION)
    (contents / "Info.plist").write_text(plist)
    return app


def build_mac_dmg(app: Path) -> Path:
    staging = BUILD / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name)
    os.symlink("/Applications", staging / "Applications")

    dmg = DIST / "OpenSquawk-Bridge-macOS.dmg"
    if dmg.exists():
        dmg.unlink()
    run(["hdiutil", "create", "-volname", APP_NAME,
         "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(dmg)])
    return dmg


def build_mac() -> list[Path]:
    if platform.system() != "Darwin":
        raise SystemExit("error: the macOS launcher can only be built on macOS")
    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    icns = make_icns()
    app = build_mac_app(icns)
    dmg = build_mac_dmg(app)
    return [dmg, app]


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", action="store_true", help="build only macOS")
    ap.add_argument("--linux", action="store_true", help="build only Linux")
    ap.add_argument("--windows", action="store_true", help="build only Windows")
    args = ap.parse_args()
    only = args.mac or args.linux or args.windows
    want_mac = args.mac or not only
    want_linux = args.linux or not only
    want_windows = args.windows or not only

    if not ICON_PNG.exists():
        print(f"error: missing icon at {ICON_PNG}", file=sys.stderr)
        return 1

    outputs: list[Path] = []
    if want_linux:
        outputs.append(build_linux())
    if want_windows:
        outputs.append(build_windows())
    if want_mac:
        if platform.system() == "Darwin":
            outputs += build_mac()
        elif args.mac:
            print("error: the macOS launcher can only be built on macOS",
                  file=sys.stderr)
            return 1
        else:
            print("• skipping macOS artifacts (not on macOS)")

    print("\n\033[32m✓ Done.\033[0m Built:")
    for p in outputs:
        print(f"  {p.relative_to(ROOT)}")
    print("\n  Unsigned. macOS: right-click → Open the first time.")
    print("  Linux: chmod +x the .sh and run it.")
    print("  Windows: double-click the .cmd (SmartScreen → More info → Run anyway).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
