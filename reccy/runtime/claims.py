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
    """Claim a resource; timeout=None waits until it becomes available."""

    def __init__(
        self, path: Path, *, timeout: float | None = 0, shared: bool = False
    ) -> None:
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError('claim timeout must be finite and nonnegative, or None')
        self.path = path
        self.timeout = timeout
        self.shared = shared
        self._descriptor: int | None = None

    def acquire(self) -> Self:
        if self._descriptor is not None:
            raise RuntimeError('Resource claim is already acquired')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        acquired = False
        deadline = None if self.timeout is None else monotonic() + self.timeout
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError('Resource claim requires a regular lock file')
            while True:
                try:
                    if sys.platform == 'win32':
                        # Locking beyond EOF is supported without writing data.
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        mode = msvcrt.LK_NBRLCK if self.shared else msvcrt.LK_NBLCK
                        msvcrt.locking(descriptor, mode, 1)
                    else:
                        mode = fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX
                        fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    remaining = None if deadline is None else deadline - monotonic()
                    if remaining is not None and remaining <= 0:
                        raise ResourceClaimConflict(
                            error.errno, 'Resource is already claimed', str(self.path)
                        ) from error
                    sleep(0.01 if remaining is None else min(remaining, 0.01))
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
            try:
                if sys.platform == 'win32':
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
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
