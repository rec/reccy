from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reccy.runtime import assets


@pytest.mark.parametrize('source_kind', list(assets.SourceKind))
def test_store_admits_every_source_kind_metadata(
    tmp_path: Path, source_kind: assets.SourceKind
) -> None:
    entry = assets.AssetStore(tmp_path / 'cache').import_bytes(
        b'finite bytes',
        source_key=f'v1:{source_kind}',
        category=assets.AssetCategory.acquired,
        source_kind=source_kind,
    )
    assert entry.source_kind is source_kind


def test_store_deduplicates_bytes_and_verifies_before_reuse(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache')
    first = store.import_bytes(
        b'first',
        source_key='v1:first',
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    second = store.import_bytes(
        b'first',
        source_key='v1:second',
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    assert first.object == second.object
    assert len(list((tmp_path / 'cache' / 'objects' / 'sha256').rglob('*'))) == 2
    store.object_path(first.object).write_bytes(b'other')
    with pytest.raises(assets.AssetCorruptionError):
        store.import_bytes(
            b'first',
            source_key='v1:third',
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
        )


def test_store_rejects_wrong_expected_identity(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache')
    with pytest.raises(assets.AssetIdentityMismatch):
        store.import_bytes(
            b'bytes',
            source_key='v1:source',
            category=assets.AssetCategory.acquired,
            source_kind=assets.SourceKind.local_file,
            expected=assets.ObjectIdentity(sha256='0' * 64, length=5),
        )


def test_open_entry_leases_verified_bytes_and_releases_lease(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache')
    entry = store.import_bytes(
        b'bytes',
        source_key='v1:source',
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    with store.open_entry(entry.id) as file:
        assert file.read() == b'bytes'
        assert len(list((tmp_path / 'cache' / 'state' / 'leases').iterdir())) == 1
    assert list((tmp_path / 'cache' / 'state' / 'leases').iterdir()) == []


def test_references_move_and_pins_are_immutable_roots(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache')
    first = store.import_bytes(
        b'first',
        source_key='v1:first',
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    second = store.import_bytes(
        b'second',
        source_key='v1:second',
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    store.set_reference('rehearsal.intro', first.id)
    pin_id = store.add_pin(first.id, expires_at=datetime.now(UTC) + timedelta(days=1))
    store.set_reference('rehearsal.intro', second.id)
    reference = assets.AssetReference.model_validate_json(
        (
            tmp_path / 'cache' / 'state' / 'references' / 'rehearsal.intro.json'
        ).read_text()
    )
    pin = assets.AssetPin.model_validate_json(
        (tmp_path / 'cache' / 'state' / 'pins' / f'{pin_id}.json').read_text()
    )
    assert reference.entry_id == second.id
    assert pin.entry_id == first.id


def test_collection_preserves_a_shared_object_still_referenced(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache')
    first = store.import_bytes(
        b'shared',
        source_key='v1:first',
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    second = store.import_bytes(
        b'shared',
        source_key='v1:second',
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    store.set_reference('saved', second.id)
    assert store.collect([]) == [first.id]
    assert store.object_path(second.object).read_bytes() == b'shared'
    assert store.entry(second.id) == second


def test_retention_rules_explain_normal_and_pressure_collection(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path / 'cache')
    entry = store.import_bytes(
        b'bytes',
        source_key='v1:source',
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
    store = assets.AssetStore(tmp_path / 'cache')
    entry = store.import_bytes(
        b'bytes',
        source_key='v1:source',
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    rule = assets.RetentionRule(name='protect', all=True, protect='forever')
    assert store.collect([rule], pressure=True) == []
    with store.open_entry(entry.id) as file:
        assert file.read() == b'bytes'
    assert (tmp_path / 'cache' / 'state' / 'access' / f'{entry.id}.json').exists()


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
