"""Bounded publication of replaceable snapshots outside an owner's work loop."""

import logging
import threading
from collections.abc import Callable

from ..errors import ReccyError

LOGGER = logging.getLogger(__name__)


class LatestPublisher[T]:
    """Keep at most one waiting snapshot; a blocked writer never blocks publish.

    close() drains the last snapshot within its deadline, returning False if the
    writer remains blocked. The daemon worker owns all calls to the writer.
    Durable events must not use this lossy publisher.
    """

    def __init__(self, write: Callable[[T], None], *, name: str) -> None:
        self.write = write
        self.error: OSError | ReccyError | None = None
        self.pending: T | None = None
        self.closed = False
        self.condition = threading.Condition()
        self.thread = threading.Thread(target=self._run, daemon=True, name=name)

    def publish(self, value: T) -> None:
        with self.condition:
            if not self.closed:
                if self.thread.ident is None:
                    self.thread.start()
                self.pending = value
                self.condition.notify()

    def close(self, timeout: float = 1.0) -> bool:
        with self.condition:
            self.closed = True
            self.condition.notify()
        if self.thread.ident is not None:
            self.thread.join(timeout)
        return not self.thread.is_alive()

    def _run(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                value, self.pending = self.pending, None
                if value is None and self.closed:
                    return
            if value is not None:
                try:
                    self.write(value)
                except (OSError, ReccyError) as error:
                    with self.condition:
                        self.error = error
                        self.closed = True
                        self.pending = None
                    LOGGER.error('Snapshot publication failed: %s', error)
                    return
