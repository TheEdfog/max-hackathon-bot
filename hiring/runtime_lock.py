"""OS lock prevents two local polling processes from consuming the same updates."""
from contextlib import contextmanager
from pathlib import Path
import os
from sqlalchemy.engine import make_url


@contextmanager
def polling_lock(database_url):
    source = make_url(database_url)
    if source.get_backend_name() != 'sqlite' or not source.database or source.database == ':memory:':
        raise ValueError('Polling requires a persistent SQLite database')
    path = Path(source.database).resolve().with_suffix('.polling.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    locked = False
    try:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError('Polling is already running for this database') from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()  # OS releases the lock on crashes too; keep the harmless file.
