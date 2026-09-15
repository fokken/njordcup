"""Cooperative cancellation and a shared deadline for a review invocation."""
from contextlib import contextmanager
import signal
import threading
import time

from .errors import RunStopped


class RunControl:
    def __init__(self, max_seconds=None):
        self.deadline = time.monotonic() + max_seconds if max_seconds is not None else None
        self.cancelled = threading.Event()
        self.cancel_reason = "cancelled"
        self.prompting = False

    def cancel(self, reason="cancelled"):
        self.cancel_reason = reason
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise RunStopped(self.cancel_reason, "Audit cancelled; saved checkpoints can be resumed")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RunStopped("deadline", "Audit time budget exhausted; saved checkpoints can be resumed")

    def timeout(self, requested):
        self.check()
        return min(requested, max(0.001, self.deadline - time.monotonic())) if self.deadline is not None else requested

    def wait(self, seconds):
        self.cancelled.wait(self.timeout(seconds))
        self.check()


@contextmanager
def handle_signals(control):
    """Signal handlers only set state; they never interrupt an atomic memory write."""
    previous = {}
    if threading.current_thread() is threading.main_thread():
        def stop(signum, frame):
            control.cancel("sigterm" if signum == signal.SIGTERM else "cancelled")
            if control.prompting:
                control.check()
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
