"""Crash-safe recovery state machine, persisted atomically on shared NFS.

Each phase is committed to ``/home/cdsw/recovery_state.json`` (tmp + os.replace,
atomic on POSIX/NFS) *before* the next destructive action, so a watchdog/job
that dies mid-recovery resumes from the last committed phase and never repeats
an irreversible step (e.g. a second head restart). A lock file guards against
two recovery runs racing.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Ordered phases. Index is used to reason about resume points.
PHASES = [
    "DETECTED",            # recovery decided; nothing destructive done yet
    "RESTARTING_HEAD",     # restart_application(head) issued
    "WAITING_HEAD",        # polling head /api/health + gcs-address
    "UPDATING_INFO",       # rewriting ray_cluster_info.json head_address
    "REBUILDING_WORKERS",  # deleting stale workers + recreating
    "REDEPLOYING",         # re-issuing Serve deployments from the store
    "COMPLETE",            # done; state file cleared
]

_STATE_PATH = Path("/home/cdsw/recovery_state.json")
_LOCK_PATH = Path("/home/cdsw/.recovery_lock")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecoveryState:
    """Persisted phase + scratch data for one recovery run."""

    def __init__(self, path: Path = _STATE_PATH, lock_path: Path = _LOCK_PATH):
        self._path = path
        self._lock_path = lock_path

    # ── State ────────────────────────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Failed to load recovery state: %s", e)
        return {}

    def phase(self) -> str | None:
        return self.load().get("phase")

    def set_phase(self, phase: str, **extra: Any) -> None:
        """Atomically checkpoint the current phase (+ optional scratch data)."""
        if phase not in PHASES:
            raise ValueError(f"unknown recovery phase: {phase}")
        data = self.load()
        data["phase"] = phase
        data["updated_at"] = _now()
        data.setdefault("started_at", _now())
        data.update(extra)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self._path)
        logger.info("Recovery phase -> %s", phase)

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except Exception as e:
            logger.error("Failed to clear recovery state: %s", e)

    def is_resumable(self) -> bool:
        """True if a prior run left an incomplete state to resume."""
        ph = self.phase()
        return ph is not None and ph != "COMPLETE"

    # ── Lock ───────────────────────────────────────────────────────────────

    def acquire_lock(self, owner: str, stale_after_s: float = 1800.0) -> bool:
        """Take the recovery lock. Returns False if another live run holds it.

        A lock older than ``stale_after_s`` is treated as abandoned (the prior
        runner died) and reclaimed.
        """
        if self._lock_path.exists():
            try:
                held = json.loads(self._lock_path.read_text())
                ts = datetime.fromisoformat(held.get("at"))
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                if age < stale_after_s:
                    logger.warning("Recovery lock held by %s (age %.0fs)", held.get("owner"), age)
                    return False
                logger.warning("Reclaiming stale recovery lock (age %.0fs)", age)
            except Exception:
                logger.warning("Unreadable recovery lock; reclaiming")
        tmp = self._lock_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"owner": owner, "at": _now()}))
        os.replace(tmp, self._lock_path)
        return True

    def release_lock(self) -> None:
        try:
            self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass
