"""Nonblocking, process-owned claims on stable local lock files."""

import errno
import math
import os
import stat
import sys
from pathlib import Path
from time import monotonic, sleep
from types import TracebackType
from typing import Self

if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl


class ResourceClaimConflict(BlockingIOError):
    """Another open claim holds the requested resource lock."""


class ResourceClaim:
    def __init__(self, path: Path, *, timeout: float = 0) -> None:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError('claim timeout must be finite and nonnegative')
        self.path = path
        self.timeout = timeout
        self._descriptor: int | None = None

    def acquire(self) -> Self:
        if self._descriptor is not None:
            raise RuntimeError('Resource claim is already acquired')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        acquired = False
        deadline = monotonic() + self.timeout
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError('Resource claim requires a regular lock file')
            while True:
                try:
                    if sys.platform == 'win32':
                        # Locking beyond EOF is supported without writing data.
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if (remaining := deadline - monotonic()) <= 0:
                        raise ResourceClaimConflict(
                            error.errno, 'Resource is already claimed', str(self.path)
                        ) from error
                    sleep(min(remaining, 0.01))
            opened = os.fstat(descriptor)
            current = self.path.stat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise OSError('Resource claim path changed during acquisition')
            self._descriptor = descriptor
            acquired = True
            return self
        finally:
            if not acquired:
                os.close(descriptor)

    def release(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            os.close(descriptor)

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
