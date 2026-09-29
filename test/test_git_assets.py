import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from reccy.runtime import assets, git_assets

pytestmark = pytest.mark.skipif(
    shutil.which('git') is None, reason='Git is unavailable'
)


def test_local_git_file_imports_selected_regular_blob(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / 'audio').mkdir()
    contents = b'git asset bytes'
    (repository / 'audio' / 'take.bin').write_bytes(contents)
    _git(repository, 'add', 'audio/take.bin')
    _git(repository, 'commit', '-m', 'Add asset')
    commit = _git(repository, 'rev-parse', 'HEAD').strip().decode()
    expected = assets.ObjectIdentity(
        sha256=hashlib.sha256(contents).hexdigest(), length=len(contents)
    )
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    entry, blob = git_assets.import_local_git_file(
        store,
        repository,
        commit,
        'audio/take.bin',
        expected,
        maximum_bytes=len(contents),
        source_key=assets.source_fingerprint(
            {'commit': commit, 'path': 'audio/take.bin'}, {}, expected, {}
        ),
    )
    assert len(blob) == len(commit)
    assert blob != expected.sha256
    with store.open_entry(entry.id) as file:
        assert file.read() == contents
    wrong = assets.ObjectIdentity(sha256='0' * 64, length=len(contents))
    with pytest.raises(assets.AssetIdentityMismatch):
        git_assets.import_local_git_file(
            store,
            repository,
            commit,
            'audio/take.bin',
            wrong,
            maximum_bytes=len(contents),
            source_key=assets.source_fingerprint(
                {'commit': commit, 'path': 'audio/take.bin'}, {}, wrong, {}
            ),
        )
    assert len(list((store.root / 'entries').glob('*.json'))) == 1


def test_local_git_file_rejects_symlink_and_lfs_pointer(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / 'linked.bin').symlink_to('target.bin')
    pointer = (
        b'version https://git-lfs.github.com/spec/v1\n'
        b'oid sha256:' + b'0' * 64 + b'\nsize 10\n'
    )
    (repository / 'pointer.bin').write_bytes(pointer)
    _git(repository, 'add', 'linked.bin', 'pointer.bin')
    _git(repository, 'commit', '-m', 'Add nonregular assets')
    commit = _git(repository, 'rev-parse', 'HEAD').strip().decode()
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    for path, data in (('linked.bin', b'target.bin'), ('pointer.bin', pointer)):
        expected = assets.ObjectIdentity(
            sha256=hashlib.sha256(data).hexdigest(), length=len(data)
        )
        with pytest.raises(assets.AssetCacheError):
            git_assets.import_local_git_file(
                store,
                repository,
                commit,
                path,
                expected,
                maximum_bytes=len(data),
                source_key=assets.source_fingerprint(
                    {'commit': commit, 'path': path}, {}, expected, {}
                ),
            )
    assert list((store.root / 'entries').glob('*.json')) == []


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / 'repository'
    repository.mkdir()
    _git(repository, 'init', '-q')
    _git(repository, 'config', 'user.name', 'Asset Test')
    _git(repository, 'config', 'user.email', 'asset@example.test')
    return repository


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ['git', '-C', str(repository), *arguments],
        check=True,
        capture_output=True,
    ).stdout
