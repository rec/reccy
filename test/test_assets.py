import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import BinaryIO

import pytest

from reccy.runtime import assets


def _source_key(label: str) -> str:
    return assets.source_fingerprint({'test': label}, {}, None, {})


@pytest.mark.parametrize('source_kind', list(assets.SourceKind))
def test_store_admits_every_source_kind_metadata(
    tmp_path: Path, source_kind: assets.SourceKind
) -> None:
    entry = assets.AssetStore(
        tmp_path / 'cache', credential_scope='public'
    ).import_bytes(
        b'finite bytes',
        source_key=_source_key(str(source_kind)),
        category=assets.AssetCategory.acquired,
        source_kind=source_kind,
    )
    assert entry.source_kind is source_kind


def test_source_fingerprint_preserves_request_value_distinctions() -> None:
    expected = assets.ObjectIdentity(sha256='a' * 64, length=12)
    location = {'kind': 'python_provider', 'arguments': {'é': [None, True, 1, 1.0]}}
    first = assets.source_fingerprint(
        location, {'package': 'set-a'}, expected, {'channels': ['left', 'right']}
    )
    reordered = assets.source_fingerprint(
        {'arguments': {'é': [None, True, 1, 1.0]}, 'kind': 'python_provider'},
        {'package': 'set-a'},
        expected,
        {'channels': ['left', 'right']},
    )
    assert first == reordered
    assert first.startswith('v1:')
    for changed in (
        {'é': [None, True, 1, 1]},
        {'é': [None, True, 1.0, 1.0]},
        {'e\u0301': [None, True, 1, 1.0]},
        {'é': [None, False, 1, 1.0]},
    ):
        assert first != assets.source_fingerprint(
            {'kind': 'python_provider', 'arguments': changed},
            {'package': 'set-a'},
            expected,
            {'channels': ['left', 'right']},
        )
    assert first != assets.source_fingerprint(
        location, {'package': 'set-b'}, expected, {'channels': ['left', 'right']}
    )


def test_source_fingerprint_keys_secret_without_persisting_it() -> None:
    location = {'kind': 'download', 'url': 'https://example.test/file'}
    first = assets.source_fingerprint(
        location, {}, None, {}, lookup_secret=b'token-a', fingerprint_key=b'a' * 32
    )
    assert b'token-a' not in first.encode()
    assert first != assets.source_fingerprint(
        location, {}, None, {}, lookup_secret=b'token-b', fingerprint_key=b'a' * 32
    )
    assert first != assets.source_fingerprint(
        location, {}, None, {}, lookup_secret=b'token-a', fingerprint_key=b'b' * 32
    )
    with pytest.raises(ValueError, match='together'):
        assets.source_fingerprint(location, {}, None, {}, lookup_secret=b'token-a')


@pytest.mark.parametrize(
    'value',
    [float('nan'), float('inf'), {1: 'value'}, (1,)],
)
def test_source_fingerprint_rejects_non_json_values(value: object) -> None:
    with pytest.raises(ValueError):
        assets.source_fingerprint({'kind': 'test', 'value': value}, {}, None, {})


def test_store_deduplicates_bytes_and_verifies_before_reuse(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    first = store.import_bytes(
        b'first',
        source_key=_source_key('first'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    second = store.import_bytes(
        b'first',
        source_key=_source_key('second'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    assert first.object == second.object
    assert len(list((store.root / 'objects' / 'sha256').rglob('*'))) == 2
    store.object_path(first.object).write_bytes(b'other')
    with pytest.raises(assets.AssetCorruptionError):
        store.import_bytes(
            b'first',
            source_key=_source_key('third'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )


def test_credential_scopes_isolate_entries_and_references(tmp_path: Path) -> None:
    root = tmp_path / 'cache'
    first = assets.AssetStore(root, credential_scope='alice')
    second = assets.AssetStore(root, credential_scope='bob')
    entry = first.import_bytes(
        b'private bytes',
        source_key=_source_key('same-request'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.download,
    )
    first.set_reference('latest', entry.id)
    assert first.root != second.root
    assert 'alice' not in str(first.root)
    with pytest.raises(assets.AssetCacheError, match='Unknown asset record'):
        second.entry(entry.id)
    with pytest.raises(assets.AssetCacheError, match='Unknown asset record'):
        second.set_reference('latest', entry.id)
    assert second.plan_collection([]) == []


def test_store_rejects_raw_source_description(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    with pytest.raises(ValueError, match='source_key'):
        store.import_bytes(
            b'bytes',
            source_key='https://example.test/private?token=secret',
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.download,
        )
    assert not store.root.exists()


def test_store_rejects_wrong_expected_identity(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    with pytest.raises(assets.AssetIdentityMismatch):
        store.import_bytes(
            b'bytes',
            source_key=_source_key('source'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
            expected=assets.ObjectIdentity(sha256='0' * 64, length=5),
        )


def test_stream_admission_verifies_bounded_complete_bytes(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    contents = b'chunk' * 20000
    expected = assets.ObjectIdentity(
        sha256=hashlib.sha256(contents).hexdigest(), length=len(contents)
    )
    entry = store.import_stream(
        BytesIO(contents),
        maximum_bytes=len(contents),
        source_key=_source_key('large file'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
        expected=expected,
    )
    assert entry.object == expected
    with store.open_entry(entry.id) as file:
        assert file.read() == contents


def test_stream_admission_cleans_up_oversize_and_interrupted_reads(
    tmp_path: Path,
) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    fields = {
        'source_key': _source_key('interrupted file'),
        'category': assets.AssetCategory.acquired,
        'source_kind': assets.SourceKind.local_file,
    }
    with pytest.raises(assets.AssetCacheError, match='maximum_bytes'):
        store.import_stream(BytesIO(b'oversize'), maximum_bytes=4, **fields)

    class Interrupted(BytesIO):
        def read(self, size: int = -1) -> bytes:
            if self.tell() > 0:
                raise OSError('source disappeared')
            return super().read(1)

    with pytest.raises(OSError, match='source disappeared'):
        store.import_stream(Interrupted(b'bytes'), maximum_bytes=5, **fields)
    assert list((store.root / 'staging').iterdir()) == []
    assert list((store.root / 'entries').iterdir()) == []


@pytest.mark.parametrize(
    'source_kind', [assets.SourceKind.local_file, assets.SourceKind.volume_file]
)
def test_file_snapshot_survives_source_changes(
    tmp_path: Path, source_kind: assets.SourceKind
) -> None:
    root = tmp_path / 'source'
    (root / 'audio').mkdir(parents=True)
    path = root / 'audio' / 'take.bin'
    path.write_bytes(b'original')
    expected = assets.ObjectIdentity(
        sha256=hashlib.sha256(b'original').hexdigest(), length=8
    )
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry = store.import_file(
        root,
        'audio/take.bin',
        maximum_bytes=8,
        source_key=_source_key('local take'),
        source_kind=source_kind,
        expected=expected,
    )
    path.write_bytes(b'changed!')
    with store.open_entry(entry.id) as file:
        assert file.read() == b'original'


def test_direct_file_read_requires_immutable_host_policy(tmp_path: Path) -> None:
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'take.bin').write_bytes(b'original')
    expected = assets.ObjectIdentity(
        sha256=hashlib.sha256(b'original').hexdigest(), length=8
    )
    with pytest.raises(ValueError, match='trusted immutable'):
        with assets.open_verified_file(
            root, 'take.bin', expected, trusted_immutable=False
        ):
            pass
    with assets.open_verified_file(
        root, 'take.bin', expected, trusted_immutable=True
    ) as file:
        assert file.read() == b'original'


def test_file_resolution_rejects_traversal_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'take.bin').write_bytes(b'original')
    (root / 'linked.bin').symlink_to('take.bin')
    expected = assets.ObjectIdentity(
        sha256=hashlib.sha256(b'original').hexdigest(), length=8
    )
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    for path in ('../take.bin', '/take.bin', 'linked.bin'):
        with pytest.raises((ValueError, assets.AssetCacheError)):
            store.import_file(
                root,
                path,
                maximum_bytes=8,
                source_key=_source_key(path),
                source_kind=assets.SourceKind.local_file,
                expected=expected,
            )


def test_open_entry_leases_verified_bytes_and_releases_lease(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry = store.import_bytes(
        b'bytes',
        source_key=_source_key('source'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    with store.open_entry(entry.id) as file:
        assert file.read() == b'bytes'
        assert len(list((store.root / 'state' / 'leases').iterdir())) == 1
    assert list((store.root / 'state' / 'leases').iterdir()) == []


def test_offline_identity_lookup_is_scoped_and_leased(tmp_path: Path) -> None:
    root = tmp_path / 'cache'
    store = assets.AssetStore(root, credential_scope='alice')
    entry = store.import_bytes(
        b'offline bytes',
        source_key=_source_key('old URL'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.download,
    )
    with store.open_expected(entry.object) as file:
        assert store.collect([]) == []
        assert file.read() == b'offline bytes'
    with pytest.raises(assets.AssetCacheMiss):
        with assets.AssetStore(root, credential_scope='bob').open_expected(
            entry.object
        ):
            pass
    assert store.collect([]) == [entry.id]
    with pytest.raises(assets.AssetCacheMiss):
        with store.open_expected(entry.object):
            pass


def test_open_entry_keeps_verified_handle_and_lease_when_path_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry = store.import_bytes(
        b'bytes',
        source_key=_source_key('source'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    verify = assets._verify_file

    def verify_with_replacement(
        file: BinaryIO, identity: assets.ObjectIdentity
    ) -> None:
        assert store.collect([]) == []
        verify(file, identity)
        replacement = tmp_path / 'replacement'
        replacement.write_bytes(b'other')
        replacement.replace(store.object_path(identity))

    monkeypatch.setattr(assets, '_verify_file', verify_with_replacement)
    with store.open_entry(entry.id) as file:
        assert file.read() == b'bytes'


def test_abandoned_lease_remains_a_root_after_store_reopens(tmp_path: Path) -> None:
    root = tmp_path / 'cache'
    store = assets.AssetStore(root, credential_scope='public')
    entry = store.import_bytes(
        b'crash artifact',
        source_key=_source_key('source'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    (store.root / 'state' / 'leases' / 'abandoned.json').write_text(
        assets.AssetLease(entry_id=entry.id).model_dump_json()
    )

    reopened = assets.AssetStore(root, credential_scope='public')
    assert reopened.collect([], pressure=True) == []
    assert reopened.entry(entry.id) == entry
    assert reopened.object_path(entry.object).read_bytes() == b'crash artifact'


def test_collection_does_not_sweep_orphaned_crash_bytes(tmp_path: Path) -> None:
    root = tmp_path / 'cache'
    store = assets.AssetStore(root, credential_scope='public')
    entry = store.import_bytes(
        b'crash artifact',
        source_key=_source_key('source'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    store._entry_path(entry.id).unlink()

    reopened = assets.AssetStore(root, credential_scope='public')
    assert reopened.collect([], pressure=True) == []
    assert reopened.object_path(entry.object).read_bytes() == b'crash artifact'


def test_failed_staging_sync_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')

    def fail_sync(descriptor: int) -> None:
        raise OSError('disk unavailable')

    monkeypatch.setattr(assets.os, 'fsync', fail_sync)
    with pytest.raises(OSError, match='disk unavailable'):
        store.import_bytes(
            b'bytes',
            source_key=_source_key('source'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )
    assert list((store.root / 'staging').iterdir()) == []


def test_references_move_and_pins_are_immutable_roots(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    first = store.import_bytes(
        b'first',
        source_key=_source_key('first'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    second = store.import_bytes(
        b'second',
        source_key=_source_key('second'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    store.set_reference('rehearsal.intro', first.id)
    pin_id = store.add_pin(first.id, expires_at=datetime.now(UTC) + timedelta(days=1))
    store.set_reference('rehearsal.intro', second.id)
    reference = assets.AssetReference.model_validate_json(
        (store.root / 'state' / 'references' / 'rehearsal.intro.json').read_text()
    )
    pin = assets.AssetPin.model_validate_json(
        (store.root / 'state' / 'pins' / f'{pin_id}.json').read_text()
    )
    assert reference.entry_id == second.id
    assert pin.entry_id == first.id


def test_expired_pin_metadata_is_removed_during_collection(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry = store.import_bytes(
        b'bytes',
        source_key=_source_key('expiring'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    expiry = datetime.now(UTC) + timedelta(days=1)
    pin_id = store.add_pin(entry.id, expires_at=expiry)
    pin_path = store.root / 'state' / 'pins' / f'{pin_id}.json'
    assert store.collect([], now=expiry - timedelta(seconds=1)) == []
    assert pin_path.exists()
    assert store.collect([], now=expiry) == [entry.id]
    assert not pin_path.exists()


def test_collection_preserves_a_shared_object_still_referenced(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    first = store.import_bytes(
        b'shared',
        source_key=_source_key('first'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    second = store.import_bytes(
        b'shared',
        source_key=_source_key('second'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    store.set_reference('saved', second.id)
    assert store.collect([]) == [first.id]
    assert store.object_path(second.object).read_bytes() == b'shared'
    assert store.entry(second.id) == second


def test_retention_rules_explain_normal_and_pressure_collection(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry = store.import_bytes(
        b'bytes',
        source_key=_source_key('source'),
        category=assets.AssetCategory.derived,
        source_kind=assets.SourceKind.complete_array,
    )
    rule = assets.RetentionRule(
        name='recent derivative',
        match=assets.RetentionMatch(category=[assets.AssetCategory.derived]),
        retain=assets.RetentionDuration(days=1, since=assets.RetentionSince.created),
    )
    decision = store.explain_retention(entry.id, [rule], now=entry.created_at)
    assert decision.matching_rules == ['recent derivative']
    assert not decision.eligible_for_ordinary_collection
    assert decision.eligible_for_pressure_collection
    assert store.plan_collection([rule]) == []
    assert store.plan_collection([rule], pressure=True)[0].entry_id == entry.id


def test_protection_and_a_successful_lease_are_collection_roots(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry = store.import_bytes(
        b'bytes',
        source_key=_source_key('source'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    rule = assets.RetentionRule(name='protect', all=True, protect='forever')
    assert store.collect([rule], pressure=True) == []
    with store.open_entry(entry.id) as file:
        assert file.read() == b'bytes'
    assert (store.root / 'state' / 'access' / f'{entry.id}.json').exists()


def test_retention_rules_reject_ambiguous_or_empty_selectors() -> None:
    with pytest.raises(ValueError, match='at least one field'):
        assets.RetentionMatch()
    with pytest.raises(ValueError, match='exactly one'):
        assets.RetentionRule(
            name='ambiguous',
            all=True,
            protect='forever',
            retain='forever',
        )


def test_newest_retention_groups_by_source_and_recomputes_after_collection(
    tmp_path: Path,
) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    first = store.import_bytes(
        b'first',
        source_key=_source_key('one'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.download,
    )
    second = store.import_bytes(
        b'second',
        source_key=_source_key('one'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.download,
    )
    other = store.import_bytes(
        b'other',
        source_key=_source_key('two'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.download,
    )
    rule = assets.RetentionRule(
        name='latest per source', all=True, newest=assets.RetentionNewest(count=1)
    )
    older, latest = sorted((first, second), key=lambda e: (e.created_at, e.id))
    assert store.explain_retention(older.id, [rule]).newest_ranks == {
        'latest per source': 2
    }
    assert store.explain_retention(latest.id, [rule]).newest_ranks == {
        'latest per source': 1
    }
    assert [d.entry_id for d in store.plan_collection([rule])] == [older.id]
    assert store.collect([rule]) == [older.id]
    assert store.explain_retention(latest.id, [rule]).retained
    assert store.explain_retention(other.id, [rule]).retained
    assert set(store.collect([rule], pressure=True)) == {latest.id, other.id}


def test_newest_retention_combines_with_duration_and_global_ranking(
    tmp_path: Path,
) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    first = store.import_bytes(
        b'first',
        source_key=_source_key('one'),
        category=assets.AssetCategory.derived,
        source_kind=assets.SourceKind.complete_array,
    )
    second = store.import_bytes(
        b'second',
        source_key=_source_key('two'),
        category=assets.AssetCategory.derived,
        source_kind=assets.SourceKind.complete_array,
    )
    rule = assets.RetentionRule(
        name='recent or latest',
        all=True,
        retain=assets.RetentionDuration(days=1, since=assets.RetentionSince.created),
        newest=assets.RetentionNewest(count=1, group_by='all'),
    )
    assert store.plan_collection([rule], now=second.created_at) == []
    later = second.created_at + timedelta(days=2)
    older = min((first, second), key=lambda e: (e.created_at, e.id))
    assert [d.entry_id for d in store.plan_collection([rule], now=later)] == [older.id]


def test_recovery_inspection_reports_unreferenced_bytes_without_deleting_them(
    tmp_path: Path,
) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry = store.import_bytes(
        b'referenced',
        source_key=_source_key('one'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    staging = store.root / 'staging' / 'interrupted'
    staging.write_bytes(b'partial')
    recovery = store.root / 'staging' / f'capture-{"a" * 32}.json'
    recovery.write_bytes(b'{}')
    orphan = store.root / 'objects' / 'sha256' / 'ab' / ('ab' * 32)
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b'orphan')
    assert store.inspect_recovery() == [
        assets.RecoveryItem(
            kind=assets.RecoveryKind.orphan_object,
            path=str(orphan.relative_to(store.root)),
            byte_length=6,
        ),
        assets.RecoveryItem(
            kind=assets.RecoveryKind.capture_recovery,
            path=str(recovery.relative_to(store.root)),
            byte_length=2,
        ),
        assets.RecoveryItem(
            kind=assets.RecoveryKind.staging,
            path=str(staging.relative_to(store.root)),
            byte_length=7,
        ),
    ]
    assert store.object_path(entry.object).read_bytes() == b'referenced'
    assert orphan.read_bytes() == b'orphan'
    assert recovery.read_bytes() == b'{}'
    assert staging.read_bytes() == b'partial'


def test_export_entry_is_atomic_and_rejects_corrupt_stored_bytes(
    tmp_path: Path,
) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry = store.import_bytes(
        b'verified bytes',
        source_key=_source_key('export'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    destination = tmp_path / 'package' / 'take.bin'
    assert store.export_entry(entry.id, destination) == entry.object
    assert destination.read_bytes() == b'verified bytes'
    store.object_path(entry.object).write_bytes(b'corrupt')
    with pytest.raises(assets.AssetCorruptionError):
        store.export_entry(entry.id, destination)
    assert destination.read_bytes() == b'verified bytes'


def test_capacity_counts_staging_and_distinct_objects(tmp_path: Path) -> None:
    store = assets.AssetStore(
        tmp_path / 'cache',
        credential_scope='public',
        capacity=assets.AssetCapacity(
            maximum_object_bytes=5,
            maximum_staging_bytes=5,
            minimum_free_space=0,
        ),
    )
    first = store.import_bytes(
        b'first',
        source_key=_source_key('one'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    store.import_bytes(
        b'first',
        source_key=_source_key('two'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    assert store.object_path(first.object).read_bytes() == b'first'
    with pytest.raises(assets.AssetInsufficientSpace, match='Object admission'):
        store.import_bytes(
            b'other',
            source_key=_source_key('three'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )
    with pytest.raises(assets.AssetInsufficientSpace, match='Staging needs'):
        store.import_stream(
            BytesIO(b'too long'),
            maximum_bytes=10,
            source_key=_source_key('four'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )
    assert len(list((store.root / 'entries').glob('*.json'))) == 2


def test_capacity_respects_existing_staging_and_free_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = assets.AssetStore(
        tmp_path / 'cache',
        credential_scope='public',
        capacity=assets.AssetCapacity(
            maximum_object_bytes=100,
            maximum_staging_bytes=10,
            minimum_free_space=5,
        ),
    )
    (store.root / 'staging').mkdir(parents=True)
    (store.root / 'staging' / 'recovery').write_bytes(b'12345678')
    with pytest.raises(assets.AssetInsufficientSpace, match='Staging needs'):
        store.import_bytes(
            b'abc',
            source_key=_source_key('one'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )
    monkeypatch.setattr(
        assets.shutil, 'disk_usage', lambda path: SimpleNamespace(free=6)
    )
    with pytest.raises(assets.AssetInsufficientSpace, match='free-space margin'):
        store.import_bytes(
            b'ab',
            source_key=_source_key('two'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )


def test_capacity_admission_tolerates_disappearing_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = assets.AssetStore(
        tmp_path / 'cache',
        credential_scope='public',
        capacity=assets.AssetCapacity(
            maximum_object_bytes=10,
            maximum_staging_bytes=10,
            minimum_free_space=0,
        ),
    )
    staged = store.root / 'staging' / 'completed-recovery'
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b'old')
    original_stat = Path.stat

    def stat(path: Path, *args: object, **kwargs: object) -> object:
        if path == staged:
            staged.unlink()
            raise FileNotFoundError(staged)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', stat)

    entry = store.import_bytes(
        b'new',
        source_key=_source_key('after-recovery'),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    with store.open_entry(entry.id) as file:
        assert file.read() == b'new'


def test_capacity_serializes_competing_imports(tmp_path: Path) -> None:
    store = assets.AssetStore(
        tmp_path / 'cache',
        credential_scope='public',
        capacity=assets.AssetCapacity(
            maximum_object_bytes=5,
            maximum_staging_bytes=5,
            minimum_free_space=0,
        ),
    )
    entered = Event()
    release = Event()

    class BlockingReader:
        def __init__(self) -> None:
            self.done = False

        def read(self, size: int = -1) -> bytes:
            if self.done:
                return b''
            entered.set()
            assert release.wait(5)
            self.done = True
            return b'first'

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            store.import_stream,
            BlockingReader(),
            maximum_bytes=5,
            source_key=_source_key('one'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )
        assert entered.wait(5)
        second = pool.submit(
            store.import_bytes,
            b'other',
            source_key=_source_key('two'),
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )
        release.set()
        assert first.result().object.length == 5
        with pytest.raises(assets.AssetInsufficientSpace):
            second.result()
    assert len(list((store.root / 'objects' / 'sha256').glob('*/*'))) == 1
