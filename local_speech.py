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

# Mirror the values of OpenSquawk/shared/utils/radioSpeech.ts's
# DEFAULT_AIRLINE_TELEPHONY.
_AIRLINE_TELEPHONY = [
    "Lufthansa",
    "Eurowings",
    "Turkish",
    "JetBlue",
    "Norwegian",
    "Swiss",
    "Speedbird",
    "Air France",
    "KLM",
    "American",
    "United",
    "Delta",
    "Ryanair",
    "Easy",
]

_STT_BIAS = " ".join(
    [
        "Air traffic control radio communication in ICAO English phraseology.",
        "Phonetic alphabet: Alfa Bravo Charlie Delta Echo Foxtrot Golf Hotel India "
        "Juliett Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango "
        "Uniform Victor Whiskey X-ray Yankee Zulu.",
        "Numbers: zero one two three four five six seven eight niner, also tree fife "
        "niner, decimal.",
        f"Airline callsigns: {', '.join(_AIRLINE_TELEPHONY)}.",
        "Common phrases: ready for pushback, request taxi, holding point, line up "
        "and wait, cleared for takeoff, contact tower, QNH, flight level, squawk, "
        "wilco, roger, affirm, negative, say again, runway, heading, descend, climb, "
        "maintain.",
    ]
)


def build_stt_prompt(expected: dict | None) -> str:
    """Build a Whisper bias prompt, preserving expected values at the end."""
    segments = [_STT_BIAS]
    if expected:
        phrase = (expected.get("phrase") or "").strip()
        if phrase:
            segments.append(f"Expected pilot transmission: {phrase}.")
        tokens = []
        seen = set()
        for token in expected.get("tokens") or []:
            token = str(token).strip()
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
        if tokens:
            segments.append(f"Expected values: {', '.join(tokens)}.")
    return " ".join(segments)


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
