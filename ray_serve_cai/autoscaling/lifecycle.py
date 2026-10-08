"""Cross-process exclusion between worker autoscaling and head recovery."""

import fcntl
from contextlib import contextmanager


def creation_rejection(exc):
    """Classify explicit HTTP rejection only; timeouts/5xx remain ambiguous.

    Do not infer success or quota scope from free-form error text.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in {400, 401, 403, 404, 422, 429}:
        return int(status)
    return None


@contextmanager
def lifecycle_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".worker_lifecycle.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another worker lifecycle operation is in progress") from exc
        yield
