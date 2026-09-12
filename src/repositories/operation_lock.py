"""Cross-process lock, released by the OS on process exit (including crashes)."""
import os
from contextlib import contextmanager, closing
from pathlib import Path


@contextmanager
def operation_lock(connect):
    # Resolve the real database, including injected test connection factories.
    with closing(connect()) as db:
        path = db.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        raise RuntimeError("Payment operations require a persistent database")
    lock_path = Path(path + ".operations.lock")
    with lock_path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
