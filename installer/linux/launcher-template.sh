#!/usr/bin/env bash
# OpenSquawk Bridge — Linux launcher (self-updating).
#
# A single downloadable file. On first run it downloads `uv` (Astral's static
# Python manager), fetches the latest source from GitHub, builds an isolated
# environment (with a self-contained Qt webview backend — no `apt install`
# needed), registers a menu entry, and starts the app. Every launch checks
# GitHub and updates itself, so you always run the latest version.
#
# Usage:  chmod +x OpenSquawk-Bridge-linux.sh && ./OpenSquawk-Bridge-linux.sh
#
# bootstrap.py is embedded below at build time (installer/build_launcher.py);
# the launcher itself carries no app code and rarely changes.

set -u

APP_NAME="OpenSquawk Bridge"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_NAME"
BIN="$DATA/bin"
UV="$BIN/uv"
BOOT="$DATA/bootstrap.py"

mkdir -p "$BIN"
LOG="$DATA/launcher.log"
exec >>"$LOG" 2>&1
echo "=== launch $(date '+%Y-%m-%d %H:%M:%S') ==="

notify() {
    command -v notify-send >/dev/null 2>&1 && notify-send "$APP_NAME" "$1" || true
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
    exit 1
fi

# Write the embedded bootstrap and hand off. Passing the launcher's own path
# lets bootstrap register a .desktop entry that re-invokes this file.
cat > "$BOOT" <<'OPENSQUAWK_BOOTSTRAP_EOF'
@BOOTSTRAP@
OPENSQUAWK_BOOTSTRAP_EOF

export UV_INSTALL_DIR="$BIN"
export OPENSQUAWK_LAUNCHER="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
exec "$UV" run --python 3.12 --no-project "$BOOT"
