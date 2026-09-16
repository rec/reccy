"""Retry timing without executing operations, sleeping or starting threads."""

import math
import time
from collections.abc import Callable
from enum import StrEnum, auto

from pydantic import BaseModel, Field


class RetryPolicy(BaseModel, frozen=True):
    attempts: int | None = Field(default=None, gt=0)
    delay: float = Field(default=1, ge=0, allow_inf_nan=False)
    backoff: float = Field(default=1, ge=1, allow_inf_nan=False)
    backoff_after: int = Field(default=1, ge=1)
    max_delay: float = Field(default=60, ge=0, allow_inf_nan=False)


class RetryStopReason(StrEnum):
    cancelled = auto()
    exhausted = auto()
    deadline = auto()


class RetrySchedule:
    def __init__(
        self,
        policy: RetryPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        deadline: float | None = None,
    ) -> None:
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError('deadline must be finite')
        self.policy = policy
        self.clock = clock
        self.deadline = deadline
        self.attempt_count = 0
        self._cancelled = False
        self._running = False
        self._delay = min(policy.delay, policy.max_delay)
        self._next_attempt = clock()

    @property
    def stop_reason(self) -> RetryStopReason | None:
        if self._cancelled:
            return RetryStopReason.cancelled
        if self.deadline is not None and self.clock() >= self.deadline:
            return RetryStopReason.deadline
        if (
            not self._running
            and self.policy.attempts is not None
            and self.attempt_count >= self.policy.attempts
        ):
            return RetryStopReason.exhausted
        return None

    def seconds_until_attempt(self) -> float | None:
        """Return zero when ready, a wait duration, or None when stopped."""
        if self._running:
            raise RuntimeError('Finish the current attempt before scheduling another')
        if self.stop_reason is not None:
            return None
        now = self.clock()
        next_attempt = self._next_attempt
        if self.deadline is not None:
            next_attempt = min(next_attempt, self.deadline)
        return max(0.0, next_attempt - now)

    def begin_attempt(self) -> bool:
        """Count an attempt only when ready; callers execute the operation."""
        if self.seconds_until_attempt() != 0:
            return False
        # Recheck the deadline in case the clock advanced during readiness checks.
        if self.stop_reason is not None:
            return False
        self.attempt_count += 1
        self._running = True
        return True

    def failed(self) -> None:
        """Schedule another attempt after a caller-classified retryable failure."""
        if not self._running:
            raise RuntimeError('No attempt is running')
        self._running = False
        self._next_attempt = self.clock() + self._delay
        if self.attempt_count >= self.policy.backoff_after:
            self._delay = min(self._delay * self.policy.backoff, self.policy.max_delay)

    def cancel(self) -> None:
        self._cancelled = True

    def reset(self) -> None:
        """Start a new recovery cycle, keeping the original absolute deadline."""
        self.attempt_count = 0
        self._running = False
        self._cancelled = False
        self._delay = min(self.policy.delay, self.policy.max_delay)
        self._next_attempt = self.clock()
