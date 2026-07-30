#!/usr/bin/env python3
"""OpenSquawk Bridge — cross-platform updater + env builder + app launcher.

Run by a per-OS launcher via `uv run --python 3.12 --no-project bootstrap.py`.
Uses only the Python standard library so it works on a bare managed Python.
Supports macOS, Linux and Windows.

Responsibilities, in order:
  1. Resolve the latest source ref from GitHub (Releases → fallback: main).
  2. If newer (or no source yet), download the tarball and swap `src/` in place.
  3. If the environment is missing or requirements changed, (re)build the venv.
  4. On Linux, install a .desktop entry so the app shows up in the app menu.
  5. Launch bridge_app.py from `src/`.

If the network is unavailable but a previous install exists, it launches that
install unchanged — the app must still start offline after the first setup.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "OpenSquawk Bridge"
REPO = "OpenSquawk/OpenSquawk-Bridge"
BRANCH = "main"          # fallback channel when no release is published
PYTHON = "3.12"          # managed Python used for the app's venv

SYSTEM = platform.system()  # "Darwin", "Linux", "Windows"
IS_WINDOWS = SYSTEM == "Windows"

USER_AGENT = "OpenSquawk-Bridge-Launcher"
# Kept short on purpose: every second here is a second in which the user has
# clicked the icon and sees nothing at all. A failed check just means "launch
# what is installed", so erring towards giving up early is the friendly choice.
API_TIMEOUT = 8
DL_TIMEOUT = 300
DRY_RUN = os.environ.get("OPENSQUAWK_BOOTSTRAP_DRYRUN") == "1"
# The Linux launcher passes its own absolute path so we can register a .desktop
# entry that re-invokes it (keeps auto-update on every menu launch).
LAUNCHER_PATH = os.environ.get("OPENSQUAWK_LAUNCHER")


def support_dir() -> Path:
    if SYSTEM == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


SUPPORT = support_dir()
BIN = SUPPORT / "bin"
UV = BIN / ("uv.exe" if IS_WINDOWS else "uv")
SRC = SUPPORT / "src"
VENV = SUPPORT / "venv"
# python.exe for pip/install work; pythonw.exe launches the GUI without a console.
if IS_WINDOWS:
    VENV_PY = VENV / "Scripts" / "python.exe"
    VENV_PYW = VENV / "Scripts" / "pythonw.exe"
else:
    VENV_PY = VENV / "bin" / "python"
    VENV_PYW = VENV_PY
STATE_FILE = SUPPORT / "state.json"
VERSION_FILE_NAME = ".opensquawk-version.json"

# Single-instance handshake with the running app. This mirrors single_instance.py
# in the app source — which we cannot import, because this check has to happen
# before the update step (the slow part we want to skip) and possibly before any
# source is downloaded at all. Keep the two file names in sync.
APP_CONFIG_DIR = Path.home() / ".opensquawk-bridge"
LOCK_FILE = APP_CONFIG_DIR / "app.lock"
FOCUS_FILE = APP_CONFIG_DIR / "focus.request"


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def notify(msg: str) -> None:
    try:
        if SYSTEM == "Darwin":
            subprocess.run(
                ["/usr/bin/osascript", "-e",
                 f'display notification "{msg}" with title "{APP_NAME}"'],
                check=False, capture_output=True)
        elif SYSTEM == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", APP_NAME, msg],
                           check=False, capture_output=True)
        # Windows: no toast (keep it dependency-free); the log covers diagnostics.
    except Exception:
        pass


def app_is_running() -> bool:
    """Whether a Bridge already holds the single-instance lock.

    The running app keeps the lock open for its lifetime, so the OS drops it on
    exit and a crash cannot leave a stale "already running" behind.
    """
    if not LOCK_FILE.exists():
        return False
    fd = None
    try:
        fd = os.open(LOCK_FILE, os.O_RDWR)
        if IS_WINDOWS:
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        return False  # we could take it, so nobody else has it
    except OSError:
        return True
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def focus_running_app() -> bool:
    """Ask the running app to show its window; True once it reacted."""
    try:
        FOCUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        FOCUS_FILE.write_text(f"{time.time()}\n", encoding="utf-8")
    except OSError:
        return False
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not FOCUS_FILE.exists():  # picked up by the app
            return True
        time.sleep(0.1)
    return False


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _get_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _commit_info(ref: str) -> dict[str, str | None]:
    """Resolve a GitHub ref to the revision and its commit timestamp."""
    commit = _get_json(f"https://api.github.com/repos/{REPO}/commits/{ref}")
    details = commit.get("commit") if commit else {}
    committer = details.get("committer") if isinstance(details, dict) else {}
    return {
        "commit": commit.get("sha") if commit else None,
        "committed_at": committer.get("date") if isinstance(committer, dict) else None,
    }


def resolve_latest() -> tuple[str, str, dict[str, str | None]] | None:
    """Return source ref, tarball URL and revision metadata, or None if offline.

    Prefers the latest published GitHub Release; falls back to the tip of the
    default branch so the launcher works before any release exists.
    """
    try:
        rel = _get_json(f"https://api.github.com/repos/{REPO}/releases/latest")
        if rel and rel.get("tag_name"):
            tag = rel["tag_name"]
            return (
                f"release:{tag}",
                rel.get("tarball_url") or f"https://api.github.com/repos/{REPO}/tarball/{tag}",
                _commit_info(tag),
            )
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 404 just means "no releases yet"
            log(f"release check failed: {e}")
    except Exception as e:
        log(f"release check failed: {e}")
        return None  # network problem — signal "no update", keep existing

    try:
        revision = _commit_info(BRANCH)
        sha = revision.get("commit")
        if sha:
            return f"branch:{sha}", f"https://api.github.com/repos/{REPO}/tarball/{sha}", revision
    except Exception as e:
        log(f"branch check failed: {e}")
    return None


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=DL_TIMEOUT) as resp, \
            open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def extract_source(tarball: Path, into: Path) -> None:
    """Extract a GitHub tarball (single top-level dir) into `into`."""
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    into_resolved = into.resolve()
    with tarfile.open(tarball, "r:gz") as tar:
        members = tar.getmembers()
        top = members[0].name.split("/", 1)[0] if members else ""
        for m in members:
            rel = m.name[len(top):].lstrip("/")
            if not rel:
                continue
            target = (into / rel).resolve()
            if not str(target).startswith(str(into_resolved)):
                raise RuntimeError(f"unsafe path in tarball: {m.name}")
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as fsrc, open(target, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
                os.chmod(target, m.mode or 0o644)


def update_source() -> None:
    """Fetch the latest source if newer than what's installed."""
    latest = resolve_latest()
    state = load_state()
    installed = state.get("ref")

    if latest is None:
        if SRC.exists():
            log("update check unavailable (offline?) — using installed source")
            return
        notify("No internet connection. Cannot install OpenSquawk Bridge.")
        raise SystemExit("cannot install: no source and no network")

    ref, tarball_url, revision = latest
    if ref == installed and SRC.exists():
        log(f"up to date ({ref})")
        return

    notify("Updating OpenSquawk Bridge…")
    log(f"updating {installed} -> {ref}")
    with tempfile.TemporaryDirectory() as tmp:
        tarball = Path(tmp) / "src.tar.gz"
        download(tarball_url, tarball)
        staging = SUPPORT / "src.new"
        extract_source(tarball, staging)
        (staging / VERSION_FILE_NAME).write_text(json.dumps({
            "ref": ref,
            "commit": revision.get("commit"),
            "committed_at": revision.get("committed_at"),
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2) + "\n", encoding="utf-8")
        old = SUPPORT / "src.old"
        if old.exists():
            shutil.rmtree(old)
        if SRC.exists():
            SRC.rename(old)
        staging.rename(SRC)
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)

    state["ref"] = ref
    save_state(state)
    log(f"source now at {ref}")


def extra_requirements() -> list[str]:
    """OS-specific packages not in requirements.txt.

    Linux: pywebview has no pip-installable GUI backend by default (GTK comes
    from the distro). We install the Qt backend so the app is fully
    self-contained and needs no `apt install`.
    """
    if SYSTEM == "Linux":
        return ["qtpy", "PySide6"]
    return []


def _requirements_hash() -> str:
    req = SRC / "requirements.txt"
    data = req.read_bytes() if req.exists() else b""
    h = hashlib.sha256()
    h.update(data)
    h.update(("\n".join(extra_requirements())).encode())
    h.update(SYSTEM.encode())
    return h.hexdigest()


def ensure_env() -> None:
    """Create/refresh the venv when it's missing or requirements changed."""
    state = load_state()
    want = _requirements_hash()
    have = state.get("requirements_hash")

    if VENV_PY.exists() and want == have:
        log("environment up to date")
        return

    notify("Installing dependencies…")
    log("building environment")
    subprocess.run([str(UV), "venv", str(VENV), "--python", PYTHON], check=True)
    reqs: list[str] = []
    req = SRC / "requirements.txt"
    if req.exists():
        reqs += ["-r", str(req)]
    reqs += extra_requirements()
    if reqs:
        subprocess.run(
            [str(UV), "pip", "install", "--python", str(VENV_PY), *reqs],
            check=True)
    state["requirements_hash"] = want
    save_state(state)
    log("environment ready")


def install_desktop_entry() -> None:
    """Linux only: register a .desktop launcher so the app is menu-visible."""
    if SYSTEM != "Linux" or not LAUNCHER_PATH:
        return
    try:
        apps = Path(os.environ.get("XDG_DATA_HOME") or
                    (Path.home() / ".local" / "share")) / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        icon_src = SRC / "web" / "assets" / "icon.png"
        icon_line = ""
        if icon_src.exists():
            icon_dst = SUPPORT / "icon.png"
            shutil.copy2(icon_src, icon_dst)
            icon_line = f"Icon={icon_dst}\n"
        desktop = apps / "opensquawk-bridge.desktop"
        desktop.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_NAME}\n"
            "Comment=Connect your flight simulator to OpenSquawk\n"
            f"Exec=\"{LAUNCHER_PATH}\"\n"
            f"{icon_line}"
            "Terminal=false\n"
            "Categories=Game;Utility;\n"
        )
        log(f"desktop entry installed at {desktop}")
    except Exception as e:
        log(f"desktop entry skipped: {e}")


def app_named_interpreter() -> Path:
    """macOS only: an interpreter whose file name is the app's name.

    The app runs from our venv, not from inside the .app bundle, so macOS has no
    bundle to read a name from and falls back to the executable's file name —
    the dock, the menu bar and Force Quit all say "python3.12". Nothing inside
    the app can change that, but the name of the file we exec is ours to pick.

    The symlink lives next to the real interpreter so the venv still resolves
    (Python finds pyvenv.cfg beside the executable). Falls back to the plain
    interpreter if the link cannot be created — a wrong name beats no app.
    """
    if SYSTEM != "Darwin":
        return VENV_PY
    link = VENV_PY.parent / APP_NAME
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(VENV_PY.name)
        return link
    except OSError as e:
        log(f"named interpreter skipped: {e}")
        return VENV_PY


def launch_app() -> None:
    entry = SRC / "bridge_app.py"
    if not entry.exists():
        notify("Install is incomplete. Please reinstall OpenSquawk Bridge.")
        raise SystemExit(f"missing entrypoint: {entry}")
    log("launching app")
    if DRY_RUN:
        log("DRY RUN — not launching the GUI")
        return
    os.chdir(SRC)
    python = str(VENV_PYW if VENV_PYW.exists() else VENV_PY)
    if SYSTEM == "Darwin":
        python = str(app_named_interpreter())
    if IS_WINDOWS:
        # execv on Windows would keep the parent console attached; spawn the GUI
        # detached with pythonw and let the launcher exit cleanly.
        DETACHED = 0x00000008  # DETACHED_PROCESS
        subprocess.Popen([python, str(entry)], cwd=str(SRC),
                         creationflags=DETACHED, close_fds=True)
        return
    os.execv(python, [python, str(entry)])


def main() -> int:
    SUPPORT.mkdir(parents=True, exist_ok=True)

    # Clicking the icon while the Bridge runs used to mean: a full update check
    # with nothing on screen, then a second window. Hand over to the running app
    # instead — before the network work, which is what made it slow.
    if app_is_running():
        log("app already running - focusing it")
        if not focus_running_app():
            notify(f"{APP_NAME} is already running.")
        return 0

    if not UV.exists():
        notify("Setup incomplete: uv is missing.")
        return 1
    # Say something *now*: the update check below can take seconds, and until
    # the window opens this notification is the only sign that the click worked.
    notify(f"Starting {APP_NAME}…")
    try:
        update_source()
        ensure_env()
        install_desktop_entry()
    except subprocess.CalledProcessError as e:
        log(f"setup step failed: {e}")
        if not (VENV_PY.exists() and (SRC / "bridge_app.py").exists()):
            notify("Setup failed. Please try again.")
            return 1
        log("continuing with the previously installed version")
    launch_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
