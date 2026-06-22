# Quicksave / Quickload des Flugzeugzustands — Design

Datum: 2026-06-22

## Ziel

Eine Action, die den aktuellen Flugzustand (Position, Höhe, Attitude, kompletter
Geschwindigkeitsvektor, Schub, Flaps/Spoiler/Gear) **speichert** und auf Knopfdruck
wieder **zurückschreibt** — ohne Reload, im selben Flug. Use-Case: kurz vor dem
Aufsetzen speichern, danach beliebig oft zum selben Punkt zurückspringen, um den
Flare zu üben.

## Entscheidungen (aus Brainstorming)

- **Ein Quicksave-Slot** (kein Mehrfach-Slot-Modell).
- **Nur Session/RAM** — überlebt keinen App-Neustart.
- **Voller Bewegungszustand** beim Laden: Position, Höhe, Attitude (Pitch/Bank/
  Heading), kompletter Velocity-Vektor inkl. Sink-/Steigrate + Rotationsraten,
  Schub/N1, plus Flaps/Spoiler/Gear. Damit fliegt der Anflug nach dem Laden
  flüssig weiter statt abzusacken.

## Ansatz

Direktes SimVar-Setzen via Python-SimConnect `.set()` auf settable Vars. Reused die
bestehende `MsfsSource`, kein Reload. (Verworfen: Slew/Freeze-Teleport — mehr
Komplexität ohne Gewinn; sim-eigenes Quicksave — nicht granular, braucht Reload.)

**Caveat:** Direktes Setzen funktioniert auf dem Standard-Sim zuverlässig. Bei
komplexen Add-ons (Fenix A320, FBW) können Flugzeug-Systeme einzelne Writes
überschreiben. Final nur auf Windows verifizierbar. Die settable-Var-Liste liegt
darum an einer Stelle, leicht justierbar.

## Komponenten

### 1. `MsfsSource` — Lesen + Schreiben des State (`msfs_source.py`)

Eigene `_SETTABLE_KEYS`-Map (getrennt von der Telemetrie-`_SIMVAR_KEYS`):

- Position: `PLANE_LATITUDE`, `PLANE_LONGITUDE`, `PLANE_ALTITUDE`
- Attitude: `PLANE_PITCH_DEGREES`, `PLANE_BANK_DEGREES`, `PLANE_HEADING_DEGREES_TRUE`
- Velocity: `VELOCITY_BODY_X/Y/Z`, `ROTATION_VELOCITY_BODY_X/Y/Z`
- Konfiguration: `FLAPS_HANDLE_INDEX`, `GEAR_HANDLE_POSITION`, `SPOILERS_HANDLE_POSITION`
- Schub: `GENERAL_ENG_THROTTLE_LEVER_POSITION:1`, `:2`

Neue Methoden:

- `read_state() -> dict | None` — liest den settable-Subset; `None` wenn nicht verbunden.
- `write_state(snap: dict) -> None` — setzt die Vars via `aq.set(...)`.

`DummyFlight` / „(None)"-Quelle: `read_state()` → `None`, `write_state` = No-op (mit Log).

### 2. Neue Step-Typen (`actions.py`)

- `{"type": "save_state"}` und `{"type": "load_state"}` in `normalize_step`/`normalize_steps`.
- `run_steps(steps, backend, should_stop=None, sim=None)` — neuer optionaler `sim`-Adapter:
  - `sim.save()` — liest den State und merkt ihn sich (RAM-Slot).
  - `sim.load()` — schreibt den gemerkten State zurück; No-op wenn nichts gespeichert.
- Reine Logik, mit `FakeSim` unit-testbar.

### 3. Quicksave-Halter (`bridge_app.py`)

- `self._quicksave: dict | None` — ein RAM-Slot.
- Kleiner Adapter, der `self.source.read_state/write_state` + den Halter bündelt und
  an `run_steps(..., sim=adapter)` durchgereicht wird.
- Nutzung über die bestehende Chain-/Trigger-Infrastruktur: eine Chain `[save_state]`
  auf einen Joystick-Knopf, eine Chain `[load_state]` auf einen anderen. Kein neues
  Trigger-Konzept.

### 4. UI (`web/index.html`, `web/app.js`)

- Zwei neue Tiles neben „Wait/Click": **Save state**, **Load state**.
- `STEP_ICON` + `prettyStep`: „Save aircraft state" / „Load aircraft state".

## Datenfluss

```
Joystick-Knopf A → Chain [save_state] → run_steps(sim=adapter)
    → adapter.save() → MsfsSource.read_state() → RAM-Slot

Joystick-Knopf B → Chain [load_state] → run_steps(sim=adapter)
    → adapter.load() → MsfsSource.write_state(RAM-Slot)
```

## Fehlerbehandlung

- Nicht verbunden / keine Sim-Quelle: save liest `None` (nichts gemerkt), load = No-op,
  jeweils geloggt — kein Crash.
- load ohne vorheriges save: No-op.
- Schreibfehler (`aq.set` wirft): gefangen + geloggt wie bestehende Action-Fehler.

## Tests

- `normalize_step` für `save_state`/`load_state`.
- `run_steps` mit `FakeSim`: save merkt sich Snapshot, load schreibt ihn zurück,
  load-ohne-save = No-op.
- Alles ohne Sim/Windows lauffähig (Dev-Mac).

## Verifikation

- Pure-Logik-Tests laufen auf dem Mac.
- Echtes Setzen in MSFS auf Windows: Checkliste — Anflug speichern, durchsacken
  lassen, laden, prüfen ob Position/Speed/Sinkrate/Config zurückgesetzt sind; mit
  Standard-A320neo (Asobo) zuerst, dann Fenix/FBW gegentesten.
