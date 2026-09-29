"""Cache host-declared deterministic finite provider values."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, Field

from .assets import (
    AssetCacheError,
    AssetCategory,
    AssetCorruptionError,
    AssetEntry,
    AssetStore,
    MediaKind,
    ObjectIdentity,
    SourceKind,
)
from .claims import ResourceClaim
from .files import atomic_output


class AssetValueConflict(AssetCacheError):
    """One declared deterministic key produced conflicting bytes."""


class ValueRecord(BaseModel, frozen=True):
    """Persistent value identity, independent of whether its bytes remain."""

    source_key: str = Field(pattern=r'^v1:[0-9a-f]{64}$')
    object: ObjectIdentity
    media_kind: MediaKind
    entry_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    invalidated: bool = False


@contextmanager
def open_deterministic_asset(
    store: AssetStore,
    source_key: str,
    produce: Callable[[], bytes],
    *,
    maximum_bytes: int,
    media_kind: MediaKind = MediaKind.other,
) -> Iterator[tuple[AssetEntry, BinaryIO]]:
    """Reuse a verified value or produce and remember its first byte identity.

    The host asserts determinism and supplies a key containing every effective
    input and implementation fact. The producer only runs on a cache miss.
    If the entry was collected, regeneration must match the original bytes.
    """
    if not re.fullmatch(r'v1:[0-9a-f]{64}', source_key):
        raise ValueError('source_key must be a versioned source fingerprint')
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError('maximum_bytes must be a positive integer')
    identifier = source_key[3:]
    record_path = store.root / 'state' / 'values' / f'{identifier}.json'
    claim_path = store.root / 'state' / 'value-claims' / f'{identifier}.lock'
    with ExitStack() as stack:
        with ResourceClaim(claim_path, timeout=5):
            record = _read_record(record_path)
            if record is not None and record.source_key != source_key:
                raise AssetCacheError('Value record has the wrong source key')
            if record is not None and record.invalidated:
                raise AssetValueConflict('Deterministic value key was invalidated')
            if record is not None and record.media_kind is not media_kind:
                raise AssetValueConflict('Deterministic key has a different media kind')
            if record is not None and record.object.length > maximum_bytes:
                raise AssetCacheError(f'Asset exceeds maximum_bytes={maximum_bytes}')
            entry = _retained_entry(store, record)
            file: BinaryIO | None = None
            if entry is not None:
                try:
                    file = stack.enter_context(store.open_entry(entry.id))
                except AssetCorruptionError:
                    raise
                except AssetCacheError:
                    entry = None
            if entry is None:
                contents = produce()
                if type(contents) is not bytes:
                    raise TypeError('deterministic producer must return bytes')
                if len(contents) > maximum_bytes:
                    raise AssetCacheError(
                        f'Asset exceeds maximum_bytes={maximum_bytes}'
                    )
                identity = ObjectIdentity(
                    sha256=hashlib.sha256(contents).hexdigest(), length=len(contents)
                )
                if record is not None and record.object != identity:
                    _write_record(
                        store,
                        record_path,
                        record.model_copy(
                            update={'invalidated': True, 'entry_id': None}
                        ),
                    )
                    raise AssetValueConflict(
                        'Deterministic value changed for the same source key'
                    )
                if record is None:
                    _write_record(
                        store,
                        record_path,
                        ValueRecord(
                            source_key=source_key,
                            object=identity,
                            media_kind=media_kind,
                        ),
                    )
                entry = store.import_bytes(
                    contents,
                    source_key=source_key,
                    category=AssetCategory.generated,
                    source_kind=SourceKind.complete_array,
                    media_kind=media_kind,
                    expected=identity,
                )
                _write_record(
                    store,
                    record_path,
                    ValueRecord(
                        source_key=source_key,
                        object=identity,
                        media_kind=media_kind,
                        entry_id=entry.id,
                    ),
                )
                file = stack.enter_context(store.open_entry(entry.id))
            assert file is not None
        yield entry, file


def _retained_entry(store: AssetStore, record: ValueRecord | None) -> AssetEntry | None:
    if record is None or record.entry_id is None:
        return None
    try:
        entry = store.entry(record.entry_id)
    except AssetCacheError:
        return None
    if (
        entry.source_key != record.source_key
        or entry.object != record.object
        or entry.media_kind is not record.media_kind
    ):
        raise AssetCacheError('Value record points to a different asset entry')
    return entry


def _read_record(path: Path) -> ValueRecord | None:
    try:
        return ValueRecord.model_validate_json(path.read_text())
    except FileNotFoundError:
        return None


def _write_record(store: AssetStore, path: Path, record: ValueRecord) -> None:
    with ResourceClaim(store.root / 'state' / 'metadata.lock', timeout=5):
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_output(path, sync=True) as temporary:
            temporary.write_text(record.model_dump_json() + '\n')
