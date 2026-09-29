import hashlib
from pathlib import Path

import pytest

from reccy.runtime import assets, file_assets


def _identity(contents: bytes) -> assets.ObjectIdentity:
    return assets.ObjectIdentity(
        sha256=hashlib.sha256(contents).hexdigest(), length=len(contents)
    )


def test_volume_id_resolves_after_mount_path_changes(tmp_path: Path) -> None:
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    assert (
        file_assets.resolve_volume_root(
            'uuid-1',
            'Takes',
            [
                file_assets.VolumeMount(
                    volume_id='uuid-1', volume_name='Takes', root=first
                )
            ],
        )
        == first
    )
    assert (
        file_assets.resolve_volume_root(
            'uuid-1',
            'Takes',
            [
                file_assets.VolumeMount(
                    volume_id='uuid-1', volume_name='Takes', root=second
                )
            ],
        )
        == second
    )
    with pytest.raises(file_assets.VolumeNotFound):
        file_assets.resolve_volume_root('uuid-1', 'Takes', [])
    with pytest.raises(file_assets.VolumeNotFound):
        file_assets.resolve_volume_root(
            'uuid-1',
            'Takes',
            [
                file_assets.VolumeMount(
                    volume_id='uuid-2', volume_name='Takes', root=second
                )
            ],
        )
    assert (
        file_assets.resolve_volume_root(
            'uuid-1',
            'Takes',
            [
                file_assets.VolumeMount(
                    volume_id='uuid-1', volume_name='Other', root=second
                )
            ],
        )
        == second
    )
    with pytest.raises(file_assets.VolumeMismatch):
        file_assets.resolve_volume_root(
            'uuid-1',
            'Takes',
            [file_assets.VolumeMount(volume_id='uuid-1', root=tmp_path / 'unmounted')],
        )
    with pytest.raises(file_assets.VolumeAmbiguous):
        file_assets.resolve_volume_root(
            'uuid-1',
            None,
            [
                file_assets.VolumeMount(volume_id='uuid-1', root=first),
                file_assets.VolumeMount(volume_id='uuid-1', root=second),
            ],
        )


def test_file_asset_direct_read_and_mutable_snapshot(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'take.bin').write_bytes(b'first')
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    source_key = assets.source_fingerprint(
        {'root': 'approved', 'path': 'take.bin'}, {}, None, {}
    )
    with file_assets.open_file_asset(
        store,
        root,
        'take.bin',
        _identity(b'first'),
        source_key=source_key,
        source_kind=assets.SourceKind.local_file,
        trusted_immutable=True,
        maximum_bytes=5,
    ) as file:
        assert file.read() == b'first'
    assert list((store.root / 'entries').glob('*.json')) == []
    with file_assets.open_file_asset(
        store,
        root,
        'take.bin',
        _identity(b'first'),
        source_key=source_key,
        source_kind=assets.SourceKind.local_file,
        trusted_immutable=False,
        maximum_bytes=5,
    ) as file:
        (root / 'take.bin').write_bytes(b'other')
        assert file.read() == b'first'
    assert len(list((store.root / 'entries').glob('*.json'))) == 1
    with pytest.raises(assets.AssetIdentityMismatch):
        with file_assets.open_file_asset(
            store,
            root,
            'take.bin',
            _identity(b'first'),
            source_key=source_key,
            source_kind=assets.SourceKind.local_file,
            trusted_immutable=False,
            maximum_bytes=5,
        ):
            pass


def test_file_asset_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (tmp_path / 'outside.bin').write_bytes(b'outside')
    (root / 'take.bin').symlink_to(tmp_path / 'outside.bin')
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    with pytest.raises(assets.AssetCacheError):
        with file_assets.open_file_asset(
            store,
            root,
            'take.bin',
            _identity(b'outside'),
            source_key=assets.source_fingerprint({'kind': 'test'}, {}, None, {}),
            source_kind=assets.SourceKind.local_file,
            trusted_immutable=False,
            maximum_bytes=7,
        ):
            pass
