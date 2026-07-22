import json
import urllib.request

from local_speech import LocalSpeechServer, pick_port


def test_pick_port_returns_first_free(monkeypatch):
    # 8765 taken, 8766 free
    import socket

    real_bind = socket.socket.bind
    taken = {8765}

    def fake_bind(self, addr):
        if addr[1] in taken:
            raise OSError("in use")
        return real_bind(self, addr)

    monkeypatch.setattr(socket.socket, "bind", fake_bind)
    assert pick_port(start=8765, end=8770) == 8766


def test_health_reports_not_ready_before_engines_load():
    server = LocalSpeechServer(engines=None)  # engines not yet ready
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/health"
        with urllib.request.urlopen(url, timeout=2) as resp:
            body = json.loads(resp.read())
        assert resp.status == 200
        assert body["ok"] is True
        assert body["ready"] is False
    finally:
        server.stop()
