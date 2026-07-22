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
exec "$UV" run --python 3.12 --no-project "$RES/bootstrap.py"
