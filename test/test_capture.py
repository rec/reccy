from pathlib import Path

import pytest

from reccy.runtime import assets, capture


def capture_store(tmp_path: Path) -> capture.CaptureStore:
    return capture.CaptureStore(assets.AssetStore(tmp_path / 'cache'))


def capture_spec(**changes: object) -> capture.CaptureSpec:
    values: dict[str, object] = {
        'source_key': 'v1:microphone',
        'source_kind': assets.SourceKind.callback,
        'maximum_bytes': 32,
        'maximum_queue_bytes': 8,
        'frame_limit': 4,
        'adapter_version': 'callback-v1',
        'encoder_version': 'bytes-v1',
    }
    values.update(changes)
    return capture.CaptureSpec.model_validate(values)


def test_callback_copies_borrowed_memory_before_it_is_reused(tmp_path: Path) -> None:
    store = capture_store(tmp_path)
    session = store.start(capture_spec())
    borrowed = bytearray(b'abcd')
    assert session.queue_fragment(borrowed, frame_count=4)
    borrowed[:] = b'xxxx'
    manifest = session.finish(capture.CaptureTermination.bound)
    with store.assets.open_entry(manifest.fragments[0].entry_id) as file:
        assert file.read() == b'abcd'
    with pytest.raises(capture.CaptureStateError, match='no longer accepting'):
        session.queue_fragment(b'late', frame_count=4)


def test_queue_overflow_records_an_explicit_gap(tmp_path: Path) -> None:
    store = capture_store(tmp_path)
    session = store.start(
        capture_spec(
            frame_limit=None,
            manual_stop=True,
            maximum_queue_bytes=4,
            overflow='gap',
        )
    )
    assert session.queue_fragment(b'abcd', frame_count=4)
    assert not session.queue_fragment(b'ef', frame_count=2)
    manifest = session.finish(capture.CaptureTermination.clean_stop)
    assert manifest.observed_frames == 6
    assert manifest.gaps == [
        capture.CaptureGap(
            native_start=4,
            frame_count=2,
            reason=capture.CaptureGapReason.queue_overflow,
        )
    ]


def test_queue_overflow_can_fail_instead_of_dropping(tmp_path: Path) -> None:
    store = capture_store(tmp_path)
    session = store.start(
        capture_spec(
            frame_limit=None,
            manual_stop=True,
            maximum_queue_bytes=4,
        )
    )
    session.queue_fragment(b'abcd', frame_count=4)
    with pytest.raises(capture.CaptureQueueOverflow):
        session.queue_fragment(b'ef', frame_count=2)
    recovery = session.abort('queue overflow')
    assert recovery.reason == 'queue overflow'
    assert store.recovery(session.id) == recovery
    with pytest.raises(capture.CaptureError):
        store.capture(session.id)


def test_abort_and_explicit_salvage_have_distinct_results(tmp_path: Path) -> None:
    store = capture_store(tmp_path)
    aborted = store.start(capture_spec())
    aborted.queue_fragment(b'abcd', frame_count=4)
    recovery = aborted.abort('provider failed')
    assert recovery.fragments
    with pytest.raises(capture.CaptureError):
        store.capture(aborted.id)

    salvaged = store.start(capture_spec())
    salvaged.queue_fragment(b'abcd', frame_count=4)
    manifest = salvaged.salvage('provider failed')
    assert manifest.termination is capture.CaptureTermination.salvaged_failure
    assert store.capture(salvaged.id) == manifest


def test_reference_movement_does_not_retarget_an_existing_pin(tmp_path: Path) -> None:
    store = capture_store(tmp_path)
    first_session = store.start(capture_spec())
    first_session.queue_fragment(b'1111', frame_count=4)
    first = first_session.finish(capture.CaptureTermination.bound)
    second_session = store.start(capture_spec())
    second_session.queue_fragment(b'2222', frame_count=4)
    second = second_session.finish(capture.CaptureTermination.bound)

    store.set_reference('rehearsal/intro', first.id)
    pin_id = store.pin_reference('rehearsal/intro')
    store.set_reference('rehearsal/intro', second.id)
    assert store.referenced('rehearsal/intro') == second
    assert store.pinned(pin_id) == first


def test_capture_requires_a_bound_and_budget(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='requires one'):
        capture_spec(frame_limit=None)
    session = capture_store(tmp_path).start(
        capture_spec(maximum_bytes=3, maximum_queue_bytes=3)
    )
    with pytest.raises(capture.CaptureCapacityError):
        session.queue_fragment(b'abcd', frame_count=4)
