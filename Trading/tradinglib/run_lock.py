"""Inter-process locks for long-running batch jobs (asset_perf2).

Uses OS file locks (fcntl.flock on Linux/macOS, msvcrt.locking on Windows):
the lock is released by the operating system when the holder dies, so a
killed or crashed run never leaves a stale lock behind. The lock file only
carries the holder's PID and a description for the log message of whoever
finds it taken.
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt


class RunLock:
    """Exclusive lock on a file; acquire(blocking=False) returns False if taken."""

    def __init__(self, path, label=""):
        self.path = path
        self.label = label
        self._fh = None

    def _try_lock(self, fh) -> bool:
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def holder(self) -> str:
        """PID and label written by the current holder ('' if unknown)."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def acquire(self, blocking=False, poll=5.0, on_wait=None) -> bool:
        """Take the lock. blocking=True waits until it is free (polling every *poll* s)."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        waited = False
        while not self._try_lock(fh):
            if not blocking:
                fh.close()
                return False
            if not waited and on_wait:
                on_wait(self.holder())
            waited = True
            time.sleep(poll)
        # Byte 0 is the lock region on Windows: write the info after it.
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()} {self.label}".strip() + "\n")
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            else:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        self.acquire(blocking=True)
        return self

    def __exit__(self, *exc):
        self.release()
