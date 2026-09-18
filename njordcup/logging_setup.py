"""CLI-scoped stderr logging; library callers retain control of their own handlers."""
from contextlib import contextmanager
import logging
import sys


@contextmanager
def logging_session():
    logger = logging.getLogger('njordcup')
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(message)s', datefmt='%H:%M:%S'))
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield
    finally:
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate
        handler.close()
