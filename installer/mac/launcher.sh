#!/bin/bash
# OpenSquawk Bridge — macOS thin launcher.
#
# This is the executable inside "OpenSquawk Bridge.app". It carries no Python
# and no app code: on first launch it downloads `uv` (Astral's static Python
# manager), then hands off to bootstrap.py, which fetches the latest source
# from GitHub, builds an environment, and starts the app.
#
# Everything the app needs lives under Application Support, so the .app itself
# never has to be rebuilt when the app code changes — a `git push` is enough.

set -u

APP_NAME="OpenSquawk Bridge"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
BIN="$SUPPORT/bin"
UV="$BIN/uv"
# Resources sit next to MacOS/ inside the bundle: .../Contents/MacOS/launcher
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"

mkdir -p "$BIN"

# Log everything; the GUI has no console once it detaches.
exec >>"$SUPPORT/launcher.log" 2>&1
echo "=== launch $(date '+%Y-%m-%d %H:%M:%S') ==="

notify() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"$APP_NAME\"" >/dev/null 2>&1 || true
}

if [ ! -x "$UV" ]; then
    notify "Setting up… first launch may take a minute."
    echo "installing uv into $BIN"
    if ! curl -LsSf https://astral.sh/uv/install.sh \
            | env UV_INSTALL_DIR="$BIN" INSTALLER_NO_MODIFY_PATH=1 sh; then
        notify "Setup failed. Check your internet connection and try again."
        echo "uv install failed"
        exit 1
    fi
fi

if [ ! -x "$UV" ]; then
    notify "Setup failed: uv not found after install."
    echo "uv missing after install"
    exit 1
fi

# `uv run` downloads a managed CPython 3.12 on first use and caches it.
# --no-project: bootstrap.py is a standalone stdlib script, not part of a project.
export UV_INSTALL_DIR="$BIN"
# Keep the managed Python inside our own Application Support folder too, so
# "delete this folder to reset the install" (see README) is actually true.
export UV_PYTHON_INSTALL_DIR="$SUPPORT/python"

# Pre-install the managed Python as its own step (instead of letting the final
# `uv run` download it lazily) so we can strip quarantine from it below before
# it is ever executed.
"$UV" python install 3.12 >/dev/null 2>&1 || true

# This app bundle is unsigned and quarantined by macOS after download. Files a
# quarantined process downloads/creates (the uv binary via curl above, the
# managed Python just installed) inherit that same quarantine flag. Left in
# place, Gatekeeper blocks running them and shows a SECOND "Open Anyway"
# prompt — for a bare CLI binary, so it has no bundle name/icon and just reads
# "uv". Approving the app once is meant to be the only prompt (see README), so
# clear quarantine from everything under our own support dir before running it.
xattr -dr com.apple.quarantine "$SUPPORT" 2>/dev/null || true

exec "$UV" run --python 3.12 --no-project "$RES/bootstrap.py"
