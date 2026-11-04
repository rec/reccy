from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pytest

from reccy.runtime import asset_sets, assets


def test_preflight_aggregates_failures_and_releases_successes(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path, credential_scope='test')
    entry = store.import_bytes(
        b'ok',
        source_key='v1:' + 'a' * 64,
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    missing = assets.ObjectIdentity(sha256='b' * 64, length=1)
    with pytest.raises(asset_sets.AssetPreparationError) as caught:
        with asset_sets.prepare_assets(
            {
                'ok': store.open_entry(entry.id),
                'missing-one': store.open_expected(missing),
                'missing-two': store.open_expected(missing),
            }
        ):
            pytest.fail('Partial preparation must not yield')
    assert [f.label for f in caught.value.failures] == ['missing-one', 'missing-two']
    assert store.collect([]) == [entry.id]


def test_preparation_and_path_hold_leases(tmp_path: Path) -> None:
    store = assets.AssetStore(tmp_path, credential_scope='test')
    entry = store.import_bytes(
        b'ok',
        source_key='v1:' + 'a' * 64,
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.local_file,
    )
    with asset_sets.prepare_assets(
        {'sample': store.open_expected(entry.object)}
    ) as prepared:
        assert prepared.identities['sample'] == entry.object
        assert prepared.streams['sample'].read() == b'ok'
        assert store.collect([]) == []
    with store.verified_path(entry.id) as path:
        assert path.read_bytes() == b'ok'
        assert store.collect([]) == []
    assert store.collect([]) == [entry.id]


def test_preflight_interrupts_without_opening_later_requests() -> None:
    @contextmanager
    def interrupted() -> Iterator[BinaryIO]:
        raise KeyboardInterrupt
        yield BytesIO()  # pragma: no cover

    later = BytesIO(b'later')
    with pytest.raises(KeyboardInterrupt):
        with asset_sets.prepare_assets({'first': interrupted(), 'later': later}):
            pytest.fail('Interrupted preparation must not yield')
    assert not later.closed


@pytest.mark.parametrize(
    'names',
    [['../escape'], ['/absolute'], ['a//b'], ['a', 'a/b'], ['C:/drive'], ['a\\b']],
)
def test_manifest_rejects_unsafe_or_conflicting_paths(names: list[str]) -> None:
    identity = asset_sets.stream_identity(BytesIO(b'test'))
    with pytest.raises(ValueError):
        asset_sets.PackageManifest(files=dict.fromkeys(names, identity))


def test_package_is_verified_and_existing_destination_preserved(tmp_path: Path) -> None:
    source = BytesIO(b'original')
    manifest = asset_sets.PackageManifest(
        files={'audio/take': asset_sets.stream_identity(source)}
    )
    target = tmp_path / 'package'
    asset_sets.export_package(target, {'audio/take': source}, manifest)
    asset_sets.verify_package(target, manifest)
    with pytest.raises(FileExistsError):
        asset_sets.export_package(target, {'audio/take': source}, manifest)
    assert (target / 'audio/take').read_bytes() == b'original'
    (target / 'audio/take').write_bytes(b'corrupt')
    with pytest.raises(asset_sets.AssetPreparationError):
        asset_sets.verify_package(target, manifest)


def test_changed_source_does_not_publish_package(tmp_path: Path) -> None:
    identity = asset_sets.stream_identity(BytesIO(b'expected'))
    with pytest.raises(assets.AssetIdentityMismatch):
        asset_sets.export_package(
            tmp_path / 'package',
            {'sample': BytesIO(b'changed')},
            asset_sets.PackageManifest(files={'sample': identity}),
        )
    assert not (tmp_path / 'package').exists()
    assert list(tmp_path.iterdir()) == []


def test_package_verification_rejects_symlink_member(tmp_path: Path) -> None:
    target = tmp_path / 'outside'
    target.write_bytes(b'ok')
    root = tmp_path / 'package'
    root.mkdir()
    (root / 'sample').symlink_to(target)
    manifest = asset_sets.PackageManifest(
        files={'sample': asset_sets.stream_identity(BytesIO(b'ok'))}
    )
    with pytest.raises(asset_sets.AssetPreparationError):
        asset_sets.verify_package(root, manifest)


def test_package_allows_host_selected_root_beneath_symlinked_parent(
    tmp_path: Path,
) -> None:
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    source = BytesIO(b'package')
    manifest = asset_sets.PackageManifest(
        files={'sample': asset_sets.stream_identity(source)}
    )
    asset_sets.export_package(alias / 'package', {'sample': source}, manifest)
    assert (real / 'package/sample').read_bytes() == b'package'
