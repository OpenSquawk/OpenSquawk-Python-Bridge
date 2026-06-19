# Global Push-to-Talk hotkey — design

Date: 2026-06-19

## Goal

Let a pilot key a **global** push-to-talk (PTT) hotkey while flying in the sim
(the `/pm` browser tab is in the background) and have that start/stop the
microphone recording **in the existing `/pm` browser app**. No Electron, no
rewrite of the heavy Nuxt app — it stays in the user's real browser.

## Why a global hotkey can't live in the browser

A browser tab only receives `keydown`/`keyup` while focused. While the user is
in MSFS, the tab is backgrounded, so a pure web hotkey is impossible. The global
keypress must be captured by a native process. We already ship one: the
**OpenSquawk Bridge** (pywebview/Python, `osq-gui`), which runs on the PC and is
already paired to the account by a per-token link (`/pm?token=…`).

## Architecture (token-keyed relay)

```
[Global key]
   │ pynput (system-wide, fires even when MSFS is focused)
   ▼
OpenSquawk Bridge (Python)
   │ POST /api/bridge/ptt {state: "down"|"up"}   (only on press/release edges)
   ▼
Backend (Nuxt/Nitro)
   │ pttBus.publish(token, state)  →  WS push to peers on the same token
   ▼
/pm (Nuxt, real browser, tab may be backgrounded)
   down → startRecording(false)
   up   → stopRecording()
```

Key facts that make this work:

- `/pm` **already** has the full PTT pipeline: `startRecording(false)` /
  `stopRecording()` bound to the on-screen "Hold to transmit" pad, plus
  `micPermission` / `requestMicAccess()`, PTT beep, auto-stop safety timer and
  upload (`processTransmission`). The feature only needs to call those functions
  from a remote signal.
- An open `getUserMedia` MediaStream and a WebSocket both keep working in a
  backgrounded tab. The `MediaRecorder` path keeps capturing audio in the
  background; only timers are throttled, not WS delivery or audio capture.
- The existing realtime path for `/pm` is a **3 s poll** of
  `/api/bridge/live` — far too slow for PTT. The app already has crossws
  WebSocket infra (`server/api/flightlab/ws.ts`), which we reuse for a low
  latency push. The telemetry poll is left unchanged.

### Why Bridge→server is HTTP, server→/pm is WS

The Bridge already talks to the backend over HTTP with `x-bridge-token`. Key
transitions are sparse (one POST per press, one per release), so a persistent WS
client in Python is unnecessary — a plain `requests.post` reuses the existing
pattern and adds only `pynput`. The latency-critical hop is server→browser,
which is an instant WS push.

## Components

### 1. Bridge (`osq-gui`)

- `pynput.keyboard` global listener. Emit `down` on the first press, `up` on
  release; suppress OS auto-repeat (track a `held` flag).
- Configurable key, stored in `~/.opensquawk-bridge/config.json` next to the
  token. Default: **unset** — the user binds a key in the Bridge UI ("Set key" →
  capture next keypress). A fixed default (e.g. Right Ctrl) collides too easily
  with sim bindings.
- Bridge UI (`web/`): key-bind control + status, and on macOS a hint + button to
  open *System Settings → Privacy → Input Monitoring* (pynput needs it; without
  it no global events arrive). Windows needs no special permission.
- On each edge: `POST {API_URL}/ptt` with `{"state": "down"|"up"}` and the
  existing `x-bridge-token` header.

### 2. Backend (`OpenSquawk`)

- `server/utils/pttBus.ts` — a tiny singleton pub/sub keyed by token, mirroring
  `flightlabTelemetryStore` (publish + subscribe).
- `server/api/bridge/ptt.post.ts` — token auth like `status.post.ts`, validate
  `state ∈ {down, up}`, then `pttBus.publish(token, state)`.
- `server/api/bridge/ws.ts` — crossws handler. A `/pm` peer sends
  `{type: "subscribe", token}`; the handler keeps a `token → Set<peer>` map and,
  on `pttBus` publish, sends `{type: "ptt", state}` to the matching peers.
  Clean up the map on `close`.

### 3. /pm (`OpenSquawk/app/pages/pm.vue`)

- When opened with `?token=`, in addition to the existing telemetry poll, open a
  WebSocket to the new endpoint and send `{type: "subscribe", token}`.
- On `{type: "ptt", state: "down"}` → `startRecording(false)`; on `"up"` →
  `stopRecording()` (reusing existing functions, including the `micPermission`
  guard, which prompts once and then persists per-origin over HTTPS).
- Reconnect with backoff on WS drop. Optional small "Remote PTT" indicator.

## Error handling & edge cases

- **Auto-repeat:** Bridge sends `down` once per physical press, guarded by a
  `held` flag; `up` clears it.
- **Lost `up`:** the existing `PTT_MAX_DURATION_MS` auto-stop timer already
  bounds a stuck transmission.
- **WS disconnected during a press:** PTT is dropped for that press; the page
  reconnects. No partial recording is left running because both edges travel the
  same channel.
- **Auth:** the POST validates the token against `BridgeToken`; the WS only
  relays to peers that presented the same token (shared-secret channel).
- **Double trigger:** the on-screen pad and remote PTT both call the same
  functions; `isRecording` guards against overlap.

## Risks to verify during implementation

- **Backgrounded-tab capture:** confirm `startRecording` works while the tab is
  in the background. `/pm` has a **prerec path over `AudioContext`**, which
  browsers may suspend in background tabs. If so, force the remote-PTT path onto
  the plain `MediaRecorder` branch (or resume the AudioContext on `down`).
- **macOS Input Monitoring:** without the permission `pynput` is silent; the UI
  must guide the user clearly.
- **`ws://` from `https://`:** N/A here — the WS goes to the same origin
  (`opensquawk.de`) over `wss://`, not to localhost.

## Out of scope

- Phone/tablet (QR) PTT — touch devices have no global hotkey; the on-screen pad
  already covers them.
- Native audio capture in the Bridge (kept as a fallback only if browser
  background capture proves unreliable).
