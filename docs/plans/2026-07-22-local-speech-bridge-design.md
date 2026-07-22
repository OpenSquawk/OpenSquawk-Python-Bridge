# Local TTS/STT via the Bridge — Design

**Date:** 2026-07-22
**Status:** Approved
**Repos touched:** `osq-gui` (Bridge, Python), `OpenSquawk` (Nuxt frontend — no server change)

## Problem

TTS and STT for `/live-atc` currently run in the cloud: the browser radio page
posts to the Nuxt endpoints `POST /api/atc/say` (OpenAI/Piper/Speaches TTS) and
`POST /api/atc/ptt` (OpenAI Whisper STT). Every transmission makes a full
round-trip to the cloud and out to OpenAI. On machines running the Bridge we can
run TTS/STT **locally** and cut that latency dramatically.

## Goal

Let the Bridge host local TTS/STT and have the radio page use it directly over
`http://127.0.0.1`, controlled by a checkbox, with automatic fallback to the
existing cloud endpoints whenever local is unavailable or fails.

## Decisions (locked)

- **Engines:** Piper (TTS) + faster-whisper (STT). The cloud `say` endpoint
  already supports Piper as a provider, so this stays consistent.
- **Transport/discovery:** Bridge runs a local HTTP server on a fixed port
  (8765, fallback scan up to 8770). The page health-scans those ports and, when
  it finds a `ready` server, calls it directly. No cloud/server change needed.
- **STT model:** `base.en`, int8 on CPU. Downloaded on first enable to a cache
  dir (not in Git). GPU/CUDA acceleration is a later follow-up, not in the first
  cut.
- **Deps:** `faster-whisper` + `piper-tts` are **not** in `requirements.txt`
  (would bloat every install by ~200 MB). They are installed on-demand into the
  venv the first time the checkbox is enabled.
- **Checkbox:** master switch in the Bridge UI. The page auto-prefers local when
  ready; cloud is the fallback. No separate page-side toggle in the MVP.

## Architecture

```
Radio page (https://opensquawk.de/pm)
  ├─ health-scan 127.0.0.1:8765..8770  → ready?
  ├─ yes  → fetch http://127.0.0.1:<port>/api/atc/say|ptt   (fast, local)
  └─ no/error → api.post('/api/atc/say|ptt')                (cloud fallback)

Bridge (pywebview) ── daemon thread ── ThreadingHTTPServer 127.0.0.1:8765
                                        ├─ GET  /health   {ok, ready, engines, model}
                                        ├─ POST /api/atc/say  → Piper → wav base64
                                        └─ POST /api/atc/ptt  → faster-whisper → text
```

## Components

### Bridge (`osq-gui`, Python)

- **`local_speech.py`** (new)
  - `LocalSpeechServer`: `ThreadingHTTPServer` bound to `127.0.0.1`, daemon
    thread, port selection 8765→8770.
  - Request handler with **CORS** (reflect/allow the configured `BASE_URL`
    origin) and **Private Network Access** support: handle `OPTIONS` preflight
    and return `Access-Control-Allow-Private-Network: true`. Without this Chrome
    blocks the public→localhost request.
  - Lazily-loaded engine wrappers `PiperEngine` (text→wav) and `WhisperEngine`
    (audio→text). Engines load on first request after readiness.
  - Whisper bias-prompt builder ported minimally to Python: phonetic alphabet +
    aviation numbers/phraseology + a statically embedded airline-telephony list
    + the request's `expected` phrase/tokens.
- **`bridge_app.py`**
  - Exposed JS API: `set_local_speech(enabled)`; readiness/setup status surfaced
    through `get_state()` as `local_speech: {enabled, ready, installing, model,
    error, port}`.
  - Persist `local_speech_enabled` in `config.json` via `_update_config`.
  - On enable: one-time setup — install `faster-whisper` + `piper-tts` into the
    venv, download `base.en` (int8) and one en_US Piper voice into
    `~/.opensquawk-bridge/models/`, then start the server. Setup runs off the UI
    thread; progress reflected via `get_state()` polling.
- **`web/index.html` + `web/app.js`**: checkbox under the "Live ATC" section —
  "Sprache lokal verarbeiten (schneller)" — bound to `set_local_speech`, showing
  installing/ready/error status.

### Frontend (`OpenSquawk`, no server change)

- **`useLocalSpeechBridge.ts`** (new): port-scan + `/health`, caches
  `localSpeechReady` + base URL, periodic re-check; safe on phones (nothing on
  127.0.0.1 → stays cloud).
- **`useRadioSpeech.ts` / `usePttRecording.ts`**: before the cloud call, if
  local is ready, `fetch(localUrl, …)`; on any failure fall back transparently
  to the existing `api.post('/api/atc/say'|'/api/atc/ptt')` route. TTS text is
  normalized client-side and sent as `preNormalized` so the Python side never
  reimplements the ATC normalizer.

## Data flow

- **STT:** page sends base64 audio (wav/webm) + `expected` {phrase, tokens} →
  Bridge builds the bias prompt → faster-whisper → `{success, transcription}`
  (same shape as cloud).
- **TTS:** page sends already-normalized text + voice/speed → Piper → 16-bit WAV
  → `{success, audio:{base64, mime:'audio/wav', size, ext:'wav'}, …}`.
  Client-side radio effects (Pizzicato) run unchanged.

## Error handling / fallback

- Server unreachable, `/health` not ready, non-2xx, timeout (~8 s), or exception
  → immediately use the cloud route. Never block the speech queue.
- Phone/QR users: scan finds nothing on 127.0.0.1 → cloud automatically.
- Dep/model setup failure → checkbox shows the error and stays off; cloud keeps
  working.

## Testing

- **Bridge:** handler tests (CORS/PNA headers, `/health`, say/ptt with **mocked**
  engines), port-selection, prompt builder — no real models. Follows existing
  `tests/` patterns.
- **Frontend:** Vitest for discovery (health-scan) and fallback routing (mocked
  fetch).

## Deliberate simplifications (MVP)

- CPU int8 `base.en` as the robust default; real CUDA GPU acceleration is a
  follow-up (needs CUDA-enabled ctranslate2 packaging).
- Single master checkbox in the Bridge; no separate page toggle (auto-detect +
  fallback covers all cases).
