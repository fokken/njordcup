"""Explicit opt-in JSONL traces. No HTTP headers or credentials are recorded."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from .errors import ReviewError


class TraceLog:
    def __init__(self, path):
        self.path = Path(path)
        self.session_id = uuid4().hex
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write('session_start')

    def write(self, event, **fields):
        record = {'trace_format': 'njordcup/1', 'session_id': self.session_id,
                  'time': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            fd = os.open(self.path, flags, 0o600)
            with os.fdopen(fd, 'r+', encoding='utf-8') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ReviewError('Trace destination must be a regular file without hard links')
                if info.st_size:
                    # Only append to our own trace files, never arbitrary existing data.
                    first = json.loads(handle.readline(4096))
                    if not isinstance(first, dict) or first.get('trace_format') != 'njordcup/1' or first.get('event') != 'session_start':
                        raise ReviewError('Trace destination already contains unrelated data; choose a new file')
                os.fchmod(handle.fileno(), 0o600)
                handle.seek(0, os.SEEK_END)
                handle.write(json.dumps(record, ensure_ascii=True) + '\n')
                handle.flush()
        except (OSError, ValueError) as exc:
            raise ReviewError('Cannot write request trace; check destination permissions and choose a new file if it contains unrelated data') from exc
