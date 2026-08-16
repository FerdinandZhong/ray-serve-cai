"""Head-node recovery for the CML-hosted Ray cluster.

The management API runs *on* the head, so it can't recover itself — recovery
runs from a separate CML **Job** pod (see ``scripts/recover_head.py``). The
:class:`~ray_serve_cai.recovery.recover.RecoveryOrchestrator` drives a
crash-safe state machine (:mod:`ray_serve_cai.recovery.recovery_state`) that
restarts the head, waits for it, refreshes the persisted head address, rebuilds
workers, and redeploys models from the :class:`DeploymentStore`.
"""

from .recover import RecoveryOrchestrator
from .recovery_state import PHASES, RecoveryState

__all__ = ["PHASES", "RecoveryState", "RecoveryOrchestrator"]
