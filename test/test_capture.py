import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from reccy.runtime import assets, capture


def capture_store(tmp_path: Path) -> capture.CaptureStore:
    return capture.CaptureStore(
        assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    )


def capture_spec(**changes: object) -> capture.CaptureSpec:
    values: dict[str, object] = {
        'source_key': assets.source_fingerprint({'test': 'microphone'}, {}, None, {}),
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


def test_aborted_capture_fragments_survive_store_reopen(tmp_path: Path) -> None:
    root = tmp_path / 'cache'
    store = capture.CaptureStore(assets.AssetStore(root, credential_scope='public'))
    session = store.start(capture_spec())
    session.queue_fragment(b'abcd', frame_count=4)
    recovery = session.abort('provider failed')

    reopened = capture.CaptureStore(assets.AssetStore(root, credential_scope='public'))
    assert reopened.assets.collect([], pressure=True) == []
    assert reopened.recovery(session.id) == recovery
    with reopened.assets.open_entry(recovery.fragments[0].entry_id) as file:
        assert file.read() == b'abcd'


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


@pytest.mark.parametrize('name', ['rehearsal//intro', 'rehearsal/./intro', 'intro/'])
def test_capture_references_reject_ambiguous_paths(tmp_path: Path, name: str) -> None:
    store = capture_store(tmp_path)
    with pytest.raises(ValueError, match='safe relative paths'):
        store.set_reference(name, '0' * 32)


def test_capture_requires_a_bound_and_budget(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='requires one'):
        capture_spec(frame_limit=None)
    session = capture_store(tmp_path).start(
        capture_spec(maximum_bytes=3, maximum_queue_bytes=3)
    )
    with pytest.raises(capture.CaptureCapacityError):
        session.queue_fragment(b'abcd', frame_count=4)


def test_failed_drain_keeps_fragments_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = capture_store(tmp_path)
    session = store.start(
        capture_spec(
            frame_limit=None,
            manual_stop=True,
            maximum_bytes=6,
            maximum_queue_bytes=4,
        )
    )
    session.queue_fragment(b'abcd', frame_count=4)
    import_bytes = store.assets.import_bytes
    failed = False

    def fail_once(contents: bytes, **kwargs: object) -> assets.AssetEntry:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError('disk unavailable')
        return import_bytes(contents, **kwargs)

    monkeypatch.setattr(store.assets, 'import_bytes', fail_once)
    with pytest.raises(OSError, match='disk unavailable'):
        session.drain()
    with pytest.raises(capture.CaptureCapacityError):
        session.queue_fragment(b'efg', frame_count=3)
    assert session.queue_fragment(b'ef', frame_count=2)
    manifest = session.finish(capture.CaptureTermination.clean_stop)
    assert manifest.observed_frames == 6
    assert manifest.stored_bytes == 6
    for fragment, expected in zip(manifest.fragments, [b'abcd', b'ef'], strict=True):
        with store.assets.open_entry(fragment.entry_id) as file:
            assert file.read() == expected


def test_failed_pin_write_keeps_capture_fragment_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = capture_store(tmp_path)
    session = store.start(capture_spec())
    session.queue_fragment(b'abcd', frame_count=4)
    write_new = store.assets._write_new_model
    failed = False

    def fail_pin_once(path: Path, value: object) -> None:
        nonlocal failed
        if path.parent.name == 'pins' and not failed:
            failed = True
            raise OSError('pin write failed')
        write_new(path, value)

    monkeypatch.setattr(store.assets, '_write_new_model', fail_pin_once)
    with pytest.raises(OSError, match='pin write failed'):
        session.drain()
    assert list((store.root / 'entries').glob('*.json')) == []

    manifest = session.finish(capture.CaptureTermination.bound)
    fragment = manifest.fragments[0]
    assert fragment.pin_id == fragment.entry_id
    assert store.assets.collect([], pressure=True) == []
    with store.assets.open_entry(fragment.entry_id) as file:
        assert file.read() == b'abcd'


def test_concurrent_finalization_publishes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = capture_store(tmp_path)
    session = store.start(capture_spec())
    session.queue_fragment(b'abcd', frame_count=4)
    publishing = threading.Event()
    release = threading.Event()
    publish = store._publish

    def hold_publish(manifest: capture.CaptureManifest) -> None:
        publishing.set()
        assert release.wait(2)
        publish(manifest)

    monkeypatch.setattr(store, '_publish', hold_publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = pool.submit(session.finish, capture.CaptureTermination.bound)
        assert publishing.wait(2)
        competing = pool.submit(session.abort, 'interrupted')
        release.set()
        manifest = completed.result(timeout=2)
        with pytest.raises(capture.CaptureStateError, match='already closed'):
            competing.result(timeout=2)
    assert store.capture(session.id) == manifest
    with pytest.raises(capture.CaptureError):
        store.recovery(session.id)


def test_duration_bound_can_finish_without_another_fragment(tmp_path: Path) -> None:
    now = 0.0
    store = capture_store(tmp_path)
    session = store.start(
        capture_spec(frame_limit=None, duration_limit=1), clock=lambda: now
    )
    session.queue_fragment(b'abcd', frame_count=4)
    now = 1.0
    assert session.finish(capture.CaptureTermination.bound).observed_frames == 4


def test_rejected_fragments_do_not_advance_capture_timeline(tmp_path: Path) -> None:
    store = capture_store(tmp_path)
    session = store.start(
        capture_spec(frame_limit=None, manual_stop=True, maximum_queue_bytes=4)
    )
    with pytest.raises(ValueError, match='contain bytes'):
        session.queue_fragment(b'', frame_count=1)
    session.queue_fragment(b'abcd', frame_count=4)
    with pytest.raises(capture.CaptureQueueOverflow):
        session.queue_fragment(b'ef', frame_count=2)
    recovery = session.abort('queue full')
    assert recovery.observed_frames == 4
    assert recovery.gaps == []
