# Local TTS/STT via the Bridge — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run TTS (Piper) and STT (faster-whisper) locally inside the Bridge, exposed on `http://127.0.0.1`, so the `/live-atc` radio page can use them directly with automatic fallback to the cloud endpoints.

**Architecture:** The Bridge (`osq-gui`, pywebview) gains a local `ThreadingHTTPServer` that mirrors the cloud endpoints `POST /api/atc/say` and `POST /api/atc/ptt` plus `GET /health`. The Nuxt frontend (`OpenSquawk`) health-scans `127.0.0.1:8765..8770`, prefers local when ready, and falls back to `api.post('/api/atc/…')` on any failure. Heavy deps and models are installed/downloaded on-demand the first time the user ticks the checkbox — never shipped in Git or `requirements.txt`.

**Tech Stack:** Python `http.server` + `threading`, `faster-whisper`, `piper-tts`; Vue 3 composables + Vitest; pytest.

**Design doc:** `docs/plans/2026-07-22-local-speech-bridge-design.md`

**Repo roots:**
- Bridge: `/Users/domi/html/osq-gui` (tasks 1–8)
- Frontend: `/Users/domi/html/OpenSquawk` (tasks 9–11)

**Note on dev/build split:** Development is on macOS; real users run on Windows. Keep engine imports lazy and platform-neutral so the Bridge still starts (and tests still run) on a machine without the engines installed.

---

## Part A — Bridge (`osq-gui`)

### Task 1: Port selection + server skeleton with `/health`

**Files:**
- Create: `local_speech.py`
- Test: `tests/test_local_speech_server.py`

**Step 1: Write the failing test**

```python
# tests/test_local_speech_server.py
import json
import urllib.request
import pytest
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
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_local_speech_server.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'local_speech'`).

**Step 3: Write minimal implementation**

```python
# local_speech.py
"""Local TTS/STT HTTP server for the Bridge.

Mirrors the cloud endpoints POST /api/atc/say (Piper) and POST /api/atc/ptt
(faster-whisper) on 127.0.0.1 so the radio page can call them directly. Engines
are loaded lazily and are optional: the module imports and the server starts
even when faster-whisper / piper-tts are not installed.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT_START = 8765
DEFAULT_PORT_END = 8770


def pick_port(start: int = DEFAULT_PORT_START, end: int = DEFAULT_PORT_END) -> int:
    """First bindable port on 127.0.0.1 in [start, end]. Raises if none free."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError(f"no free port in {start}..{end}")


class _Handler(BaseHTTPRequestHandler):
    # Set by LocalSpeechServer before serving.
    server_ref: "LocalSpeechServer" = None  # type: ignore

    def log_message(self, *args):  # silence default stderr logging
        pass

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        # Allow the page origin + Private Network Access (Chrome public->localhost).
        self.send_header("Access-Control-Allow-Origin", self.server_ref.allow_origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            engines = self.server_ref.engines
            self._send_json(200, {
                "ok": True,
                "ready": bool(engines and engines.ready),
                "engines": ["piper", "faster-whisper"],
                "model": self.server_ref.model_name,
            })
        else:
            self._send_json(404, {"ok": False, "error": "not found"})


class LocalSpeechServer:
    def __init__(self, engines=None, allow_origin: str = "*", model_name: str = "base.en"):
        self.engines = engines
        self.allow_origin = allow_origin
        self.model_name = model_name
        self.port = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        self.port = pick_port()
        handler = _Handler
        handler.server_ref = self
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
```

**Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_local_speech_server.py -v`
Expected: PASS (both tests).

**Step 5: Commit**

```bash
git add local_speech.py tests/test_local_speech_server.py
git commit -m "feat(bridge): local speech server skeleton with /health + CORS/PNA"
```

---

### Task 2: Whisper bias-prompt builder (Python port)

Port `buildSttPrompt` from `OpenSquawk/server/api/atc/ptt.post.ts`. The page sends
`expected: {phrase?, tokens?}`; the Bridge builds the same kind of bias prompt.
Keep the telephony list static (mirror the values of `DEFAULT_AIRLINE_TELEPHONY`
from `OpenSquawk/shared/utils/radioSpeech`).

**Files:**
- Modify: `local_speech.py`
- Test: `tests/test_stt_prompt.py`

**Step 1: Write the failing test**

```python
# tests/test_stt_prompt.py
from local_speech import build_stt_prompt


def test_prompt_includes_phonetic_alphabet_and_base():
    p = build_stt_prompt(None)
    assert "Alfa Bravo Charlie" in p
    assert "ICAO English" in p


def test_prompt_appends_expected_phrase_and_tokens_last():
    p = build_stt_prompt({"phrase": "Ready for taxi", "tokens": ["25R", "DLH123"]})
    # Expected content is appended after the generic bias (survives truncation).
    assert p.index("Ready for taxi") > p.index("Alfa Bravo Charlie")
    assert "25R" in p and "DLH123" in p
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_stt_prompt.py -v`
Expected: FAIL (`ImportError: cannot import name 'build_stt_prompt'`).

**Step 3: Write minimal implementation** (append to `local_speech.py`)

```python
# Mirror the *values* of DEFAULT_AIRLINE_TELEPHONY (OpenSquawk/shared/utils/
# radioSpeech.ts). Copy the current list verbatim when implementing; the sample
# below is illustrative and must be replaced with the real values.
_AIRLINE_TELEPHONY = [
    "Lufthansa", "Speedbird", "Ryanair", "Easy", "Wizz Air", "Eurowings",
    # ... copy the rest from the shared map ...
]

_STT_BIAS = " ".join([
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
])


def build_stt_prompt(expected: dict | None) -> str:
    """Bias prompt for faster-whisper. Generic bias first, expected values last
    (Whisper keeps only the final ~224 tokens)."""
    segments = [_STT_BIAS]
    if expected:
        phrase = (expected.get("phrase") or "").strip()
        if phrase:
            segments.append(f"Expected pilot transmission: {phrase}.")
        tokens = []
        seen = set()
        for t in (expected.get("tokens") or []):
            t = str(t).strip()
            if t and t not in seen:
                seen.add(t)
                tokens.append(t)
        if tokens:
            segments.append(f"Expected values: {', '.join(tokens)}.")
    return " ".join(segments)
```

**Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_stt_prompt.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add local_speech.py tests/test_stt_prompt.py
git commit -m "feat(bridge): STT bias-prompt builder"
```

---

### Task 3: STT endpoint `POST /api/atc/ptt`

Handler decodes base64 audio, calls the (injected/mocked) whisper engine, returns
`{success, transcription}` — the exact shape `usePttRecording` expects.

**Files:**
- Modify: `local_speech.py`
- Test: `tests/test_local_speech_server.py`

**Step 1: Write the failing test** (add to existing test file)

```python
def _post(port, path, payload):
    import urllib.request
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


class FakeEngines:
    ready = True
    def transcribe(self, wav_bytes, prompt): return "ready for taxi"
    def synthesize(self, text, voice, speed): return b"RIFFfake", "audio/wav", "wav"


def test_ptt_returns_transcription():
    import base64
    server = LocalSpeechServer(engines=FakeEngines())
    server.start()
    try:
        audio = base64.b64encode(b"\x00\x01\x02").decode()
        status, body = _post(server.port, "/api/atc/ptt", {
            "audio": audio, "moduleId": "m", "lessonId": "l", "format": "wav",
            "expected": {"phrase": "ready for taxi", "tokens": []},
        })
        assert status == 200
        assert body == {"success": True, "transcription": "ready for taxi"}
    finally:
        server.stop()
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_local_speech_server.py::test_ptt_returns_transcription -v`
Expected: FAIL (404 / no `do_POST`).

**Step 3: Implement** — add `do_POST` routing + `_handle_ptt` to `_Handler`:

```python
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._send_json(400, {"success": False, "error": "bad json"})

        engines = self.server_ref.engines
        if not engines or not engines.ready:
            return self._send_json(503, {"success": False, "error": "engines not ready"})

        if self.path == "/api/atc/ptt":
            return self._handle_ptt(body, engines)
        if self.path == "/api/atc/say":
            return self._handle_say(body, engines)
        return self._send_json(404, {"success": False, "error": "not found"})

    def _handle_ptt(self, body, engines):
        import base64
        audio_b64 = body.get("audio") or ""
        if not audio_b64:
            return self._send_json(400, {"success": False, "error": "audio required"})
        try:
            wav = base64.b64decode(audio_b64)
        except Exception:
            return self._send_json(400, {"success": False, "error": "bad base64"})
        prompt = build_stt_prompt(body.get("expected"))
        fmt = body.get("format") or "wav"
        text = engines.transcribe(wav, prompt, fmt).strip()
        if not text:
            return self._send_json(400, {"success": False, "error": "no speech detected"})
        return self._send_json(200, {"success": True, "transcription": text})
```

Update `FakeEngines.transcribe` signature to `(self, wav_bytes, prompt, fmt="wav")`.

**Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_local_speech_server.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add local_speech.py tests/test_local_speech_server.py
git commit -m "feat(bridge): local STT endpoint /api/atc/ptt"
```

---

### Task 4: TTS endpoint `POST /api/atc/say`

Returns `{success, audio:{base64, mime, size, ext}, ...}` — the shape
`useRadioSpeech` reads (`response.audio.base64`, `response.audio.mime`).

**Files:**
- Modify: `local_speech.py`
- Test: `tests/test_local_speech_server.py`

**Step 1: Write the failing test**

```python
def test_say_returns_audio_base64():
    server = LocalSpeechServer(engines=FakeEngines())
    server.start()
    try:
        status, body = _post(server.port, "/api/atc/say", {
            "text": "cleared for takeoff", "voice": "verse", "speed": 1.0,
            "level": 4, "preNormalized": True,
        })
        assert status == 200
        assert body["success"] is True
        assert body["audio"]["mime"] == "audio/wav"
        assert body["audio"]["ext"] == "wav"
        assert body["audio"]["base64"]  # non-empty
    finally:
        server.stop()
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_local_speech_server.py::test_say_returns_audio_base64 -v`
Expected: FAIL (`_handle_say` missing).

**Step 3: Implement** — add `_handle_say`:

```python
    def _handle_say(self, body, engines):
        import base64
        text = (body.get("text") or "").strip()
        if not text:
            return self._send_json(400, {"success": False, "error": "text required"})
        voice = (body.get("voice") or "").strip() or None
        speed = float(body.get("speed") or 1.0)
        speed = max(0.5, min(2.0, speed))
        wav_bytes, mime, ext = engines.synthesize(text, voice, speed)
        return self._send_json(200, {
            "success": True,
            "text": text,
            "audio": {
                "mime": mime,
                "base64": base64.b64encode(wav_bytes).decode(),
                "size": len(wav_bytes),
                "ext": ext,
            },
            "meta": {"ttsProvider": "piper-local"},
        })
```

**Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_local_speech_server.py -v`
Expected: PASS (all).

**Step 5: Commit**

```bash
git add local_speech.py tests/test_local_speech_server.py
git commit -m "feat(bridge): local TTS endpoint /api/atc/say"
```

---

### Task 5: Engine wrappers (lazy Piper + faster-whisper)

Real engines behind the `transcribe` / `synthesize` interface used above. Imports
are lazy so the module works without the packages installed; a light test asserts
graceful behaviour when deps are absent.

**Files:**
- Modify: `local_speech.py`
- Test: `tests/test_engines.py`

**Step 1: Write the failing test**

```python
# tests/test_engines.py
from local_speech import SpeechEngines


def test_engines_not_ready_until_loaded():
    e = SpeechEngines(model_dir="/tmp/osq-nonexistent", model_name="base.en")
    assert e.ready is False  # nothing loaded yet


def test_load_raises_helpful_error_without_deps(monkeypatch):
    e = SpeechEngines(model_dir="/tmp/osq-nonexistent", model_name="base.en")
    monkeypatch.setattr(e, "_import_whisper", lambda: (_ for _ in ()).throw(ImportError("no faster_whisper")))
    import pytest
    with pytest.raises(RuntimeError) as ei:
        e.load()
    assert "faster-whisper" in str(ei.value)
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_engines.py -v`
Expected: FAIL (`SpeechEngines` missing).

**Step 3: Implement** — add `SpeechEngines` to `local_speech.py`:

```python
import io
import wave


class SpeechEngines:
    """Lazily loads faster-whisper + Piper. `ready` flips True after load()."""

    def __init__(self, model_dir: str, model_name: str = "base.en",
                 piper_voice_path: str | None = None):
        self.model_dir = model_dir
        self.model_name = model_name
        self.piper_voice_path = piper_voice_path
        self._whisper = None
        self._piper = None
        self.ready = False

    def _import_whisper(self):
        from faster_whisper import WhisperModel  # lazy
        return WhisperModel

    def _import_piper(self):
        from piper import PiperVoice  # lazy
        return PiperVoice

    def load(self):
        try:
            WhisperModel = self._import_whisper()
        except ImportError as e:
            raise RuntimeError(f"faster-whisper not installed: {e}") from e
        try:
            PiperVoice = self._import_piper()
        except ImportError as e:
            raise RuntimeError(f"piper-tts not installed: {e}") from e

        # CPU int8 default; device="auto" lets faster-whisper use CUDA if the
        # runtime is present, otherwise CPU. compute_type int8 keeps CPU fast.
        self._whisper = WhisperModel(
            self.model_name, download_root=self.model_dir,
            device="auto", compute_type="int8",
        )
        self._piper = PiperVoice.load(self.piper_voice_path)
        self.ready = True

    def transcribe(self, wav_bytes: bytes, prompt: str, fmt: str = "wav") -> str:
        segments, _ = self._whisper.transcribe(
            io.BytesIO(wav_bytes), language="en", temperature=0,
            initial_prompt=prompt,
        )
        return " ".join(seg.text for seg in segments)

    def synthesize(self, text: str, voice: str | None, speed: float):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            self._piper.synthesize(text, wav_file, length_scale=1.0 / max(0.1, speed))
        return buf.getvalue(), "audio/wav", "wav"
```

> Note: verify the exact `piper-tts` and `faster-whisper` APIs against their
> installed versions during implementation (method names/signatures vary by
> release) and adjust. The public interface (`transcribe`/`synthesize`/`ready`)
> must stay stable for the handler + tests.

**Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_engines.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add local_speech.py tests/test_engines.py
git commit -m "feat(bridge): lazy Piper + faster-whisper engine wrappers"
```

---

### Task 6: On-demand setup (dep install + model download)

One-time setup when the checkbox is first enabled: install `faster-whisper` +
`piper-tts` into the running venv and download the Piper voice. Model weights for
whisper download on first `load()` via `download_root`.

**Files:**
- Modify: `local_speech.py`
- Test: `tests/test_setup.py`

**Step 1: Write the failing test**

```python
# tests/test_setup.py
from local_speech import ensure_dependencies


def test_ensure_dependencies_skips_when_import_succeeds(monkeypatch):
    calls = []
    monkeypatch.setattr("local_speech._pip_install", lambda pkgs: calls.append(pkgs))
    monkeypatch.setattr("local_speech._deps_present", lambda: True)
    ensure_dependencies()
    assert calls == []  # already present → no install


def test_ensure_dependencies_installs_when_missing(monkeypatch):
    calls = []
    monkeypatch.setattr("local_speech._deps_present", lambda: False)
    monkeypatch.setattr("local_speech._pip_install", lambda pkgs: calls.append(pkgs))
    ensure_dependencies()
    assert calls and "faster-whisper" in calls[0]
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_setup.py -v`
Expected: FAIL (`ensure_dependencies` missing).

**Step 3: Implement** — add to `local_speech.py`:

```python
import subprocess
import sys

_REQUIRED_PKGS = ["faster-whisper", "piper-tts"]


def _deps_present() -> bool:
    try:
        import faster_whisper  # noqa: F401
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def _pip_install(pkgs: list[str]):
    subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])


def ensure_dependencies():
    """Install engine deps into the current venv if missing. One-time, slow."""
    if _deps_present():
        return
    _pip_install(_REQUIRED_PKGS)
```

**Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_setup.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add local_speech.py tests/test_setup.py
git commit -m "feat(bridge): on-demand dependency setup for local speech"
```

---

### Task 7: Wire into `bridge_app.py` (config, state, exposed API)

**Files:**
- Modify: `bridge_app.py`
- Test: `tests/test_bridge_local_speech.py`

**Step 1: Write the failing test**

```python
# tests/test_bridge_local_speech.py
from bridge_app import Api  # adjust import to the actual class name


def test_set_local_speech_persists_and_reports_state(tmp_path, monkeypatch):
    # Point config at a temp dir; stub the heavy setup so the test is fast.
    import bridge_app
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")

    api = bridge_app.Api()  # match real constructor
    monkeypatch.setattr(api, "_start_local_speech", lambda: None)

    api.set_local_speech(True)
    state = api.get_state()
    assert state["local_speech"]["enabled"] is True
```

> Adjust class/constructor to match `bridge_app.py` (inspect the exposed API
> class and how `get_state` is built). Stub `_start_local_speech` so no engines
> load in the test.

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_bridge_local_speech.py -v`
Expected: FAIL (`set_local_speech` missing).

**Step 3: Implement** in `bridge_app.py`:

- Add module constant `MODELS_DIR = CONFIG_DIR / "models"`.
- In `__init__`: read `local_speech_enabled` from config; init
  `self._local_speech = {"enabled": <cfg>, "ready": False, "installing": False, "error": None, "port": 0, "model": "base.en"}` and `self._local_server = None`.
- Add exposed methods:

```python
    # ---- exposed API (called from JS) ----
    def set_local_speech(self, enabled: bool) -> dict:
        self._local_speech["enabled"] = bool(enabled)
        self._update_config(local_speech_enabled=bool(enabled))
        if enabled:
            threading.Thread(target=self._start_local_speech, daemon=True).start()
        else:
            self._stop_local_speech()
        return {"ok": True}

    def _start_local_speech(self):
        import local_speech
        self._local_speech.update(installing=True, error=None)
        try:
            local_speech.ensure_dependencies()
            engines = local_speech.SpeechEngines(
                model_dir=str(MODELS_DIR), model_name=self._local_speech["model"],
                piper_voice_path=str(MODELS_DIR / "piper" / "en_US-ryan-medium.onnx"),
            )
            # Download the Piper voice if missing (see local_speech.ensure_piper_voice).
            local_speech.ensure_piper_voice(MODELS_DIR)
            engines.load()
            server = local_speech.LocalSpeechServer(
                engines=engines, allow_origin=BASE_URL,
                model_name=self._local_speech["model"],
            )
            server.start()
            self._local_server = server
            self._local_speech.update(ready=True, installing=False, port=server.port)
        except Exception as exc:  # noqa: BLE001
            self._local_speech.update(ready=False, installing=False, error=str(exc))

    def _stop_local_speech(self):
        if self._local_server:
            self._local_server.stop()
            self._local_server = None
        self._local_speech.update(ready=False, installing=False, port=0)
```

- In `get_state()`, add to the returned dict: `"local_speech": dict(self._local_speech)`.
- On startup (where the app boots the telemetry thread), if
  `self._local_speech["enabled"]`, kick `_start_local_speech` in a daemon thread.
- Add `ensure_piper_voice(models_dir)` to `local_speech.py` that downloads the
  chosen Piper `.onnx` + `.onnx.json` into `models_dir/piper/` if absent (HTTPS
  from the Piper voices repo; skip if files exist). Add a small unit test that it
  no-ops when files already exist (mock the download).

**Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_bridge_local_speech.py tests/test_local_speech_server.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add bridge_app.py local_speech.py tests/test_bridge_local_speech.py
git commit -m "feat(bridge): wire local speech into app state + config + lifecycle"
```

---

### Task 8: Bridge UI checkbox + status

**Files:**
- Modify: `web/index.html` (add checkbox under the "Live ATC" section)
- Modify: `web/app.js` (bind to `set_local_speech`, render `local_speech` status)

**Step 1:** In `web/index.html`, in the Live ATC block, add:

```html
<label class="row toggle">
  <input type="checkbox" id="localSpeechToggle">
  <span>Sprache lokal verarbeiten (schneller)</span>
</label>
<div id="localSpeechStatus" class="hint"></div>
```

**Step 2:** In `web/app.js`, following the existing pattern for toggles/state:

```js
const localSpeechToggle = document.getElementById('localSpeechToggle');
const localSpeechStatus = document.getElementById('localSpeechStatus');

localSpeechToggle.addEventListener('change', () => {
  window.pywebview.api.set_local_speech(localSpeechToggle.checked);
});

// Inside the existing render/get_state polling handler:
function renderLocalSpeech(ls) {
  if (!ls) return;
  localSpeechToggle.checked = !!ls.enabled;
  if (!ls.enabled)        localSpeechStatus.textContent = '';
  else if (ls.error)      localSpeechStatus.textContent = 'Fehler: ' + ls.error;
  else if (ls.installing) localSpeechStatus.textContent = 'Wird eingerichtet… (einmaliger Download)';
  else if (ls.ready)      localSpeechStatus.textContent = 'Aktiv · Port ' + ls.port + ' · ' + ls.model;
  else                    localSpeechStatus.textContent = 'Starte…';
}
// call renderLocalSpeech(state.local_speech) where other state is rendered.
```

**Step 3: Manual verification** (no automated UI test in this repo):

Run: `python bridge_app.py` (macOS dev), toggle the checkbox, confirm status text
transitions installing → active and `config.json` gains `local_speech_enabled: true`.
Then `GET http://127.0.0.1:8765/health` returns `ready: true`.

**Step 4: Commit**

```bash
git add web/index.html web/app.js
git commit -m "feat(bridge): UI checkbox + status for local speech"
```

---

## Part B — Frontend (`OpenSquawk`)

> Run these in `/Users/domi/html/OpenSquawk`. Tests use Vitest
> (`yarn vitest run <file>`). Follow the existing `*.test.ts` patterns in
> `app/composables` / `server/utils`.

### Task 9: `useLocalSpeechBridge` discovery composable

**Files:**
- Create: `app/composables/useLocalSpeechBridge.ts`
- Test: `app/composables/useLocalSpeechBridge.test.ts`

**Step 1: Write the failing test**

```ts
// app/composables/useLocalSpeechBridge.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { probeLocalSpeech } from './useLocalSpeechBridge'

describe('probeLocalSpeech', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('returns the base url of the first ready port', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('8765')) return { ok: true, json: async () => ({ ok: true, ready: false }) } as any
      if (url.includes('8766')) return { ok: true, json: async () => ({ ok: true, ready: true }) } as any
      throw new Error('refused')
    }))
    const base = await probeLocalSpeech([8765, 8766, 8767])
    expect(base).toBe('http://127.0.0.1:8766')
  })

  it('returns null when nothing is ready', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('refused') }))
    const base = await probeLocalSpeech([8765, 8766])
    expect(base).toBeNull()
  })
})
```

**Step 2: Run to verify it fails**

Run: `yarn vitest run app/composables/useLocalSpeechBridge.test.ts`
Expected: FAIL (module not found).

**Step 3: Implement**

```ts
// app/composables/useLocalSpeechBridge.ts
import { ref } from 'vue'

const PORTS = [8765, 8766, 8767, 8768, 8769, 8770]
const HEALTH_TIMEOUT_MS = 1200
const RECHECK_MS = 15_000

export async function probeLocalSpeech(ports: number[] = PORTS): Promise<string | null> {
  for (const port of ports) {
    const base = `http://127.0.0.1:${port}`
    try {
      const ctrl = new AbortController()
      const t = setTimeout(() => ctrl.abort(), HEALTH_TIMEOUT_MS)
      const res = await fetch(`${base}/health`, { signal: ctrl.signal })
      clearTimeout(t)
      if (!res.ok) continue
      const body = await res.json()
      if (body?.ok && body?.ready) return base
    } catch { /* port closed / refused — try next */ }
  }
  return null
}

const localBase = ref<string | null>(null)
let started = false

export function useLocalSpeechBridge() {
  const refresh = async () => { localBase.value = await probeLocalSpeech() }
  if (typeof window !== 'undefined' && !started) {
    started = true
    void refresh()
    setInterval(() => void refresh(), RECHECK_MS)
  }
  return {
    localBase,
    /** Absolute URL for an endpoint, or null when local is unavailable. */
    localUrl: (path: string) => (localBase.value ? localBase.value + path : null),
    refresh,
  }
}
```

**Step 4: Run to verify it passes**

Run: `yarn vitest run app/composables/useLocalSpeechBridge.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add app/composables/useLocalSpeechBridge.ts app/composables/useLocalSpeechBridge.test.ts
git commit -m "feat(live-atc): local speech bridge discovery composable"
```

---

### Task 10: Route STT through local with fallback

**Files:**
- Modify: `app/composables/usePttRecording.ts` (the two `api.post('/api/atc/ptt', …)` calls in `processTransmission`)
- Test: `app/composables/usePttRecording.localRouting.test.ts`

**Approach:** Add a helper that tries the local endpoint first (raw `fetch`, no
auth header — the Bridge needs none), and on any non-2xx / throw falls back to the
existing `api.post`. Keep the request bodies identical.

**Step 1: Write the failing test** (unit-test the helper in isolation)

```ts
// app/composables/usePttRecording.localRouting.test.ts
import { describe, it, expect, vi } from 'vitest'
import { postWithLocalFallback } from './usePttRecording'

describe('postWithLocalFallback', () => {
  it('uses local when it returns 2xx', async () => {
    const local = vi.fn(async () => ({ ok: true, json: async () => ({ success: true, transcription: 'roger' }) }))
    vi.stubGlobal('fetch', local as any)
    const cloud = vi.fn()
    const res = await postWithLocalFallback('http://127.0.0.1:8765/api/atc/ptt', { a: 1 }, cloud as any)
    expect(res.transcription).toBe('roger')
    expect(cloud).not.toHaveBeenCalled()
  })

  it('falls back to cloud when local throws', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('refused') }) as any)
    const cloud = vi.fn(async () => ({ success: true, transcription: 'cloud' }))
    const res = await postWithLocalFallback('http://127.0.0.1:8765/api/atc/ptt', { a: 1 }, cloud as any)
    expect(res.transcription).toBe('cloud')
    expect(cloud).toHaveBeenCalledOnce()
  })

  it('falls back to cloud when localUrl is null', async () => {
    const cloud = vi.fn(async () => ({ success: true, transcription: 'cloud' }))
    const res = await postWithLocalFallback(null, { a: 1 }, cloud as any)
    expect(res.transcription).toBe('cloud')
  })
})
```

**Step 2: Run to verify it fails**

Run: `yarn vitest run app/composables/usePttRecording.localRouting.test.ts`
Expected: FAIL (`postWithLocalFallback` not exported).

**Step 3: Implement** — export the helper from `usePttRecording.ts` and use it:

```ts
const LOCAL_TIMEOUT_MS = 8000

export async function postWithLocalFallback(
  localUrl: string | null,
  body: any,
  cloudPost: () => Promise<any>,
): Promise<any> {
  if (localUrl) {
    try {
      const ctrl = new AbortController()
      const t = setTimeout(() => ctrl.abort(), LOCAL_TIMEOUT_MS)
      const res = await fetch(localUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      })
      clearTimeout(t)
      if (res.ok) return await res.json()
    } catch { /* fall through to cloud */ }
  }
  return cloudPost()
}
```

Then in `processTransmission`, replace each `api.post('/api/atc/ptt', payload)`
with:

```ts
const { localUrl } = useLocalSpeechBridge()
const result = await postWithLocalFallback(
  localUrl('/api/atc/ptt'),
  payload,
  () => api.post('/api/atc/ptt', payload),
)
```

(Extract `payload` to a variable for both the intercom and radio branches.)

**Step 4: Run to verify it passes**

Run: `yarn vitest run app/composables/usePttRecording.localRouting.test.ts`
Expected: PASS. Then run the existing suite to confirm no regressions:
`yarn vitest run app/composables`

**Step 5: Commit**

```bash
git add app/composables/usePttRecording.ts app/composables/usePttRecording.localRouting.test.ts
git commit -m "feat(live-atc): route STT through local bridge with cloud fallback"
```

---

### Task 11: Route TTS through local with fallback

**Files:**
- Modify: `app/composables/useRadioSpeech.ts` (the `api.post('/api/atc/say', …)` calls in `fetchSpeechAudio` and `speakPlainText`)
- Test: `app/composables/useRadioSpeech.localRouting.test.ts`

**Key correctness point:** the Bridge does **not** normalize. `fetchSpeechAudio`
already sends `preNormalized` text for the normalized path — fine. For
`speakPlainText` (which sends raw text and relies on server-side `normalizeATC`),
normalize client-side before sending to the local endpoint: reuse
`engine.normalizeATCText` and send `preNormalized: true` in the local body. The
cloud fallback body stays exactly as today (so cloud still normalizes when it
runs).

**Step 1: Write the failing test** — reuse `postWithLocalFallback` (import it from
`usePttRecording.ts`, or lift it into a shared `useLocalSpeechBridge.ts` export;
prefer moving it to `useLocalSpeechBridge.ts` and importing from there in both
composables to stay DRY). Add a test asserting `speakPlainText`'s local body
carries `preNormalized: true`.

```ts
// app/composables/useRadioSpeech.localRouting.test.ts
import { describe, it, expect, vi } from 'vitest'
import { postWithLocalFallback } from './useLocalSpeechBridge'

describe('TTS local routing', () => {
  it('uses local audio response when available', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, json: async () => ({ success: true, audio: { base64: 'AAA', mime: 'audio/wav', size: 3, ext: 'wav' } }),
    })) as any)
    const cloud = vi.fn()
    const res = await postWithLocalFallback('http://127.0.0.1:8765/api/atc/say', { text: 'x', preNormalized: true }, cloud as any)
    expect(res.audio.base64).toBe('AAA')
    expect(cloud).not.toHaveBeenCalled()
  })
})
```

> If moving `postWithLocalFallback` into `useLocalSpeechBridge.ts`, update Task
> 10's import accordingly and re-run its test.

**Step 2: Run to verify it fails**

Run: `yarn vitest run app/composables/useRadioSpeech.localRouting.test.ts`
Expected: FAIL until the helper is importable from `useLocalSpeechBridge.ts`.

**Step 3: Implement**

- Move `postWithLocalFallback` + `LOCAL_TIMEOUT_MS` into `useLocalSpeechBridge.ts`
  and export; import it in both `usePttRecording.ts` and `useRadioSpeech.ts`.
- In `fetchSpeechAudio`: build the existing body, then
  `const { localUrl } = useLocalSpeechBridge()` and
  `return await postWithLocalFallback(localUrl('/api/atc/say'), body, () => api.post('/api/atc/say', body, { signal: abort.signal }))`.
  Preserve the abort/timeout behaviour (the local fetch has its own timeout; the
  outer watchdog still aborts the cloud path).
- In `speakPlainText`: create a local body that normalizes the text
  (`normalizeATCText(trimmed, { ...vars.value, ...flags.value })`) and sets
  `preNormalized: true`; keep the cloud body as the current raw-text version.
  `await postWithLocalFallback(localUrl('/api/atc/say'), localBody, () => api.post('/api/atc/say', cloudBody))`.

**Step 4: Run to verify it passes**

Run: `yarn vitest run app/composables/useRadioSpeech.localRouting.test.ts app/composables/usePttRecording.localRouting.test.ts`
Expected: PASS. Then `yarn vitest run app/composables` for no regressions.

**Step 5: Commit**

```bash
git add app/composables/useRadioSpeech.ts app/composables/useLocalSpeechBridge.ts app/composables/useRadioSpeech.localRouting.test.ts app/composables/usePttRecording.ts
git commit -m "feat(live-atc): route TTS through local bridge with cloud fallback"
```

---

## Final verification (end-to-end, manual)

1. Bridge (Windows or macOS dev): enable the checkbox, wait for "Aktiv · Port …".
2. Open `/live-atc` (paired). Confirm `GET http://127.0.0.1:<port>/health` shows
   `ready: true` from the browser (DevTools console).
3. PTT a transmission → network tab shows the request going to `127.0.0.1`, not
   the cloud; transcription returns. Kill the Bridge server → next PTT falls back
   to `/api/atc/ptt` cloud with no user-visible break.
4. Same for TTS (`/api/atc/say`): local response plays with radio effects; after
   killing the server, cloud takes over.
5. Phone via QR: confirm it silently uses cloud (no localhost reachable).

## Follow-ups (out of scope here)

- CUDA GPU acceleration (CUDA-enabled ctranslate2 packaging).
- User-selectable whisper model size in the Bridge UI.
- Optional page-side toggle/indicator ("Local speech active").
