"""The launcher's own single-instance probe.

installer/bootstrap.py duplicates the lock handshake because it runs before the
app source exists. These tests pin the duplication down: same file names, same
answer as the real guard.
"""

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import bridge_app
import single_instance

ROOT = Path(__file__).resolve().parent.parent


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location(
        "osq_bootstrap", ROOT / "installer" / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bootstrap():
    return _load_bootstrap()


def test_launcher_and_app_agree_on_the_lock_location(bootstrap):
    """If these drift, the launcher silently starts a second app again."""
    assert bootstrap.APP_CONFIG_DIR == bridge_app.CONFIG_DIR
    assert bootstrap.LOCK_FILE.name == single_instance.LOCK_NAME
    assert bootstrap.FOCUS_FILE.name == single_instance.FOCUS_NAME


def test_no_lock_file_means_not_running(bootstrap, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "LOCK_FILE", tmp_path / "app.lock")
    assert bootstrap.app_is_running() is False


def test_named_interpreter_is_a_sibling_symlink(bootstrap, tmp_path, monkeypatch):
    """macOS names the dock entry after this file, so it must be ours."""
    real = tmp_path / "bin" / "python"
    real.parent.mkdir(parents=True)
    real.touch()
    monkeypatch.setattr(bootstrap, "SYSTEM", "Darwin")
    monkeypatch.setattr(bootstrap, "VENV_PY", real)

    link = bootstrap.app_named_interpreter()
    assert link.name == bootstrap.APP_NAME
    # Beside the real interpreter, or the venv no longer resolves.
    assert link.parent == real.parent
    assert link.resolve() == real.resolve()

    # A relaunch must not trip over the link from the previous one.
    assert bootstrap.app_named_interpreter() == link


def test_named_interpreter_is_macos_only(bootstrap, tmp_path, monkeypatch):
    real = tmp_path / "bin" / "python"
    monkeypatch.setattr(bootstrap, "SYSTEM", "Windows")
    monkeypatch.setattr(bootstrap, "VENV_PY", real)
    assert bootstrap.app_named_interpreter() == real


def test_detects_a_running_app(bootstrap, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "LOCK_FILE", tmp_path / "app.lock")
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT)!r})
        import single_instance
        from pathlib import Path
        assert single_instance.acquire(Path({str(tmp_path)!r}))
        print("locked", flush=True)
        time.sleep(10)
    """)
    holder = subprocess.Popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert bootstrap.app_is_running() is True
    finally:
        holder.kill()
        holder.wait()

    # Lock released with the process: the next click starts the app normally.
    assert bootstrap.app_is_running() is False
