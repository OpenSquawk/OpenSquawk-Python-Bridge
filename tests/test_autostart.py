import plistlib
import sys
from pathlib import Path

import bridge_app


def _bare_api():
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    return api


def test_linux_autostart_desktop_file(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_app.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(bridge_app, "_launch_command", lambda: ["/opt/OpenSquawk Bridge"])

    api = _bare_api()
    res = api.set_autostart(True)

    desktop = tmp_path / ".config" / "autostart" / "opensquawk-bridge.desktop"
    assert res == {"ok": True, "enabled": True}
    assert desktop.exists()
    assert "Exec='/opt/OpenSquawk Bridge'" in desktop.read_text(encoding="utf-8")

    res = api.set_autostart(False)
    assert res == {"ok": True, "enabled": False}
    assert not desktop.exists()


def test_macos_autostart_launch_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_app.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(bridge_app, "_launch_command", lambda: [sys.executable, "/app/bridge_app.py"])

    api = _bare_api()
    res = api.set_autostart(True)

    plist = tmp_path / "Library" / "LaunchAgents" / "de.opensquawk.bridge.plist"
    assert res == {"ok": True, "enabled": True}
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "de.opensquawk.bridge"
    assert data["ProgramArguments"] == [sys.executable, "/app/bridge_app.py"]
    assert data["RunAtLoad"] is True
