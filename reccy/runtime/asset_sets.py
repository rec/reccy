"""Prepare host-authorized finite assets and publish verified file packages."""

import ctypes
import hashlib
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from typing import BinaryIO

from pydantic import BaseModel, Field, field_validator

from .assets import (
    AssetCacheError,
    AssetIdentityMismatch,
    ObjectIdentity,
    open_verified_file,
)


class AssetFailure(BaseModel, frozen=True):
    label: str
    error: str


class AssetPreparationError(AssetCacheError):
    """All preparation failures, without leaking acquisition exception details."""

    def __init__(self, failures: list[AssetFailure]) -> None:
        self.failures = failures
        super().__init__('; '.join(f'{f.label}: {f.error}' for f in failures))


class PackageManifest(BaseModel, frozen=True):
    files: dict[str, ObjectIdentity] = Field(default_factory=dict)

    @field_validator('files')
    @classmethod
    def confined_paths(
        cls, files: dict[str, ObjectIdentity]
    ) -> dict[str, ObjectIdentity]:
        for name in files:
            if (
                not name
                or '\\' in name
                or PureWindowsPath(name).drive
                or any(p in {'', '.', '..'} for p in name.split('/'))
            ):
                raise ValueError(f'Package path must be relative and portable: {name}')
        paths = {Path(p) for p in files}
        if any(p in paths for q in paths for p in q.parents):
            raise ValueError('Package file paths conflict')
        return files


class PreparedAssets:
    """Verified streams valid only inside their preparation context."""

    def __init__(
        self, streams: dict[str, BinaryIO], identities: dict[str, ObjectIdentity]
    ) -> None:
        self.streams = streams
        self.identities = identities


@contextmanager
def prepare_assets(
    requests: Mapping[str, AbstractContextManager[BinaryIO]],
) -> Iterator[PreparedAssets]:
    """Open every authorized request before yielding, keeping all leases alive.

    Hosts construct acquisition contexts and implement policy, offline selection,
    and media validation. Labels must be public diagnostic names, not secret URLs.
    Interruptions propagate immediately; ordinary acquisition failures aggregate.
    """
    with ExitStack() as stack:
        streams: dict[str, BinaryIO] = {}
        identities: dict[str, ObjectIdentity] = {}
        failures: list[AssetFailure] = []
        for label, request in requests.items():
            try:
                stream = stack.enter_context(request)
                identities[label] = stream_identity(stream)
                streams[label] = stream
            except (OSError, ValueError, AssetCacheError) as error:
                failures.append(AssetFailure(label=label, error=type(error).__name__))
        if failures:
            raise AssetPreparationError(failures)
        yield PreparedAssets(streams, identities)


def stream_identity(stream: BinaryIO) -> ObjectIdentity:
    """Measure a seekable finite stream and rewind it for its consumer."""
    stream.seek(0)
    digest = hashlib.sha256()
    length = 0
    while block := stream.read(1024 * 1024):
        digest.update(block)
        length += len(block)
    stream.seek(0)
    return ObjectIdentity(sha256=digest.hexdigest(), length=length)


def verify_package(root: Path, manifest: PackageManifest) -> None:
    """Verify all declared members beneath a host-controlled immutable root."""
    with prepare_assets(
        {
            n: open_verified_file(root, n, i, trusted_immutable=True)
            for n, i in manifest.files.items()
        }
    ):
        pass


def write_package(
    root: Path, streams: Mapping[str, BinaryIO], manifest: PackageManifest
) -> None:
    """Write and verify members in a new private staging directory.

    The caller owns staging and publication. Existing files are never overwritten.
    Streams remain caller-owned and are rewound before and after copying.
    """
    if set(streams) != set(manifest.files):
        raise ValueError('Package streams do not match manifest paths')
    if root.is_symlink():
        raise ValueError('Package staging root must not be a symlink')
    root = root.resolve()
    for name, identity in manifest.files.items():
        target = root / name
        if any(
            p.is_symlink() for p in [target, *target.parents] if p.is_relative_to(root)
        ):
            raise ValueError(f'Unsafe package destination: {name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        source = streams[name]
        source.seek(0)
        with target.open('xb') as destination:
            remaining = identity.length
            while block := source.read(min(1024 * 1024, remaining + 1)):
                if len(block) > remaining:
                    raise AssetIdentityMismatch(f'Package bytes grew: {name}')
                destination.write(block)
                remaining -= len(block)
        source.seek(0)
        with target.open('rb') as destination:
            if stream_identity(destination) != identity:
                raise AssetIdentityMismatch(f'Package bytes changed: {name}')
    verify_package(root, manifest)


def publish_directory(staging: Path, destination: Path) -> None:
    """Atomically publish a new directory, never replacing an existing package."""
    if sys.platform == 'win32':
        staging.rename(destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        rename = getattr(libc, 'renamex_np', None)
        if rename is None:
            raise AssetCacheError('Exclusive directory rename is unavailable')
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = (os.fsencode(staging), os.fsencode(destination), 4)
    elif sys.platform.startswith('linux'):
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise AssetCacheError('Exclusive directory rename is unavailable')
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        arguments = (-100, os.fsencode(staging), -100, os.fsencode(destination), 1)
    else:
        raise AssetCacheError('Exclusive directory rename is unavailable')
    rename.restype = ctypes.c_int
    if rename(*arguments) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def export_package(
    destination: Path, streams: Mapping[str, BinaryIO], manifest: PackageManifest
) -> None:
    """Publish a complete new package after copying and verifying every member."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=f'.{destination.name}-', dir=destination.parent
    ) as directory:
        staging = Path(directory) / 'package'
        staging.mkdir()
        write_package(staging, streams, manifest)
        publish_directory(staging, destination)
