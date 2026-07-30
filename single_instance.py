"""Single-instance guard — one running Bridge per user.

The Bridge is easy to start twice: every click on the app icon runs the thin
launcher, which checks GitHub for updates before anything at all becomes
visible. Starting a second copy that way is both slow and pointless — for the
better part of a minute nothing happens, and then a duplicate window appears
next to the one that was already open.

So the running app holds an exclusive lock on `app.lock` for its whole
lifetime, and every start asks first: if the lock is taken, drop a
`focus.request` file next to it and exit. The running app watches for that file
and raises its window, so clicking the icon focuses the app you already have.

`installer/bootstrap.py` carries its own copy of the probe — it has to run
before the update check (that slow step is the whole point of skipping) and
before this file has even been downloaded. Keep the two file names in sync.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

# Mirrors bridge_app.CONFIG_DIR; callers pass their own to keep the two in sync.
DEFAULT_DIR = Path.home() / ".opensquawk-bridge"
LOCK_NAME = "app.lock"
FOCUS_NAME = "focus.request"

FOCUS_POLL_SECONDS = 0.5

# Held for the process lifetime once acquired. The OS drops the lock when the
# process exits, so a crashed app never leaves a stale lock behind — which is
# exactly why this is a real file lock and not a PID file.
_held_fd: int | None = None


def lock_path(config_dir: Path | None = None) -> Path:
    return (config_dir or DEFAULT_DIR) / LOCK_NAME


def focus_path(config_dir: Path | None = None) -> Path:
    return (config_dir or DEFAULT_DIR) / FOCUS_NAME


def _try_lock(fd: int) -> bool:
    """Take an exclusive, non-blocking lock on an open file descriptor."""
    try:
        if sys.platform.startswith("win"):
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def acquire(config_dir: Path | None = None) -> bool:
    """Claim the single-instance lock. False means another Bridge is running.

    The lock is held until the process exits; there is nothing to release.
    """
    global _held_fd
    if _held_fd is not None:
        return True

    path = lock_path(config_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        # Can't even open the lock file — never block the app over that.
        return True

    if not _try_lock(fd):
        os.close(fd)
        return False

    try:
        os.truncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:
        pass
    _held_fd = fd
    return True


def request_focus(config_dir: Path | None = None, timeout: float = 2.0) -> bool:
    """Ask the running instance to come to the front.

    Returns True once it picked the request up, False if it never did (an app
    that is starting up, or wedged) — the caller can then say something itself
    instead of leaving the user with no feedback at all.
    """
    path = focus_path(config_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{time.time()}\n", encoding="utf-8")
    except OSError:
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not path.exists():  # consumed by the running app
            return True
        time.sleep(0.1)
    return False


def watch_focus_requests(
    on_focus: Callable[[], None], config_dir: Path | None = None
) -> threading.Thread:
    """Run `on_focus` whenever another start asks us to show ourselves."""
    path = focus_path(config_dir)

    # A request left over from a previous run must not raise the window now.
    try:
        path.unlink()
    except OSError:
        pass

    def loop() -> None:
        while True:
            time.sleep(FOCUS_POLL_SECONDS)
            try:
                if not path.exists():
                    continue
                path.unlink()
            except OSError:
                continue
            try:
                on_focus()
            except Exception as exc:  # never let the watcher die
                print(f"[instance] could not raise the window: {exc}")

    thread = threading.Thread(target=loop, name="focus-watcher", daemon=True)
    thread.start()
    return thread
