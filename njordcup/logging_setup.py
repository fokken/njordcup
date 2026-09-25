"""CLI-scoped stderr logging; library callers retain control of their own handlers."""
from contextlib import contextmanager
import logging
import sys
import os
import stat

from .errors import ReviewError


class TeeStderr:
    """Include ordinary CLI diagnostics and finding notifications, not just log records."""
    def __init__(self, console, file):
        self.console, self.file = console, file

    def write(self, text):
        self.file.write(text)
        self.file.flush()
        return self.console.write(text)

    def flush(self):
        self.file.flush()
        self.console.flush()

    def __getattr__(self, name):
        return getattr(self.console, name)


@contextmanager
def logging_session():
    logger = logging.getLogger('njordcup')
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(message)s', datefmt='%H:%M:%S'))
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    original_stderr, logfile = sys.stderr, None

    def attach_file(path):
        nonlocal logfile
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ReviewError('--log-file must be a regular file without hard links')
            os.fchmod(fd, 0o600)
            logfile = os.fdopen(fd, 'a', encoding='utf-8')
        except BaseException:
            os.close(fd)
            raise
        tee = TeeStderr(original_stderr, logfile)
        handler.setStream(tee)
        sys.stderr = tee

    try:
        yield attach_file
    finally:
        try:
            handler.flush()
        finally:
            sys.stderr = original_stderr
            logger.handlers = old_handlers
            logger.setLevel(old_level)
            logger.propagate = old_propagate
            handler.close()
            if logfile:
                logfile.close()
