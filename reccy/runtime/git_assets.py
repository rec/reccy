"""Acquire a pinned file from a host-approved Git object store."""

import os
import re
import subprocess
from pathlib import Path, PureWindowsPath
from typing import IO

from .assets import (
    AssetCacheError,
    AssetCategory,
    AssetEntry,
    AssetIdentityMismatch,
    AssetStore,
    MediaKind,
    ObjectIdentity,
    SourceKind,
)


def import_local_git_file(
    store: AssetStore,
    repository: Path,
    commit: str,
    path: str,
    expected: ObjectIdentity,
    *,
    maximum_bytes: int,
    source_key: str,
    media_kind: MediaKind = MediaKind.other,
    git_executable: str = 'git',
) -> tuple[AssetEntry, str]:
    """Verify one regular blob without checkout, filters, hooks, or lazy fetch.

    The repository is selected and authorized by the host. The returned blob ID
    is Git transport evidence; the entry's SHA-256 identifies the file bytes.
    """
    _validate_request(commit, path, expected, maximum_bytes)
    command = [git_executable, '-C', str(repository), '--no-replace-objects']
    environment = os.environ.copy()
    environment['GIT_NO_LAZY_FETCH'] = '1'
    environment['GIT_OPTIONAL_LOCKS'] = '0'
    blob = _selected_blob(command, environment, commit, path)
    length = int(_git(command, environment, 'cat-file', '-s', blob).strip())
    if length > maximum_bytes:
        raise AssetCacheError(f'Git blob exceeds maximum_bytes={maximum_bytes}')
    if length != expected.length:
        raise AssetIdentityMismatch(
            f'Expected {expected.length} bytes, Git blob has {length}'
        )
    try:
        process = subprocess.Popen(
            [*command, 'cat-file', 'blob', blob],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError as error:
        raise AssetCacheError(f'Git blob reader could not start: {error}') from error
    try:
        assert process.stdout is not None
        prefix = process.stdout.read(min(length, 256))
        if prefix.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            raise AssetCacheError(f'Git path is an unresolved LFS pointer: {path}')
        entry = store.import_stream(
            _GitReader(process, process.stdout, prefix),
            maximum_bytes=maximum_bytes,
            source_key=source_key,
            category=AssetCategory.acquired,
            source_kind=SourceKind.git_file,
            media_kind=media_kind,
            expected=expected,
        )
        return entry, blob
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


def import_remote_git_file(
    store: AssetStore,
    repository_url: str,
    transport_repository: Path,
    commit: str,
    path: str,
    expected: ObjectIdentity,
    *,
    maximum_bytes: int,
    source_key: str,
    media_kind: MediaKind = MediaKind.other,
    git_executable: str = 'git',
) -> tuple[AssetEntry, str]:
    """Fetch a selected blob into a host-approved, quota-limited bare repository.

    The host authorizes the URL and supplies a bare repository on storage with
    a hard transport quota. Git may need more than the selected blob's size;
    quota exhaustion fails the acquisition without publishing an asset.
    """
    _validate_request(commit, path, expected, maximum_bytes)
    command = [git_executable, '-C', str(transport_repository), '--no-replace-objects']
    environment = os.environ.copy()
    environment['GIT_NO_LAZY_FETCH'] = '1'
    environment['GIT_OPTIONAL_LOCKS'] = '0'
    environment['GIT_TERMINAL_PROMPT'] = '0'
    _git(
        command,
        environment,
        'fetch',
        '--filter=blob:none',
        '--no-tags',
        '--no-recurse-submodules',
        '--no-auto-gc',
        '--no-write-fetch-head',
        '--',
        repository_url,
        commit,
        timeout=120,
    )
    blob = _selected_blob(command, environment, commit, path)
    try:
        _git(command, environment, 'cat-file', '-e', blob)
    except AssetCacheError:
        try:
            _git(
                command,
                environment,
                'fetch',
                '--no-tags',
                '--no-recurse-submodules',
                '--no-auto-gc',
                '--no-write-fetch-head',
                '--',
                repository_url,
                blob,
                timeout=120,
            )
        except AssetCacheError:
            _git(
                command,
                environment,
                'fetch',
                '--no-tags',
                '--no-recurse-submodules',
                '--no-auto-gc',
                '--no-write-fetch-head',
                '--',
                repository_url,
                commit,
                timeout=120,
            )
    return import_local_git_file(
        store,
        transport_repository,
        commit,
        path,
        expected,
        maximum_bytes=maximum_bytes,
        source_key=source_key,
        media_kind=media_kind,
        git_executable=git_executable,
    )


def _selected_blob(
    command: list[str], environment: dict[str, str], commit: str, path: str
) -> str:
    if _git(command, environment, 'cat-file', '-t', commit).strip() != b'commit':
        raise AssetCacheError(f'Git object is not a commit: {commit}')
    tree = _git(command, environment, 'ls-tree', '-z', commit, '--', path)
    matches = [item for item in tree.split(b'\0') if item]
    if len(matches) != 1:
        raise AssetCacheError(f'Git path is missing or ambiguous: {path}')
    try:
        description, observed_path = matches[0].split(b'\t', 1)
        mode, kind, blob_id = description.split(b' ')
    except ValueError as error:
        raise AssetCacheError(f'Invalid Git tree entry for {path}') from error
    if (
        observed_path != os.fsencode(path)
        or mode not in {b'100644', b'100755'}
        or kind != b'blob'
    ):
        raise AssetCacheError(f'Git path is not a regular blob: {path}')
    return blob_id.decode('ascii')


def _validate_request(
    commit: str, path: str, expected: ObjectIdentity, maximum_bytes: int
) -> None:
    if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', commit):
        raise ValueError('commit must be a full lowercase Git object ID')
    parts = path.split('/')
    if (
        not path
        or path.startswith('/')
        or '\\' in path
        or PureWindowsPath(path).drive
        or any(part in {'', '.', '..'} for part in parts)
    ):
        raise ValueError('Git path must be a relative portable path')
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError('maximum_bytes must be a positive integer')
    if expected.length > maximum_bytes:
        raise AssetCacheError(f'Git blob exceeds maximum_bytes={maximum_bytes}')


def _git(
    command: list[str], environment: dict[str, str], *arguments: str, timeout: int = 10
) -> bytes:
    try:
        result = subprocess.run(
            [*command, *arguments],
            capture_output=True,
            env=environment,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AssetCacheError(f'Git object lookup failed: {error}') from error
    if result.returncode:
        raise AssetCacheError(
            f'Git object lookup failed with status {result.returncode}'
        )
    return result.stdout


class _GitReader:
    def __init__(
        self, process: subprocess.Popen[bytes], stream: IO[bytes], prefix: bytes
    ) -> None:
        self.process = process
        self.stream = stream
        self.prefix = prefix

    def read(self, size: int = -1) -> bytes:
        if self.prefix:
            result = self.prefix if size < 0 else self.prefix[:size]
            self.prefix = self.prefix[len(result) :]
            return result
        result = self.stream.read(size)
        if not result and self.process.wait() != 0:
            raise AssetCacheError('Git blob read failed')
        return result
