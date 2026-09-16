import pytest
from pydantic import ValidationError

from reccy.runtime.retry import RetryPolicy, RetrySchedule, RetryStopReason


class Clock:
    now: float = 0

    def __call__(self) -> float:
        return self.now


def test_bounded_backoff_and_attempt_limit() -> None:
    clock = Clock()
    schedule = RetrySchedule(
        RetryPolicy(attempts=4, delay=2, backoff=3, max_delay=5), clock=clock
    )
    for d in [2, 5, 5]:
        assert schedule.begin_attempt()
        schedule.failed()
        assert schedule.seconds_until_attempt() == d
        assert not schedule.begin_attempt()
        clock.now += d
    assert schedule.begin_attempt()
    schedule.failed()
    assert schedule.attempt_count == 4
    assert schedule.stop_reason == RetryStopReason.exhausted
    assert schedule.seconds_until_attempt() is None
    assert not schedule.begin_attempt()


def test_deadline_caps_wait_without_starting_an_extra_attempt() -> None:
    clock = Clock()
    schedule = RetrySchedule(RetryPolicy(delay=10), clock=clock, deadline=3)
    assert schedule.begin_attempt()
    schedule.failed()
    assert schedule.seconds_until_attempt() == 3
    clock.now = 3
    assert not schedule.begin_attempt()
    assert schedule.stop_reason == RetryStopReason.deadline
    schedule.reset()
    assert not schedule.begin_attempt()


def test_cancellation_and_success_reset_are_distinct_from_exhaustion() -> None:
    clock = Clock()
    schedule = RetrySchedule(RetryPolicy(attempts=1, delay=2), clock=clock)
    schedule.cancel()
    assert schedule.stop_reason == RetryStopReason.cancelled
    assert not schedule.begin_attempt()
    schedule.reset()
    assert schedule.begin_attempt()
    schedule.reset()  # Operation succeeded, even if its result was None.
    assert schedule.attempt_count == 0
    assert schedule.stop_reason is None
    assert schedule.begin_attempt()
    schedule.failed()
    assert schedule.stop_reason == RetryStopReason.exhausted


def test_fixed_delay_and_deferred_backoff() -> None:
    clock = Clock()
    schedule = RetrySchedule(
        RetryPolicy(delay=1, backoff=2, backoff_after=2), clock=clock
    )
    for d in [1, 1, 2, 4]:
        assert schedule.begin_attempt()
        schedule.failed()
        assert schedule.seconds_until_attempt() == d
        clock.now += d
    fixed = RetrySchedule(RetryPolicy(delay=5), clock=clock)
    for _ in range(3):
        assert fixed.begin_attempt()
        fixed.failed()
        assert fixed.seconds_until_attempt() == 5
        clock.now += 5


def test_attempt_must_be_finished_before_next_attempt() -> None:
    schedule = RetrySchedule(RetryPolicy())
    with pytest.raises(RuntimeError, match='No attempt'):
        schedule.failed()
    assert schedule.begin_attempt()
    with pytest.raises(RuntimeError, match='Finish'):
        schedule.begin_attempt()


@pytest.mark.parametrize(
    'values',
    [
        {'attempts': 0},
        {'delay': -1},
        {'delay': float('nan')},
        {'backoff': 0.5},
        {'max_delay': float('inf')},
        {'backoff_after': 0},
    ],
)
def test_retry_policy_rejects_invalid_timing(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate(values)
