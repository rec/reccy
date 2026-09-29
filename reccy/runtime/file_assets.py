"""Resolve host-approved file roots and open finite verified asset bytes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, Field

from .assets import (
    AssetCacheError,
    AssetStore,
    MediaKind,
    ObjectIdentity,
    SourceKind,
    open_verified_file,
)


class VolumeNotFound(AssetCacheError):
    """No currently mounted volume has the requested stable ID."""


class VolumeAmbiguous(AssetCacheError):
    """The host reported more than one mount for a stable volume ID."""


class VolumeMismatch(AssetCacheError):
    """The host's measured volume has no available mount root."""


class VolumeMount(BaseModel, frozen=True):
    """A host-measured stable volume identity and its current mount root."""

    volume_id: str = Field(min_length=1)
    volume_name: str | None = None
    root: Path


def resolve_volume_root(
    volume_id: str,
    volume_name: str | None,
    mounts: list[VolumeMount],
) -> Path:
    """Map an authored ID to exactly one currently mounted, matching root."""
    matches = [m for m in mounts if m.volume_id == volume_id]
    if not matches:
        description = f'{volume_id} ({volume_name})' if volume_name else volume_id
        raise VolumeNotFound(f'Volume is not mounted: {description}')
    if len(matches) != 1:
        raise VolumeAmbiguous(f'Volume ID has multiple mounts: {volume_id}')
    mount = matches[0]
    if not mount.root.is_dir():
        raise VolumeMismatch(f'Volume root is unavailable for ID {volume_id}')
    return mount.root


@contextmanager
def open_file_asset(
    store: AssetStore,
    root: Path,
    relative_path: str,
    expected: ObjectIdentity,
    *,
    source_key: str,
    source_kind: SourceKind,
    trusted_immutable: bool,
    maximum_bytes: int,
    media_kind: MediaKind = MediaKind.other,
) -> Iterator[BinaryIO]:
    """Open trusted bytes in place or a verified snapshot of a mutable file.

    The host selects and authorizes ``root`` and supplies its stable source
    context in ``source_key``. A trusted source must remain immutable through
    consumption; other sources are copied before they are exposed.
    """
    if source_kind not in {SourceKind.local_file, SourceKind.volume_file}:
        raise ValueError('file asset requires a local or volume source kind')
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError('maximum_bytes must be a positive integer')
    if expected.length > maximum_bytes:
        raise AssetCacheError(f'Asset exceeds maximum_bytes={maximum_bytes}')
    if trusted_immutable:
        with open_verified_file(
            root, relative_path, expected, trusted_immutable=True
        ) as file:
            yield file
    else:
        entry = store.import_file(
            root,
            relative_path,
            maximum_bytes=maximum_bytes,
            source_key=source_key,
            source_kind=source_kind,
            expected=expected,
            media_kind=media_kind,
        )
        with store.open_entry(entry.id) as file:
            yield file
