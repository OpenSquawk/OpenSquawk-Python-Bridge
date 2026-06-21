# Multi-chain flight actions with event triggers — design

2026-06-21

## Goal

Turn the single flight-action chain into **multiple chains shown as tabs**, each
bound to exactly one trigger. Add four new event-trigger hooks alongside the
existing key/joystick hotkey, and gate all event triggers behind one global
cooldown so two scenarios can't fire on top of each other.

Decisions (confirmed with user):

- **1 tab = 1 chain = 1 trigger.** Several chains may share the same hook type;
  when that hook fires, all bound+enabled chains run sequentially as one scenario.
- **Fresh start** — the old single chain / autorun / trigger config is dropped,
  not migrated.
- **Only event triggers are gated** by the cooldown. The manual "Run now" button
  always fires immediately and does not arm the cooldown.

## Trigger types

A chain's `trigger` is one of:

- `{"type": "hook", "hook": "app_start"}` — once, shortly after the app starts
- `{"type": "hook", "hook": "sim"}`      — simulator detected (first connected sample)
- `{"type": "hook", "hook": "aircraft"}` — aircraft detected (`aircraft` becomes non-None)
- `{"type": "hook", "hook": "gps_jump"}` — GPS + altitude jump (see below)
- `{"type": "keys", "keys": [...]}`      — keyboard combo (unchanged)
- `{"type": "joy", "joy": "...", "button": N}` — joystick button (unchanged)
- `null` — unbound (chain never auto-fires; still runnable via "Run now")

`sim` / `aircraft` / `gps_jump` are session-scoped: each fires once per flight
session and re-arms when the session drops (disconnect / source switch / logout).
`app_start` fires once per process.

## Data model

`config.json` replaces `actions_steps` / `actions_trigger` / `actions_autorun`
with one list:

```json
"actions_chains": [
  {
    "id": "c1",                       // stable id
    "name": "On aircraft detected",   // default = trigger label, user-editable
    "enabled": true,
    "trigger": { "type": "hook", "hook": "aircraft" },
    "steps": [ { "type": "wait", "seconds": 1 }, ... ]
  }
]
```

`actions.py` gains `normalize_chain` / `normalize_chains` (id/name/enabled/
trigger/steps validation, reusing `normalize_steps`). The step model and replay
engine are unchanged.

## GPS / altitude jump detection (pure function in actions.py)

`is_gps_jump(prev, cur)` where each arg is `(lat, lon, alt_ft)` or `None`.
Returns False if either is `None` (first sample).

- **GPS condition** (true if any):
  - one point out of range and the other in range — a null↔real transition
    (out of range = `lat==0 and lon==0`, or `|lat|>90`, or `|lon|>180`)
  - both in range and great-circle distance ≥ 50 km
- **Altitude condition** (true if any):
  - `|alt_cur - alt_prev| ≥ 1000 ft`
  - both altitudes < 1000 ft
- **Jump** = GPS condition AND altitude condition.

Constants: `GPS_JUMP_KM = 50.0`, `ALT_JUMP_FT = 1000.0`. Distance via haversine.

## Runtime (bridge_app.py)

State: `actions_chains`, `actions_active_id` (UI tab), `_actions_running`,
`_cooldown_until`, per-session flags `_sim_fired` / `_aircraft_fired`,
`_prev_gps`, per-chain hotkey edge map `_combo_down`, recording target
`_recording_chain_id`, capture target carried in `_capturing["target"]`
(`"ptt"` or a chain id).

**Global gate** — `_try_event_fire(reason, hook)`:
1. collect enabled chains whose trigger matches `hook` (or matches the hotkey id)
2. log `[trigger] <hook> erkannt` always
3. under lock: if `_actions_running` or `now < _cooldown_until`, log
   `… unterdrückt (cooldown)` and return; else set `_actions_running = True`
4. spawn `_run_chains(reason, chains)`: run each chain's steps in order
   (shared backend, `should_stop = not _actions_running`); in `finally` set
   `_cooldown_until = now + 10` and `_actions_running = False`

**Hook sources:**
- `app_start`: a short delayed thread from `__init__` calls `_try_event_fire`
- `sim` / `aircraft` / `gps_jump`: evaluated in `_eval_flight_hooks(connected,
  aircraft, sample)` from `_tick_stream`, replacing `_maybe_autorun`. Resets
  `_sim_fired` / `_aircraft_fired` / `_prev_gps` when the session drops.
- `keys` / `joy`: `_eval_key_trigger` / `_on_joy_button` iterate hotkey-bound
  chains with per-chain edge detection, then call `_try_event_fire`.

**Run now** (`actions_run_now(chain_id)`): rejects if `_actions_running`;
otherwise runs that one chain without arming the cooldown.

## API (exposed to JS)

`actions_add_chain`, `actions_remove_chain(id)`, `actions_rename_chain(id, name)`,
`actions_set_enabled(id, on)`, `actions_set_trigger_hook(id, hook)`,
`actions_capture_trigger(id, kind)`, `actions_clear_trigger(id)`,
`actions_add_step(id, step)`, `actions_remove_step(id, index)`,
`actions_clear_steps(id)`, `actions_record_start(id)` / `actions_record_stop(id)`,
`actions_run_now(id)`, `actions_stop()`, `actions_set_active(id)`.
`get_state()` returns `actions_chains`, `actions_active_id`, `actions_running`,
`actions_recording_id`, `actions_capturing` (kind for the active chain),
`actions_backend_ok`, and a per-chain `trigger_label`.

## UI (web/index.html, app.js, style.css)

Tab strip in the "Flight actions" panel: one tab per chain + "＋". The active
tab shows: editable name, enable toggle, a trigger selector (dropdown of the 4
hooks **or** Set key / Set joystick), the step list with +Wait/+Click/Record/
Run now/Clear (as today), and a delete-chain button. Panel header tag reflects
the aggregate state (RUN / REC / ARMED / OFF).

## Testing

- `actions.py`: `is_gps_jump` edge cases (50 km boundary, null↔real transition,
  altitude OR branch, both-low-altitude on-ground teleport), `normalize_chain`.
- `bridge_app.py`: cooldown gate fires once then suppresses within the window;
  session re-arming for sim/aircraft; multiple chains on one hook run in order;
  "Run now" bypasses the cooldown. Rewrites `test_actions_autotrigger.py` and
  `test_actions_api.py` onto the chain model. Existing runner/record/steps tests
  are unaffected.
