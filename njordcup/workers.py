"""Bounded file jobs; only the calling thread persists audit checkpoints."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import logging
from queue import Queue, Empty
import threading

from .errors import RunStopped

log = logging.getLogger(__name__)


class RequestGate:
    """Stop new requests while allowing already-sent requests to finish."""
    def __init__(self):
        self.reason = None
        self._lock = threading.Lock()

    def stop(self, reason):
        with self._lock:
            if self.reason is None:
                self.reason = reason

    def check(self):
        with self._lock:
            if self.reason:
                raise RunStopped(self.reason, 'File review queue stopped; saved checkpoints can be resumed')


def run_files(paths, workers, provider, run_job, update):
    """Acknowledge each checkpoint before that job may send another request."""
    gate = RequestGate()
    events = Queue(maxsize=workers * 2)
    abort = threading.Event()
    pending = iter(paths)
    active = 0
    failure = None

    def send(event):
        # A bounded queue avoids retaining many snapshots if persistence is slow.
        from queue import Full
        while not abort.is_set():
            try:
                events.put(event, timeout=0.1)
                return
            except Full:
                pass
        raise RunStopped('checkpoint_error', 'Coordinator could not save worker progress')

    def job(path):
        def checkpoint(report):
            acknowledged = threading.Event()
            send(('checkpoint', path, deepcopy(report), acknowledged))
            while not acknowledged.wait(0.1):
                if abort.is_set():
                    raise RunStopped('checkpoint_error', 'Coordinator could not save worker progress')
        try:
            log.info('File worker started: %r', path)
            result = run_job(path, provider.fork(gate), checkpoint)
            send(('done', path, result, None))
        except BaseException as exc:
            gate.stop(getattr(exc, 'reason', 'worker_error'))
            if not abort.is_set():
                send(('error', path, exc, None))

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='njordcup-file') as pool:
        try:
            while True:
                try:
                    provider.control.check()
                except RunStopped as exc:
                    gate.stop(exc.reason)
                while active < workers and gate.reason is None:
                    path = next(pending, None)
                    if path is None:
                        break
                    pool.submit(job, path)
                    active += 1
                if not active:
                    break
                try:
                    kind, path, value, acknowledged = events.get(timeout=0.1)
                except Empty:
                    continue
                if kind == 'error':
                    active -= 1
                    failure = failure or value
                else:
                    update(path, value)
                    if value.get('stop_reason'):
                        gate.stop(value['stop_reason'])
                    if acknowledged:
                        acknowledged.set()
                    else:
                        log.info('File worker finished: %r (%s)', path, value['status'])
                        active -= 1
            if failure:
                raise failure
        except BaseException:
            gate.stop('worker_error')
            raise
        finally:
            abort.set()
    return gate.reason
