"""Cheap, cached detection of running simulator processes.

Used by the setup dialog and the auto-select loop to tell whether a sim is
running. Scanning shells out (`tasklist` on Windows, `pgrep` elsewhere), so the
result is cached briefly — the UI polls detection at a few hertz and we don't
want a subprocess per poll.

No third-party dependency (psutil is not bundled): the two native tools are
present on every target OS.
"""

from __future__ import annotations

import subprocess
import sys
import time

_CACHE: dict[str, tuple[float, bool]] = {}
_TTL = 2.0  # seconds a detection result stays fresh


def process_running(patterns: tuple[str, ...] | list[str], ttl: float = _TTL) -> bool:
    """True if any process whose command line contains one of `patterns` runs.

    Case-insensitive substring match. Result is cached for `ttl` seconds keyed
    by the pattern set, so rapid repeated calls collapse to one scan.
    """
    key = "|".join(patterns)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    found = _scan(patterns)
    _CACHE[key] = (now, found)
    return found


def _scan(patterns: tuple[str, ...] | list[str]) -> bool:
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["tasklist"], capture_output=True, text=True, timeout=4,
            ).stdout.lower()
            return any(p.lower() in out for p in patterns)
        # macOS / Linux: pgrep -f matches against the full command line, -i is
        # case-insensitive. returncode 0 means at least one match.
        for p in patterns:
            r = subprocess.run(
                ["pgrep", "-fi", p], capture_output=True, text=True, timeout=4,
            )
            if r.returncode == 0 and r.stdout.strip():
                return True
        return False
    except Exception:
        return False
