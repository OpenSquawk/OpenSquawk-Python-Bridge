# Flight Actions — Design

**Date:** 2026-06-20
**Status:** Approved (brainstorming)

## Summary

A collapsible "Flight actions" panel in the Bridge app (below the Simulator
selector) lets the user build a single action chain that is replayed either
automatically when a new flight session is detected, or on demand via a
global hotkey / joystick button. Steps are added individually or captured by
recording the user's real keystrokes and clicks.

This is intentionally a *mini* feature: **one** action chain, two triggers
(auto-on-new-flight + manual hotkey) that run the same chain.

## Scope decisions (from brainstorming)

- **Step types:** `wait` (seconds), `key` (key / key combo), `click` (absolute
  screen position). **No joystick *output*** (would need vJoy/ViGEm + a driver
  install) — joystick/HOTAS stays a *trigger only*.
- **Trigger inputs:** key, key combo, or HOTAS/joystick button — same input
  model as the existing push-to-talk (PTT) trigger.
- **Auto-trigger event:** a new flight *session* — sim connected **and**
  aircraft loaded. Fires **once per session**; re-arms on disconnect.
- **Recording captures wait times automatically:** real gaps between captured
  actions become `wait` steps (rounded to 0.1 s).
- **Stop recording** via the UI button; the click/keystroke that stops it is
  discarded (clicks on the app's own window are ignored while recording).

Bundled UI compaction (same change set):
- **Push-to-talk** panel becomes collapsible (reuse the telemetry
  `collapse-head` pattern).
- **Live ATC** panel: QR code moves to the right of the "Open on this PC"
  button, caption "auf Handy oder iPad", shorter copy.

## Architecture

System-wide key/click sending and global input capture only work from the
Python backend (the JS frontend cannot do either). `pynput` is already a
dependency: `keyboard.Controller` / `mouse.Controller` to *send*, listeners to
*record*. The frontend is pure UI talking to `BridgeApi` via the existing
`get_state()` poll + method calls.

### Data model (persisted in `~/.opensquawk-bridge/config.json`)

```jsonc
{
  "actions_steps": [
    {"type": "wait",  "seconds": 1.5},
    {"type": "key",   "keys": ["key:ctrl_l", "char:m"]},  // PTT key-identity format
    {"type": "click", "x": 1820, "y": 40, "button": "left"}
  ],
  "actions_trigger": {"type": "keys", "keys": [...]} | {"type": "joy", "joy": "...", "button": 3} | null,
  "actions_autorun": true
}
```

Key identities reuse the existing `_key_identity` / `_pretty_key` format so the
PTT and actions code share label rendering.

### Backend (`BridgeApi`)

- **Trigger refactor:** generalise the current single-`self.trigger` capture
  system into two slots: `ptt` and `actions`. `self._capturing` becomes a spec
  like `{"slot": "ptt"|"actions", "kind": "key"|"joy"}`. Key-press and
  joystick-button evaluation check both triggers; the actions trigger is
  **edge-triggered** (fires the chain on press, ignores release).
- **`ActionRunner`:** runs the chain on a dedicated daemon thread.
  - `wait` → `time.sleep(seconds)` (bounded/sane max).
  - `key` → press all keys, brief hold, release in reverse.
  - `click` → move mouse to (x, y), click `button`.
  - Single-run guard (no concurrent/overlapping runs); `stop()` cancels.
- **Recording:** add a `pynput.mouse.Listener` (keyboard listener already
  exists). In record mode, keystrokes and clicks are appended with real
  timestamps; gaps become `wait` steps on stop. Clicks within the app window
  bounds are ignored. The action that stops recording is dropped.
- **Auto-trigger:** in `_tick_stream`, detect the edge "no active session →
  `sample.connected` and `sample.aircraft` present". On that edge, if
  `actions_autorun` and the chain is non-empty, run the chain once. Reset the
  armed flag when the source disconnects / is switched.
- **New API methods:** `actions_add_step(step)`, `actions_remove_step(i)`,
  `actions_clear()`, `actions_record_start()`, `actions_record_stop()`,
  `actions_run_now()`, `actions_stop()`, `actions_capture_trigger(kind)` /
  `actions_clear_trigger()` (mirrors PTT capture), `actions_set_autorun(bool)`.
  `get_state()` gains: `actions_steps`, `actions_trigger_label`,
  `actions_trigger_set`, `actions_recording`, `actions_running`,
  `actions_autorun`, `actions_capturing`.

### Frontend (`web/`)

- New **collapsible "Flight actions" panel** under the Simulator panel:
  step list (each with a delete button), "+ Wait / + Key / + Click" buttons,
  "Record" (toggles to "Stop"), "Run now" (toggles to "Stop" while running),
  a trigger binder (Set key / Set joystick / Clear — like PTT), and a
  "Run automatically on new flight" checkbox.
- **Push-to-talk** panel: wrap body in a `collapse-body`, head becomes a
  `collapse-head` button with a chevron (same as telemetry).
- **Live ATC** panel: two-column layout — button left, QR right with caption
  "auf Handy oder iPad"; trimmed description text. General density pass.

## Edge cases / safety

- Clicks are **absolute screen coordinates**, meant for the same monitor setup
  that recorded them — surfaced as a UI hint.
- Auto-run only when enabled, chain non-empty, never twice per session.
- macOS needs Input Monitoring **and** Accessibility permission (same as PTT) —
  reuse the existing permission hint.
- `wait` seconds clamped to a sane range; chain length unbounded but practical.

## Testing

`pytest` in `tests/` (sending is mocked — the dev machine is macOS and cannot
run MSFS / real input):
- Step validation / normalisation.
- Recording → steps conversion with injected timestamps (gaps → `wait`).
- Auto-trigger edge logic: exactly one fire per session, re-arm on disconnect.
- Config persistence round-trip (`actions_*` keys).

## Out of scope

- Multiple named macros / macro library.
- Joystick *output* (vJoy/ViGEm).
- Per-aircraft or per-airport conditional chains.
- The separate `pm.vue` recording-trigger bug in `~/html/OpenSquawk/`
  (tracked separately).
