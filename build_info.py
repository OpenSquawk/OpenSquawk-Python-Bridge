"""Read the source revision displayed by the OpenSquawk Bridge UI."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / ".opensquawk-version.json"
STATE_FILE = ROOT.parent / "state.json"
REPO = "OpenSquawk/OpenSquawk-Bridge"
_lock = threading.Lock()
_refresh_started = False


def _from_metadata() -> dict[str, str | None] | None:
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        commit = data.get("commit")
        if isinstance(commit, str) and commit:
            return {
                "commit": commit[:7],
                "committed_at": data.get("committed_at") if isinstance(data.get("committed_at"), str) else None,
                "ref": data.get("ref") if isinstance(data.get("ref"), str) else None,
            }
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _from_launcher_state() -> dict[str, str | None] | None:
    """Read the old launcher's state until it has been upgraded once."""
    try:
        ref = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("ref")
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(ref, str) or ":" not in ref:
        return None
    channel, value = ref.split(":", 1)
    return {
        "commit": value[:7] if channel == "branch" else None,
        "committed_at": None,
        "ref": ref,
    }


def _from_git() -> dict[str, str | None] | None:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        committed_at = subprocess.check_output(
            ["git", "show", "-s", "--format=%cI", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return {"commit": commit or None, "committed_at": committed_at or None, "ref": "local"}
    except (OSError, subprocess.CalledProcessError):
        return None


_build_info = _from_metadata() or _from_git() or _from_launcher_state() or {
    "commit": None, "committed_at": None, "ref": None,
}


def get_build_info() -> dict[str, str | None]:
    """Return the installed revision, preferring updater-provided metadata.

    Release tarballs deliberately do not contain a ``.git`` directory.  The
    launcher writes the small metadata file alongside the source after a
    successful update, so the UI can still identify the exact installed build.
    The git fallback keeps the information useful during local development.
    """
    with _lock:
        return dict(_build_info)


def refresh_build_info_async() -> None:
    """Fill in metadata for installs made by a pre-metadata launcher.

    This is deliberately asynchronous: a failed GitHub request must never slow
    down opening the Bridge. Once resolved, the result is stored with the source
    and remains visible on later offline starts.
    """
    global _refresh_started
    with _lock:
        if _refresh_started or _build_info.get("committed_at"):
            return
        _refresh_started = True
        ref = _build_info.get("ref")
    if not isinstance(ref, str) or ":" not in ref:
        return

    def refresh() -> None:
        channel, value = ref.split(":", 1)
        if channel not in {"branch", "release"}:
            return
        try:
            request = urllib.request.Request(
                f"https://api.github.com/repos/{REPO}/commits/{value}",
                headers={"User-Agent": "OpenSquawk-Bridge"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            commit = data.get("sha")
            details = data.get("commit")
            committer = details.get("committer") if isinstance(details, dict) else None
            committed_at = committer.get("date") if isinstance(committer, dict) else None
            if not isinstance(commit, str):
                return
            resolved = {"commit": commit[:7], "committed_at": committed_at, "ref": ref}
            with _lock:
                _build_info.update(resolved)
            VERSION_FILE.write_text(json.dumps({**resolved, "commit": commit}, indent=2) + "\n", encoding="utf-8")
        except Exception:
            # The existing local revision remains useful when offline.
            return

    threading.Thread(target=refresh, daemon=True).start()
