from threading import Event

from reccy.runtime.publication import LatestPublisher


def test_slow_publication_coalesces_and_has_bounded_close() -> None:
    entered, release = Event(), Event()
    written: list[int] = []

    def write(value: int) -> None:
        entered.set()
        assert release.wait(2)
        written.append(value)

    publisher = LatestPublisher(write, name='TestPublisher')
    try:
        publisher.publish(0)
        assert entered.wait(1)
        for value in range(1, 1000):
            publisher.publish(value)
        assert not publisher.close(0.01)
    finally:
        release.set()
    assert publisher.close(1)
    assert written == [0, 999]


def test_failed_publication_is_reported_without_blocking_owner() -> None:
    def write(value: int) -> None:
        raise PermissionError('read-only')

    publisher = LatestPublisher(write, name='TestPublisher')
    publisher.publish(1)
    assert publisher.close(1)
    assert isinstance(publisher.error, PermissionError)
