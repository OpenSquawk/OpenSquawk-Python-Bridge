"""Local TTS/STT HTTP server for the OpenSquawk Bridge.

The server mirrors the cloud speech endpoints on loopback while keeping its
optional engines lazily loaded, so the Bridge itself starts without them.
"""
from __future__ import annotations

import json
import io
import socket
import threading
import wave
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

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"success": False, "error": "bad json"})
            return

        engines = self.server_ref.engines
        if not engines or not engines.ready:
            self._send_json(503, {"success": False, "error": "engines not ready"})
            return

        if self.path == "/api/atc/ptt":
            self._handle_ptt(body, engines)
            return
        if self.path == "/api/atc/say":
            self._handle_say(body, engines)
            return
        self._send_json(404, {"success": False, "error": "not found"})

    def _handle_ptt(self, body: dict, engines):
        import base64

        audio_b64 = body.get("audio") or ""
        if not audio_b64:
            self._send_json(400, {"success": False, "error": "audio required"})
            return
        try:
            wav = base64.b64decode(audio_b64, validate=True)
        except Exception:
            self._send_json(400, {"success": False, "error": "bad base64"})
            return
        prompt = build_stt_prompt(body.get("expected"))
        fmt = body.get("format") or "wav"
        text = engines.transcribe(wav, prompt, fmt).strip()
        if not text:
            self._send_json(400, {"success": False, "error": "no speech detected"})
            return
        self._send_json(200, {"success": True, "transcription": text})

    def _handle_say(self, body: dict, engines):
        import base64

        text = (body.get("text") or "").strip()
        if not text:
            self._send_json(400, {"success": False, "error": "text required"})
            return
        voice = (body.get("voice") or "").strip() or None
        speed = float(body.get("speed") or 1.0)
        speed = max(0.5, min(2.0, speed))
        wav_bytes, mime, ext = engines.synthesize(text, voice, speed)
        self._send_json(
            200,
            {
                "success": True,
                "text": text,
                "audio": {
                    "mime": mime,
                    "base64": base64.b64encode(wav_bytes).decode(),
                    "size": len(wav_bytes),
                    "ext": ext,
                },
                "meta": {"ttsProvider": "piper-local"},
            },
        )


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


class SpeechEngines:
    """Lazily load faster-whisper and Piper behind the server's stable API."""

    def __init__(
        self,
        model_dir: str,
        model_name: str = "base.en",
        piper_voice_path: str | None = None,
    ):
        self.model_dir = model_dir
        self.model_name = model_name
        self.piper_voice_path = piper_voice_path
        self._whisper = None
        self._piper = None
        self.ready = False

    def _import_whisper(self):
        from faster_whisper import WhisperModel

        return WhisperModel

    def _import_piper(self):
        from piper import PiperVoice

        return PiperVoice

    def load(self):
        try:
            whisper_model = self._import_whisper()
        except ImportError as error:
            raise RuntimeError(f"faster-whisper not installed: {error}") from error
        try:
            piper_voice = self._import_piper()
        except ImportError as error:
            raise RuntimeError(f"piper-tts not installed: {error}") from error

        self._whisper = whisper_model(
            self.model_name,
            download_root=self.model_dir,
            device="auto",
            compute_type="int8",
        )
        self._piper = piper_voice.load(self.piper_voice_path)
        self.ready = True

    def transcribe(self, wav_bytes: bytes, prompt: str, fmt: str = "wav") -> str:
        del fmt  # faster-whisper detects the container from the byte stream.
        segments, _ = self._whisper.transcribe(
            io.BytesIO(wav_bytes),
            language="en",
            temperature=0,
            initial_prompt=prompt,
        )
        return " ".join(segment.text for segment in segments)

    def synthesize(self, text: str, voice: str | None, speed: float):
        del voice  # The MVP ships one selected local Piper voice.
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            # piper-tts releases expose speed through SynthesisConfig rather
            # than as a synthesize() keyword argument.
            try:
                from piper.config import SynthesisConfig

                self._piper.synthesize(
                    text,
                    wav_file,
                    syn_config=SynthesisConfig(length_scale=1.0 / max(0.1, speed)),
                )
            except ImportError:
                # Compatibility with older Piper releases.
                self._piper.synthesize(
                    text,
                    wav_file,
                    length_scale=1.0 / max(0.1, speed),
                )
        return buffer.getvalue(), "audio/wav", "wav"
