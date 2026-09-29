"""Private verified storage for finite host asset bytes.

This module stores opaque finite bytes. Source acquisition, provider execution,
and real-time capture stay with their host-specific adapters.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from enum import auto
from math import isfinite
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile
from typing import BinaryIO, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator
from strenum import StrEnum

from .claims import ResourceClaim
from .files import atomic_output
from .http_freshness import HTTPRecord, response_freshness


class AssetCacheError(RuntimeError):
    """An asset-store operation could not complete."""


class AssetCorruptionError(AssetCacheError):
    """Stored bytes do not match their immutable object identity."""


class AssetIdentityMismatch(AssetCacheError):
    """Admitted bytes do not match the caller's expected object identity."""


class AssetCacheMiss(AssetCacheError):
    """No retained entry in this credential scope has the requested bytes."""


class AssetInsufficientSpace(AssetCacheError):
    """An admission would exceed a configured storage or free-space budget."""


class AssetCategory(StrEnum):
    acquired = auto()
    generated = auto()
    derived = auto()


class SourceKind(StrEnum):
    local_file = auto()
    volume_file = auto()
    download = auto()
    git_file = auto()
    streaming_url = auto()
    complete_array = auto()
    callback = auto()
    client_buffer = auto()


class MediaKind(StrEnum):
    audio = auto()
    midi = auto()
    image = auto()
    other = auto()


class ObjectIdentity(BaseModel, frozen=True):
    """The content-addressed identity of one finite immutable byte sequence."""

    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    length: int = Field(ge=0)


def source_fingerprint(
    location: dict[str, object],
    context: dict[str, object],
    expected: ObjectIdentity | None,
    representation: dict[str, object],
    *,
    lookup_secret: bytes | None = None,
    fingerprint_key: bytes | None = None,
) -> str:
    """Identify a resolved request without storing its credential material.

    Callers provide effective, public request facts. A secret that changes the
    lookup must be supplied separately with a stable host-private HMAC key.
    The resulting key is only an index, never acquisition authorization.
    """
    for value in (location, context, representation):
        if type(value) is not dict:
            raise ValueError('source fingerprint fields must be JSON objects')
        _validate_fingerprint_json(value)
    if (lookup_secret is None) != (fingerprint_key is None):
        raise ValueError('lookup_secret and fingerprint_key must be supplied together')
    if fingerprint_key is not None and len(fingerprint_key) < 32:
        raise ValueError('fingerprint_key must contain at least 32 bytes')
    request = {
        'location': location,
        'context': context,
        'expected': None if expected is None else expected.model_dump(mode='json'),
        'representation': representation,
        'secret': (
            None
            if lookup_secret is None or fingerprint_key is None
            else hmac.digest(fingerprint_key, lookup_secret, 'sha256').hex()
        ),
    }
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')
    return f'v1:{hashlib.sha256(encoded).hexdigest()}'


@contextmanager
def open_verified_file(
    root: Path,
    relative_path: str,
    expected: ObjectIdentity,
    *,
    trusted_immutable: bool,
) -> Iterator[BinaryIO]:
    """Read a trusted immutable file in place through its verified handle."""
    if not trusted_immutable:
        raise ValueError('direct reads require a trusted immutable source')
    with _open_file_beneath(root, relative_path) as file:
        _verify_file(file, expected)
        file.seek(0)
        yield file


class AssetEntry(BaseModel, frozen=True):
    """Immutable facts for one acquisition or materialization."""

    version: int = 1
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r'^[0-9a-f]{32}$')
    object: ObjectIdentity
    source_key: str = Field(pattern=r'^v1:[0-9a-f]{64}$')
    category: AssetCategory
    source_kind: SourceKind
    media_kind: MediaKind = MediaKind.other
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tags: list[str] = Field(default_factory=list)

    @field_validator('created_at')
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError('created_at must be UTC')
        return value


class AssetReference(BaseModel, frozen=True):
    """A mutable host-local retention root pointing at one immutable entry."""

    entry_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class AssetPin(BaseModel, frozen=True):
    """An immutable retention root, optionally expiring at a UTC instant."""

    entry_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    expires_at: datetime | None = None

    @field_validator('expires_at')
    @classmethod
    def validate_expires_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError('expires_at must be UTC')
        return value


class AssetLease(BaseModel, frozen=True):
    """A temporary root for an entry while a caller consumes its bytes."""

    entry_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class RetentionSince(StrEnum):
    created = auto()
    access = auto()


class RetentionDuration(BaseModel, frozen=True):
    """One positive whole-unit retention duration."""

    seconds: int | None = Field(default=None, gt=0)
    minutes: int | None = Field(default=None, gt=0)
    hours: int | None = Field(default=None, gt=0)
    days: int | None = Field(default=None, gt=0)
    since: RetentionSince = RetentionSince.access

    @model_validator(mode='after')
    def validate_one_unit(self) -> RetentionDuration:
        if (
            sum(
                value is not None
                for value in (self.seconds, self.minutes, self.hours, self.days)
            )
            != 1
        ):
            raise ValueError('retention duration requires exactly one unit')
        return self

    def total_seconds(self) -> int:
        values = {
            'seconds': self.seconds,
            'minutes': self.minutes,
            'hours': self.hours,
            'days': self.days,
        }
        unit, value = next((key, value) for key, value in values.items() if value)
        assert value is not None
        return value * {'seconds': 1, 'minutes': 60, 'hours': 3600, 'days': 86400}[unit]


class RetentionMatch(BaseModel, frozen=True):
    """An AND selector over immutable entry facts."""

    category: list[AssetCategory] | None = None
    source_kind: list[SourceKind] | None = None
    media_kind: list[MediaKind] | None = None
    source_key: list[str] | None = None
    tags: list[str] | None = None

    @model_validator(mode='after')
    def validate_nonempty(self) -> RetentionMatch:
        if all(
            value is None
            for value in (
                self.category,
                self.source_kind,
                self.media_kind,
                self.source_key,
                self.tags,
            )
        ):
            raise ValueError('retention match must select at least one field')
        return self

    def matches(self, entry: AssetEntry) -> bool:
        return (
            (self.category is None or entry.category in self.category)
            and (self.source_kind is None or entry.source_kind in self.source_kind)
            and (self.media_kind is None or entry.media_kind in self.media_kind)
            and (self.source_key is None or entry.source_key in self.source_key)
            and (self.tags is None or bool(set(entry.tags) & set(self.tags)))
        )


class RetentionNewest(BaseModel, frozen=True):
    """Retain the newest matching entries, optionally within each source."""

    count: int = Field(gt=0)
    group_by: str = Field(default='source', pattern=r'^(source|all)$')


class RetentionRule(BaseModel, frozen=True):
    """One additive protection or retention rule for finite entries."""

    name: str = Field(min_length=1)
    match: RetentionMatch | None = None
    all: bool = False
    protect: RetentionDuration | str | None = None
    retain: RetentionDuration | str | None = None
    newest: RetentionNewest | None = None

    @model_validator(mode='after')
    def validate_rule(self) -> RetentionRule:
        if self.match is None and not self.all:
            raise ValueError('retention rule requires match or all=true')
        if self.match is not None and self.all:
            raise ValueError('retention rule cannot combine match and all=true')
        if self.protect is not None and (self.retain is not None or self.newest):
            raise ValueError('retention rule requires exactly one action family')
        if self.protect is None and self.retain is None and self.newest is None:
            raise ValueError('retention rule requires protect, retain, or newest')
        action = self.protect if self.protect is not None else self.retain
        if isinstance(action, str) and action not in {'forever', 'while_fresh'}:
            raise ValueError(
                'retention action must be forever, while_fresh, or a duration'
            )
        if self.protect == 'while_fresh':
            raise ValueError('while_fresh is a retain action')
        if self.retain == 'while_fresh' and (
            self.match is None
            or self.match.source_kind is None
            or set(self.match.source_kind) != {SourceKind.download}
        ):
            raise ValueError('while_fresh requires a download-only source-kind match')
        if self.newest is not None and self.retain == 'forever':
            raise ValueError('newest cannot be combined with retain=forever')
        return self

    def matches(self, entry: AssetEntry) -> bool:
        return self.all or (self.match is not None and self.match.matches(entry))


class RetentionDecision(BaseModel, frozen=True):
    """Why an entry is or is not eligible for one collection mode."""

    entry_id: str
    rooted: bool
    protected: bool
    retained: bool
    matching_rules: list[str]
    newest_ranks: dict[str, int] = Field(default_factory=dict)

    @property
    def eligible_for_ordinary_collection(self) -> bool:
        return not self.rooted and not self.protected and not self.retained

    @property
    def eligible_for_pressure_collection(self) -> bool:
        return not self.rooted and not self.protected


class AssetAccess(BaseModel, frozen=True):
    """Mutable evidence that a caller successfully consumed an entry."""

    consumed_at: datetime

    @field_validator('consumed_at')
    @classmethod
    def validate_consumed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError('consumed_at must be UTC')
        return value


class AssetCapacity(BaseModel, frozen=True):
    """Installation-specific byte budgets for one credential scope."""

    maximum_object_bytes: int = Field(gt=0)
    maximum_staging_bytes: int = Field(gt=0)
    minimum_free_space: int = Field(ge=0)


class RecoveryKind(StrEnum):
    staging = auto()
    capture_recovery = auto()
    orphan_object = auto()


class RecoveryItem(BaseModel, frozen=True):
    """Unreferenced store bytes requiring operator review before reclamation."""

    kind: RecoveryKind
    path: str
    byte_length: int = Field(ge=0)


class AssetStore:
    """A cooperating-process store of verified finite bytes and entry manifests.

    ``root`` is a host-owned private directory. Each credential scope gets a
    separate store beneath it; callers must derive the scope from host authority,
    never from an untrusted source key. Every host process using one scope must
    use this class so publication and retention-root updates share the claim.
    """

    def __init__(
        self,
        root: Path,
        *,
        credential_scope: str,
        capacity: AssetCapacity | None = None,
    ) -> None:
        if not credential_scope:
            raise ValueError('credential_scope must be a nonempty host-owned ID')
        self.root = (
            root
            / 'scopes'
            / hashlib.sha256(credential_scope.encode('utf-8')).hexdigest()
        )
        self.capacity = capacity

    def import_bytes(
        self,
        contents: bytes,
        *,
        source_key: str,
        category: AssetCategory,
        source_kind: SourceKind,
        media_kind: MediaKind = MediaKind.other,
        expected: ObjectIdentity | None = None,
        tags: list[str] | None = None,
        pin: bool = False,
    ) -> AssetEntry:
        """Store verified bytes, optionally pinning before publishing the entry."""
        identity = ObjectIdentity(
            sha256=hashlib.sha256(contents).hexdigest(), length=len(contents)
        )
        if expected is not None and identity != expected:
            raise AssetIdentityMismatch(
                f'Expected {expected.sha256}/{expected.length}, got '
                f'{identity.sha256}/{identity.length}'
            )
        entry = AssetEntry(
            object=identity,
            source_key=source_key,
            category=category,
            source_kind=source_kind,
            media_kind=media_kind,
            tags=[] if tags is None else tags,
        )
        self._prepare_directories()
        with self._admission():
            with self._stage(contents) as staged:
                self._publish(staged, entry, pin=pin)
        return entry

    def import_stream(
        self,
        source: _ByteReader,
        *,
        maximum_bytes: int,
        source_key: str,
        category: AssetCategory,
        source_kind: SourceKind,
        media_kind: MediaKind = MediaKind.other,
        expected: ObjectIdentity | None = None,
        tags: list[str] | None = None,
        pin: bool = False,
    ) -> AssetEntry:
        """Stage and verify a finite source without keeping its body in memory."""
        if type(maximum_bytes) is not int or maximum_bytes <= 0:
            raise ValueError('maximum_bytes must be a positive integer')
        self._prepare_directories()
        with self._admission():
            with self._stage_stream(source, maximum_bytes) as (staged, identity):
                if expected is not None and identity != expected:
                    raise AssetIdentityMismatch(
                        f'Expected {expected.sha256}/{expected.length}, got '
                        f'{identity.sha256}/{identity.length}'
                    )
                entry = AssetEntry(
                    object=identity,
                    source_key=source_key,
                    category=category,
                    source_kind=source_kind,
                    media_kind=media_kind,
                    tags=[] if tags is None else tags,
                )
                self._publish(staged, entry, pin=pin)
        return entry

    def import_file(
        self,
        root: Path,
        relative_path: str,
        *,
        maximum_bytes: int,
        source_key: str,
        source_kind: SourceKind,
        expected: ObjectIdentity,
        media_kind: MediaKind = MediaKind.other,
        pin: bool = False,
    ) -> AssetEntry:
        """Copy a confined file into a verified immutable cache snapshot."""
        if source_kind not in {SourceKind.local_file, SourceKind.volume_file}:
            raise ValueError('import_file requires a local or volume source')
        if type(maximum_bytes) is not int or maximum_bytes <= 0:
            raise ValueError('maximum_bytes must be a positive integer')
        if expected.length > maximum_bytes:
            raise AssetCacheError(f'Asset exceeds maximum_bytes={maximum_bytes}')
        with _open_file_beneath(root, relative_path) as file:
            return self.import_stream(
                file,
                maximum_bytes=maximum_bytes,
                source_key=source_key,
                category=AssetCategory.acquired,
                source_kind=source_kind,
                media_kind=media_kind,
                expected=expected,
                pin=pin,
            )

    def entry(self, entry_id: str) -> AssetEntry:
        """Read an immutable entry manifest without treating it as consumption."""
        return self._read_model(self._entry_path(entry_id), AssetEntry)

    def verify_object(self, identity: ObjectIdentity) -> None:
        """Check stored bytes, rather than trusting an existing pathname or size."""
        path = self.object_path(identity)
        try:
            with path.open('rb') as file:
                _verify_file(file, identity)
        except FileNotFoundError as error:
            raise AssetCorruptionError(
                f'Missing asset object {identity.sha256}'
            ) from error

    @contextmanager
    def open_entry(self, entry_id: str) -> Iterator[BinaryIO]:
        """Open verified bytes while a durable lease prevents their collection."""
        with self._open_selected(entry_id=entry_id) as file:
            yield file

    @contextmanager
    def open_expected(self, expected: ObjectIdentity) -> Iterator[BinaryIO]:
        """Open retained bytes by identity after the host authorizes the request."""
        with self._open_selected(expected=expected) as file:
            yield file

    def set_reference(self, name: str, entry_id: str) -> None:
        """Create or atomically move a named root to an existing entry."""
        self._validate_name(name)
        with ResourceClaim(self._metadata_lock(), timeout=5):
            self.entry(entry_id)
            self._write_model(
                self.root / 'state' / 'references' / f'{name}.json',
                AssetReference(entry_id=entry_id),
            )

    def remove_reference(self, name: str) -> None:
        """Remove one named root without collecting the entry immediately."""
        self._validate_name(name)
        with ResourceClaim(self._metadata_lock(), timeout=5):
            (self.root / 'state' / 'references' / f'{name}.json').unlink(
                missing_ok=True
            )

    def add_pin(self, entry_id: str, *, expires_at: datetime | None = None) -> str:
        """Add an immutable pin and return its opaque ID."""
        pin_id = uuid4().hex
        with ResourceClaim(self._metadata_lock(), timeout=5):
            self.entry(entry_id)
            self._write_new_model(
                self.root / 'state' / 'pins' / f'{pin_id}.json',
                AssetPin(entry_id=entry_id, expires_at=expires_at),
            )
        return pin_id

    def remove_pin(self, pin_id: str) -> None:
        """Remove one pin without collecting its entry immediately."""
        if not re.fullmatch(r'[0-9a-f]{32}', pin_id):
            raise ValueError('pin_id must be an opaque asset ID')
        with ResourceClaim(self._metadata_lock(), timeout=5):
            (self.root / 'state' / 'pins' / f'{pin_id}.json').unlink(missing_ok=True)

    def explain_retention(
        self,
        entry_id: str,
        rules: list[RetentionRule],
        *,
        now: datetime | None = None,
    ) -> RetentionDecision:
        """Explain roots and additive retention at one immutable time snapshot."""
        current = self._utc_now(now)
        with ResourceClaim(self._metadata_lock(), timeout=5):
            entry = self.entry(entry_id)
            roots = self._root_entry_ids(current)
            access = self._access_time(entry.id)
            newest = self._newest_matches(self._entries(), rules)
            fresh = (
                self._fresh_entry_ids(current)
                if any(rule.retain == 'while_fresh' for rule in rules)
                else set()
            )
        return self._retention_decision(
            entry, rules, current, roots, access, newest, fresh
        )

    def plan_collection(
        self,
        rules: list[RetentionRule],
        *,
        pressure: bool = False,
        now: datetime | None = None,
    ) -> list[RetentionDecision]:
        """List deletion candidates without changing metadata or payloads."""
        current = self._utc_now(now)
        self._validate_rule_names(rules)
        candidates: list[RetentionDecision] = []
        with ResourceClaim(self._metadata_lock(), timeout=5):
            roots = self._root_entry_ids(current)
            entries = self._entries()
            newest = self._newest_matches(entries, rules)
            fresh = (
                self._fresh_entry_ids(current)
                if any(rule.retain == 'while_fresh' for rule in rules)
                else set()
            )
            for entry in entries:
                decision = self._retention_decision(
                    entry,
                    rules,
                    current,
                    roots,
                    self._access_time(entry.id),
                    newest,
                    fresh,
                )
                if (
                    decision.eligible_for_pressure_collection
                    if pressure
                    else decision.eligible_for_ordinary_collection
                ):
                    candidates.append(decision)
        return candidates

    def collect(
        self,
        rules: list[RetentionRule],
        *,
        pressure: bool = False,
        now: datetime | None = None,
    ) -> list[str]:
        """Delete eligible entries, retaining orphan objects for crash analysis."""
        current = self._utc_now(now)
        planned = self.plan_collection(rules, pressure=pressure, now=current)
        deleted: list[str] = []
        with ResourceClaim(self._metadata_lock(), timeout=5):
            roots = self._root_entry_ids(current)
            entries = {e.id: e for e in self._entries()}
            newest = self._newest_matches(list(entries.values()), rules)
            fresh = (
                self._fresh_entry_ids(current)
                if any(rule.retain == 'while_fresh' for rule in rules)
                else set()
            )
            object_counts: dict[tuple[str, int], int] = {}
            for entry in entries.values():
                key = (entry.object.sha256, entry.object.length)
                object_counts[key] = object_counts.get(key, 0) + 1
            for decision in planned:
                if (entry := entries.get(decision.entry_id)) is None:
                    continue
                renewed = self._retention_decision(
                    entry,
                    rules,
                    current,
                    roots,
                    self._access_time(entry.id),
                    newest,
                    fresh,
                )
                eligible = (
                    renewed.eligible_for_pressure_collection
                    if pressure
                    else renewed.eligible_for_ordinary_collection
                )
                if not eligible:
                    continue
                self._entry_path(entry.id).unlink()
                key = (entry.object.sha256, entry.object.length)
                object_counts[key] -= 1
                if object_counts[key] == 0:
                    self.object_path(entry.object).unlink(missing_ok=True)
                deleted.append(entry.id)
        return deleted

    def object_path(self, identity: ObjectIdentity) -> Path:
        return self.root / 'objects' / 'sha256' / identity.sha256[:2] / identity.sha256

    def export_entry(self, entry_id: str, destination: Path) -> ObjectIdentity:
        """Atomically copy one verified entry to a host-approved destination."""
        with self.open_entry(entry_id) as source:
            identity = self.entry(entry_id).object
            with atomic_output(destination, sync=True) as temporary:
                with temporary.open('wb') as target:
                    shutil.copyfileobj(source, target, length=65536)
        return identity

    def inspect_recovery(self) -> list[RecoveryItem]:
        """Report staged and orphan bytes without deleting possibly live work.

        A staging file can belong to an active writer; this report cannot
        classify it as abandoned until writer ownership is recorded.
        """
        with ResourceClaim(self._metadata_lock(), timeout=5):
            referenced = {self.object_path(e.object) for e in self._entries()}
            staging = self.root / 'staging'
            objects = self.root / 'objects' / 'sha256'
            found: list[RecoveryItem] = []
            for kind, paths in (
                (RecoveryKind.staging, staging.glob('*')),
                (RecoveryKind.orphan_object, objects.glob('*/*')),
            ):
                for path in paths:
                    if kind is RecoveryKind.orphan_object and path in referenced:
                        continue
                    try:
                        info = path.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        item_kind = (
                            RecoveryKind.capture_recovery
                            if kind is RecoveryKind.staging
                            and re.fullmatch(r'capture-[0-9a-f]{32}\.json', path.name)
                            else kind
                        )
                        found.append(
                            RecoveryItem(
                                kind=item_kind,
                                path=str(path.relative_to(self.root)),
                                byte_length=info.st_size,
                            )
                        )
            return sorted(found, key=lambda item: item.path)

    @contextmanager
    def _open_selected(
        self,
        *,
        entry_id: str | None = None,
        expected: ObjectIdentity | None = None,
    ) -> Iterator[BinaryIO]:
        lease_id = uuid4().hex
        lease_path = self.root / 'state' / 'leases' / f'{lease_id}.json'
        with ResourceClaim(self._metadata_lock(), timeout=5):
            if entry_id is not None:
                entry = self.entry(entry_id)
            else:
                assert expected is not None
                matches = (e for e in self._entries() if e.object == expected)
                if (entry := next(matches, None)) is None:
                    raise AssetCacheMiss(
                        f'No retained asset object {expected.sha256}/{expected.length}'
                    )
            self._write_new_model(lease_path, AssetLease(entry_id=entry.id))
        consumed = False
        try:
            try:
                file = self.object_path(entry.object).open('rb')
            except FileNotFoundError as error:
                raise AssetCorruptionError(
                    f'Missing asset object {entry.object.sha256}'
                ) from error
            with file:
                _verify_file(file, entry.object)
                file.seek(0)
                yield file
            consumed = True
        finally:
            with ResourceClaim(self._metadata_lock(), timeout=5):
                lease_path.unlink(missing_ok=True)
                if consumed:
                    self._write_model(
                        self.root / 'state' / 'access' / f'{entry.id}.json',
                        AssetAccess(consumed_at=datetime.now(UTC)),
                    )

    def _prepare_directories(self) -> None:
        for path in (
            self.root / 'staging',
            self.root / 'entries',
            self.root / 'state' / 'leases',
            self.root / 'state' / 'access',
            self.root / 'state' / 'pins',
            self.root / 'state' / 'references',
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _publish(self, staged: Path, entry: AssetEntry, *, pin: bool) -> None:
        with ResourceClaim(self._metadata_lock(), timeout=5):
            object_path = self.object_path(entry.object)
            object_path.parent.mkdir(parents=True, exist_ok=True)
            if object_path.exists():
                self.verify_object(entry.object)
            else:
                if self.capacity is not None:
                    used = sum(
                        path.stat().st_size
                        for path in (self.root / 'objects' / 'sha256').glob('*/*')
                        if path.is_file()
                    )
                    if used + entry.object.length > self.capacity.maximum_object_bytes:
                        raise AssetInsufficientSpace(
                            f'Object admission needs {entry.object.length} bytes; '
                            f'{self.capacity.maximum_object_bytes - used} bytes remain'
                        )
                staged.replace(object_path)
            if pin:
                self._write_new_model(
                    self.root / 'state' / 'pins' / f'{entry.id}.json',
                    AssetPin(entry_id=entry.id),
                )
            self._write_new_model(self._entry_path(entry.id), entry)

    @contextmanager
    def _stage(self, contents: bytes) -> Iterator[Path]:
        staged: Path | None = None
        try:
            self._check_staging_growth(
                self._staging_bytes(), len(contents), len(contents)
            )
            with NamedTemporaryFile(dir=self.root / 'staging', delete=False) as file:
                staged = Path(file.name)
                file.write(contents)
                file.flush()
                os.fsync(file.fileno())
            yield staged
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    @contextmanager
    def _stage_stream(
        self, source: _ByteReader, maximum_bytes: int
    ) -> Iterator[tuple[Path, ObjectIdentity]]:
        staged: Path | None = None
        try:
            digest = hashlib.sha256()
            length = 0
            existing = self._staging_bytes()
            with NamedTemporaryFile(dir=self.root / 'staging', delete=False) as file:
                staged = Path(file.name)
                while block := source.read(min(65536, maximum_bytes - length + 1)):
                    length += len(block)
                    if length > maximum_bytes:
                        raise AssetCacheError(
                            f'Asset exceeds maximum_bytes={maximum_bytes}'
                        )
                    self._check_staging_growth(existing, length, len(block))
                    digest.update(block)
                    file.write(block)
                file.flush()
                os.fsync(file.fileno())
            yield staged, ObjectIdentity(sha256=digest.hexdigest(), length=length)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    def _metadata_lock(self) -> Path:
        return self.root / 'state' / 'metadata.lock'

    def _admission_lock(self) -> Path:
        return self.root / 'state' / 'admission.lock'

    @contextmanager
    def _admission(self) -> Iterator[None]:
        claim = (
            ResourceClaim(self._admission_lock(), timeout=5)
            if self.capacity
            else nullcontext()
        )
        try:
            with claim:
                yield
        except OSError as error:
            if error.errno != errno.ENOSPC:
                raise
            raise AssetInsufficientSpace(
                'Filesystem exhausted during admission'
            ) from error

    def _staging_bytes(self) -> int:
        if self.capacity is None:
            return 0
        total = 0
        for path in (self.root / 'staging').glob('*'):
            try:
                info = path.stat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
        return total

    def _check_staging_growth(
        self, existing: int, staged_total: int, additional: int
    ) -> None:
        if self.capacity is None:
            return
        required = existing + staged_total
        if required > self.capacity.maximum_staging_bytes:
            raise AssetInsufficientSpace(
                f'Staging needs {required} bytes; '
                f'limit is {self.capacity.maximum_staging_bytes}'
            )
        free = shutil.disk_usage(self.root).free
        if free < additional + self.capacity.minimum_free_space:
            raise AssetInsufficientSpace(
                f'Admission needs {additional} bytes and '
                f'{self.capacity.minimum_free_space} bytes free-space margin; '
                f'{free} bytes free'
            )

    def _entry_path(self, entry_id: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{32}', entry_id):
            raise ValueError('entry_id must be an opaque asset ID')
        return self.root / 'entries' / f'{entry_id}.json'

    def _entries(self) -> list[AssetEntry]:
        return [
            self._read_model(path, AssetEntry)
            for path in (self.root / 'entries').glob('*.json')
        ]

    def _root_entry_ids(self, now: datetime) -> set[str]:
        references = [
            self._read_model(path, AssetReference)
            for path in (self.root / 'state' / 'references').glob('*.json')
        ]
        pins = [
            self._read_model(path, AssetPin)
            for path in (self.root / 'state' / 'pins').glob('*.json')
        ]
        leases = [
            self._read_model(path, AssetLease)
            for path in (self.root / 'state' / 'leases').glob('*.json')
        ]
        return {
            *(reference.entry_id for reference in references),
            *(
                pin.entry_id
                for pin in pins
                if pin.expires_at is None or pin.expires_at > now
            ),
            *(lease.entry_id for lease in leases),
        }

    def _access_time(self, entry_id: str) -> datetime | None:
        path = self.root / 'state' / 'access' / f'{entry_id}.json'
        if not path.exists():
            return None
        return self._read_model(path, AssetAccess).consumed_at

    def _retention_decision(
        self,
        entry: AssetEntry,
        rules: list[RetentionRule],
        now: datetime,
        roots: set[str],
        access: datetime | None,
        newest: dict[str, dict[str, int]],
        fresh: set[str],
    ) -> RetentionDecision:
        matching = [rule for rule in rules if rule.matches(entry)]
        protected = any(
            rule.protect == 'forever'
            or (
                isinstance(rule.protect, RetentionDuration)
                and now < self._deadline(rule.protect, entry.created_at, access)
            )
            for rule in matching
        )
        retained = any(
            rule.retain == 'forever'
            or (rule.retain == 'while_fresh' and entry.id in fresh)
            or (
                isinstance(rule.retain, RetentionDuration)
                and now < self._deadline(rule.retain, entry.created_at, access)
            )
            or (
                rule.newest is not None
                and (rank := newest.get(rule.name, {}).get(entry.id)) is not None
                and rank <= rule.newest.count
            )
            for rule in matching
        )
        return RetentionDecision(
            entry_id=entry.id,
            rooted=entry.id in roots,
            protected=protected,
            retained=retained,
            matching_rules=[rule.name for rule in matching],
            newest_ranks={
                rule.name: rank
                for rule in matching
                if (rank := newest.get(rule.name, {}).get(entry.id)) is not None
            },
        )

    def _newest_matches(
        self, entries: list[AssetEntry], rules: list[RetentionRule]
    ) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for rule in rules:
            if rule.newest is None:
                continue
            groups: dict[str, list[AssetEntry]] = {}
            for entry in entries:
                if rule.matches(entry):
                    key = entry.source_key if rule.newest.group_by == 'source' else ''
                    groups.setdefault(key, []).append(entry)
            result[rule.name] = {
                entry.id: rank
                for group in groups.values()
                for rank, entry in enumerate(
                    sorted(group, key=lambda e: (e.created_at, e.id), reverse=True),
                    start=1,
                )
            }
        return result

    def _fresh_entry_ids(self, now: datetime) -> set[str]:
        fresh: set[str] = set()
        for path in (self.root / 'state' / 'http').glob('*.json'):
            record = self._read_model(path, HTTPRecord)
            try:
                entry = self.entry(record.entry_id)
            except AssetCacheError:
                continue
            if entry.source_key != f'v1:{path.stem}':
                raise AssetCorruptionError(
                    'HTTP record points to another source request'
                )
            try:
                policy = response_freshness(
                    record.headers,
                    {},
                    record.request_time,
                    record.response_time,
                    now,
                )
            except ValueError:
                continue
            if policy.fresh:
                fresh.add(record.entry_id)
        return fresh

    def _deadline(
        self,
        duration: RetentionDuration,
        created_at: datetime,
        access_at: datetime | None,
    ) -> datetime:
        origin = created_at if duration.since is RetentionSince.created else access_at
        if origin is None:
            return created_at
        return origin + timedelta(seconds=duration.total_seconds())

    def _utc_now(self, value: datetime | None) -> datetime:
        current = datetime.now(UTC) if value is None else value
        if current.tzinfo is None or current.utcoffset() != UTC.utcoffset(current):
            raise ValueError('now must be UTC')
        return current

    def _validate_rule_names(self, rules: list[RetentionRule]) -> None:
        if len({rule.name for rule in rules}) != len(rules):
            raise ValueError('retention rule names must be unique')

    def _read_model[Model: BaseModel](self, path: Path, model: type[Model]) -> Model:
        try:
            return model.model_validate_json(path.read_text())
        except FileNotFoundError as error:
            raise AssetCacheError(f'Unknown asset record {path.name}') from error

    def _write_new_model(self, path: Path, value: BaseModel) -> None:
        if path.exists():
            raise AssetCacheError(f'Asset record already exists: {path.name}')
        self._write_model(path, value)

    def _write_model(self, path: Path, value: BaseModel) -> None:
        with atomic_output(path, sync=True) as temporary:
            temporary.write_text(value.model_dump_json() + '\n')

    def _validate_name(self, name: str) -> None:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
            raise ValueError('reference names must be filename-safe')


def _validate_fingerprint_json(value: object) -> None:
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 10000 or depth > 64:
            raise ValueError('source request exceeds 10000 values or 64 levels')
        if item is None or type(item) in {bool, int, str}:
            continue
        if type(item) is float:
            if not isfinite(item):
                raise ValueError('source request numbers must be finite')
            continue
        if type(item) is list:
            pending.extend((child, depth + 1) for child in item)
            continue
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError('source request object keys must be strings')
            pending.extend((child, depth + 1) for child in item.values())
            continue
        raise ValueError('source request must contain only JSON values')


def _open_file_beneath(root: Path, relative_path: str) -> BinaryIO:
    parts = relative_path.split('/')
    if (
        not relative_path
        or relative_path.startswith('/')
        or '\\' in relative_path
        or PureWindowsPath(relative_path).drive
        or any(part in {'', '.', '..'} for part in parts)
    ):
        raise ValueError('asset path must stay beneath its declared root')
    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        directory_fds.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY))
        for part in parts[:-1]:
            directory_fds.append(
                os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fds[-1],
                )
            )
        file_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fds[-1]
        )
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise AssetCacheError(f'Asset is not a regular file: {relative_path}')
        file = os.fdopen(file_fd, 'rb')
        file_fd = None
        return file
    except OSError as error:
        raise AssetCacheError(
            f'Cannot open asset beneath its root: {relative_path}: {error.strerror}'
        ) from error
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(directory_fds):
            os.close(descriptor)


def _verify_file(file: BinaryIO, identity: ObjectIdentity) -> None:
    digest = hashlib.sha256()
    length = 0
    while block := file.read(65536):
        digest.update(block)
        length += len(block)
    if digest.hexdigest() != identity.sha256 or length != identity.length:
        raise AssetCorruptionError(f'Corrupt asset object {identity.sha256}')


class _ByteReader(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...
