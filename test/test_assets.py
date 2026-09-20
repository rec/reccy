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
