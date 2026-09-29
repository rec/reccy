from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    verify = store._verify_file

    def verify_with_replacement(
        file: BinaryIO, identity: assets.ObjectIdentity
    ) -> None:
        assert store.collect([]) == []
        verify(file, identity)
        replacement = tmp_path / 'replacement'
        replacement.write_bytes(b'other')
        replacement.replace(store.object_path(identity))

    monkeypatch.setattr(store, '_verify_file', verify_with_replacement)
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
