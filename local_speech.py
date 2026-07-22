"""Local TTS/STT HTTP server for the OpenSquawk Bridge.

The server mirrors the cloud speech endpoints on loopback while keeping its
optional engines lazily loaded, so the Bridge itself starts without them.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


DEFAULT_PORT_START = 8765
DEFAULT_PORT_END = 8770


def pick_port(start: int = DEFAULT_PORT_START, end: int = DEFAULT_PORT_END) -> int:
    """Return the first bindable loopback port in the inclusive range."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError(f"no free port in {start}..{end}")


class _Handler(BaseHTTPRequestHandler):
    """Request handler configured with its owning LocalSpeechServer."""

    server_ref: "LocalSpeechServer" = None  # type: ignore[assignment]

    def log_message(self, *args):
        """Suppress the default HTTP access log."""

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", self.server_ref.allow_origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            engines = self.server_ref.engines
            self._send_json(
                200,
                {
                    "ok": True,
                    "ready": bool(engines and engines.ready),
                    "engines": ["piper", "faster-whisper"],
                    "model": self.server_ref.model_name,
                },
            )
            return
        self._send_json(404, {"ok": False, "error": "not found"})


class LocalSpeechServer:
    """Loopback-only local speech HTTP server, running on a daemon thread."""

    def __init__(
        self,
        engines=None,
        allow_origin: str = "*",
        model_name: str = "base.en",
    ):
        self.engines = engines
        self.allow_origin = allow_origin
        self.model_name = model_name
        self.port = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        self.port = pick_port()
        _Handler.server_ref = self
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
