"""Shared private scratch root for the test suites.

Every suite works inside ``data/tests/<prefix>-<random>`` and removes its own
tree when it finishes. A suite that is killed or crashes cannot, so the roots
accumulate; this sweeps what earlier runs left behind. Puppy itself never
touches this directory - it is the harness's own state, and only the harness
can tell a stale root from one a concurrent run is using.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import time

BASE = Path(__file__).resolve().parent.parent
PRIVATE_TESTS = BASE / "data" / "tests"
# Generous enough that a long suite running beside this one is never a
# candidate, short enough that leftovers do not outlive a day of work.
STALE_AFTER_SECONDS = 6 * 60 * 60


def sweep_stale(now=None) -> int:
    """Remove scratch roots older than STALE_AFTER_SECONDS."""
    cutoff = (time.time() if now is None else now) - STALE_AFTER_SECONDS
    removed = 0
    try:
        children = sorted(PRIVATE_TESTS.iterdir())
    except OSError:
        return 0
    for child in children:
        try:
            if child.is_symlink() or not child.is_dir() or \
                    child.lstat().st_mtime > cutoff:
                continue
            shutil.rmtree(str(child))
            removed += 1
        except OSError:
            continue   # a run still holding it, or not ours to remove
    return removed


def private_root(prefix: str) -> Path:
    """Create this run's scratch root, sweeping whatever earlier runs left."""
    PRIVATE_TESTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE_TESTS.chmod(0o700)
    sweep_stale()
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(PRIVATE_TESTS)))
