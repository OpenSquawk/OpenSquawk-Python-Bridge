"""Single-instance guard: second start hands over instead of opening a window."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import single_instance

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config_dir(tmp_path):
    """A private config dir, so tests never touch the user's real lock."""
    yield tmp_path / "config"
    # The lock is meant to live as long as the process; drop it between tests so
    # each one starts out as a fresh "app".
    if single_instance._held_fd is not None:
        os.close(single_instance._held_fd)
        single_instance._held_fd = None


def _holder(config_dir: Path, seconds: float = 5.0) -> subprocess.Popen:
    """Start a process that takes the lock and keeps it, like a running app."""
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT)!r})
        import single_instance
        from pathlib import Path
        assert single_instance.acquire(Path({str(config_dir)!r}))
        print("locked", flush=True)
        time.sleep({seconds})
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "locked"
    return proc


def test_acquire_succeeds_when_nothing_runs(config_dir):
    assert single_instance.acquire(config_dir) is True
    assert single_instance.lock_path(config_dir).exists()


def test_second_process_cannot_acquire(config_dir):
    holder = _holder(config_dir)
    try:
        assert single_instance.acquire(config_dir) is False
    finally:
        holder.kill()
        holder.wait()


def test_lock_is_free_again_after_the_holder_dies(config_dir):
    """A crash must not lock the user out of their own app."""
    holder = _holder(config_dir)
    holder.kill()
    holder.wait()
    assert single_instance.acquire(config_dir) is True


def test_focus_request_is_seen_by_the_running_app(config_dir):
    seen = []
    single_instance.watch_focus_requests(lambda: seen.append(True), config_dir)

    assert single_instance.request_focus(config_dir, timeout=5.0) is True
    deadline = time.time() + 5.0
    while not seen and time.time() < deadline:
        time.sleep(0.05)
    assert seen == [True]
    assert not single_instance.focus_path(config_dir).exists()


def test_focus_request_reports_no_listener(config_dir):
    """Nobody watching: the caller learns it and can notify the user itself."""
    assert single_instance.request_focus(config_dir, timeout=0.3) is False


def test_stale_request_does_not_raise_the_window_on_startup(config_dir):
    """A request written while no app ran must not fire at the next start."""
    focus = single_instance.focus_path(config_dir)
    focus.parent.mkdir(parents=True, exist_ok=True)
    focus.write_text("stale\n")

    seen = []
    single_instance.watch_focus_requests(lambda: seen.append(True), config_dir)
    time.sleep(single_instance.FOCUS_POLL_SECONDS * 3)
    assert seen == []
