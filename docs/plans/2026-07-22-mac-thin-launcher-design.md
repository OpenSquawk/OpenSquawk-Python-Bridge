# macOS Thin Launcher + Auto-Updater — Design

Date: 2026-07-22
Repo: OpenSquawk/OpenSquawk-Python-Bridge

## Goal

Users download **one file** for macOS, double-click, and the app installs
itself and runs. It then keeps itself up to date from GitHub automatically.
Maintainers never rebuild binaries — they push source (and optionally cut a
release). No code signing / notarization.

## Approach: Thin Launcher + Source

The distributed artifact is a tiny, version-independent launcher `.app`. It
contains **no Python and no app code**. On every launch it fetches the latest
source from GitHub, prepares an isolated Python environment, and runs the app.

Because the launcher never carries app code, it almost never changes. All real
updates flow through the GitHub source — a `git push` (or a `gh release create`)
reaches every user on their next launch.

### Runtime layout (per user)

```
~/Library/Application Support/OpenSquawk Bridge/
  bin/uv              # Astral uv, a single static binary (Python + venv manager)
  src/                # extracted source of the app (bridge_app.py, web/, …)
  venv/               # dependencies, built with `uv pip install`
  state.json          # installed ref + requirements hash
  launcher.log        # bootstrap log
```

### Launch flow

```
OpenSquawk Bridge.app/Contents/MacOS/launcher   (bash, the bundle executable)
  │  first run only: download uv into bin/uv (via astral install.sh)
  ▼
uv run --python 3.12 --no-project  Resources/bootstrap.py   (stdlib only)
  │  1. resolve latest ref (GitHub Releases → fallback: main branch)
  │  2. if newer or src missing: download tarball, extract, swap src/ atomically
  │  3. if venv missing or requirements changed: uv venv + uv pip install
  │  4. write state.json
  ▼
exec venv/bin/python src/bridge_app.py   (cwd = src/)
```

Offline behaviour: if the GitHub check fails but `src/` and `venv/` already
exist, the launcher skips the update and starts the installed version. Only a
first run with no network fails (with a notification).

### Update channel

`bootstrap.py` first queries `GET /repos/{repo}/releases/latest`. If a release
exists, its `tag_name` is the version and its source tarball is downloaded.
If there is **no** published release (current state of the repo), it falls back
to the `main` branch HEAD commit — so the launcher works today with zero
releases, and automatically switches to release-gated updates once the
maintainer starts tagging.

The installed identifier (release tag or commit sha) is stored in `state.json`
and compared on each launch.

### User-visible feedback

The bootstrap phase can take ~30–90 s on the very first launch (uv + Python +
dependency download). Milestones are surfaced with macOS notifications
(`osascript display notification`): "Setting up…", "Updating…", "Starting…".
Subsequent launches are fast (everything cached) and silent.

### No signing

The `.app` is unsigned. macOS quarantines it on download, so the **first**
launch needs right-click → Open (documented in the README). Everything the
launcher downloads afterwards (uv, Python, source) comes via `curl`/`urllib`,
which are not quarantined, so they run without prompts.

## Files

- `installer/mac/launcher.sh` — bundle executable; bootstraps uv, runs bootstrap.py
- `installer/mac/bootstrap.py` — stdlib-only updater + env builder + app launcher
- `installer/mac/Info.plist.in` — bundle metadata template
- `installer/build_launcher.py` — assembles `dist/OpenSquawk Bridge.app` and a
  `.dmg` (run once on macOS; not per app-version)
- README: "Download & Install (macOS)" + a build note

## Out of scope (for now)

- Windows / Linux launchers (same pattern later; Mac first).
- In-app "check for updates" UI (updates happen at launch).
- Auto-update for the OS autostart entry (autostart runs the installed src
  directly and updates on the next normal launch).
- Code signing / notarization (explicitly not wanted).
```

