"""Private verified storage for finite host asset bytes.

This module stores opaque finite bytes. Source acquisition, provider execution,
and real-time capture stay with their host-specific adapters.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import auto
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator
from strenum import StrEnum

from .claims import ResourceClaim
from .files import atomic_output


class AssetCacheError(RuntimeError):
    """An asset-store operation could not complete."""


class AssetCorruptionError(AssetCacheError):
    """Stored bytes do not match their immutable object identity."""


class AssetIdentityMismatch(AssetCacheError):
    """Admitted bytes do not match the caller's expected object identity."""


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


class AssetEntry(BaseModel, frozen=True):
    """Immutable facts for one acquisition or materialization."""

    version: int = 1
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r'^[0-9a-f]{32}$')
    object: ObjectIdentity
    source_key: str = Field(min_length=1)
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

    def matches(self, entry: AssetEntry) -> bool:
        return (
            (self.category is None or entry.category in self.category)
            and (self.source_kind is None or entry.source_kind in self.source_kind)
            and (self.media_kind is None or entry.media_kind in self.media_kind)
            and (self.source_key is None or entry.source_key in self.source_key)
            and (self.tags is None or bool(set(entry.tags) & set(self.tags)))
        )


class RetentionRule(BaseModel, frozen=True):
    """One additive protection or retention rule for finite entries."""

    name: str = Field(min_length=1)
    match: RetentionMatch | None = None
    all: bool = False
    protect: RetentionDuration | str | None = None
    retain: RetentionDuration | str | None = None

    @model_validator(mode='after')
    def validate_rule(self) -> RetentionRule:
        if self.match is None and not self.all:
            raise ValueError('retention rule requires match or all=true')
        if self.match is not None and self.all:
            raise ValueError('retention rule cannot combine match and all=true')
        if (self.protect is None) == (self.retain is None):
            raise ValueError('retention rule requires exactly one of protect or retain')
        action = self.protect if self.protect is not None else self.retain
        if isinstance(action, str) and action != 'forever':
            raise ValueError('retention action must be forever or a duration')
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


class AssetStore:
    """A cooperating-process store of verified finite bytes and entry manifests.

    ``root`` is a host-owned private directory. Every host process using one
    store must use this class so publication and retention-root updates share
    the metadata claim.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

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
    ) -> AssetEntry:
        """Store verified bytes and publish a new immutable entry manifest."""
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
        with self._stage(contents) as staged:
            with ResourceClaim(self._metadata_lock()):
                object_path = self.object_path(identity)
                object_path.parent.mkdir(parents=True, exist_ok=True)
                if object_path.exists():
                    self.verify_object(identity)
                else:
                    staged.replace(object_path)
                self._write_new_model(self._entry_path(entry.id), entry)
        return entry

    def entry(self, entry_id: str) -> AssetEntry:
        """Read an immutable entry manifest without treating it as consumption."""
        return self._read_model(self._entry_path(entry_id), AssetEntry)

    def verify_object(self, identity: ObjectIdentity) -> None:
        """Check stored bytes, rather than trusting an existing pathname or size."""
        path = self.object_path(identity)
        digest = hashlib.sha256()
        length = 0
        try:
            with path.open('rb') as file:
                while block := file.read(65536):
                    digest.update(block)
                    length += len(block)
        except FileNotFoundError as error:
            raise AssetCorruptionError(
                f'Missing asset object {identity.sha256}'
            ) from error
        if digest.hexdigest() != identity.sha256 or length != identity.length:
            raise AssetCorruptionError(f'Corrupt asset object {identity.sha256}')

    @contextmanager
    def open_entry(self, entry_id: str) -> Iterator[BinaryIO]:
        """Open verified bytes while a durable lease prevents their collection."""
        entry = self.entry(entry_id)
        self.verify_object(entry.object)
        lease_id = uuid4().hex
        lease_path = self.root / 'state' / 'leases' / f'{lease_id}.json'
        with ResourceClaim(self._metadata_lock()):
            self._write_new_model(lease_path, AssetLease(entry_id=entry.id))
        consumed = False
        try:
            with self.object_path(entry.object).open('rb') as file:
                yield file
            consumed = True
        finally:
            with ResourceClaim(self._metadata_lock()):
                lease_path.unlink(missing_ok=True)
                if consumed:
                    self._write_model(
                        self.root / 'state' / 'access' / f'{entry.id}.json',
                        AssetAccess(consumed_at=datetime.now(UTC)),
                    )

    def set_reference(self, name: str, entry_id: str) -> None:
        """Create or atomically move a named root to an existing entry."""
        self._validate_name(name)
        self.entry(entry_id)
        with ResourceClaim(self._metadata_lock()):
            self._write_model(
                self.root / 'state' / 'references' / f'{name}.json',
                AssetReference(entry_id=entry_id),
            )

    def remove_reference(self, name: str) -> None:
        """Remove one named root without collecting the entry immediately."""
        self._validate_name(name)
        with ResourceClaim(self._metadata_lock()):
            (self.root / 'state' / 'references' / f'{name}.json').unlink(
                missing_ok=True
            )

    def add_pin(self, entry_id: str, *, expires_at: datetime | None = None) -> str:
        """Add an immutable pin and return its opaque ID."""
        self.entry(entry_id)
        pin_id = uuid4().hex
        with ResourceClaim(self._metadata_lock()):
            self._write_new_model(
                self.root / 'state' / 'pins' / f'{pin_id}.json',
                AssetPin(entry_id=entry_id, expires_at=expires_at),
            )
        return pin_id

    def remove_pin(self, pin_id: str) -> None:
        """Remove one pin without collecting its entry immediately."""
        if not re.fullmatch(r'[0-9a-f]{32}', pin_id):
            raise ValueError('pin_id must be an opaque asset ID')
        with ResourceClaim(self._metadata_lock()):
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
        entry = self.entry(entry_id)
        with ResourceClaim(self._metadata_lock()):
            roots = self._root_entry_ids(current)
            access = self._access_time(entry.id)
        return self._retention_decision(entry, rules, current, roots, access)

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
        for entry_path in (self.root / 'entries').glob('*.json'):
            decision = self.explain_retention(entry_path.stem, rules, now=current)
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
        """Delete eligible manifests and then their newly unreachable objects."""
        current = self._utc_now(now)
        planned = self.plan_collection(rules, pressure=pressure, now=current)
        deleted: list[str] = []
        with ResourceClaim(self._metadata_lock()):
            for decision in planned:
                entry = self.entry(decision.entry_id)
                renewed = self._retention_decision(
                    entry,
                    rules,
                    current,
                    self._root_entry_ids(current),
                    self._access_time(entry.id),
                )
                eligible = (
                    renewed.eligible_for_pressure_collection
                    if pressure
                    else renewed.eligible_for_ordinary_collection
                )
                if not eligible:
                    continue
                self._entry_path(entry.id).unlink()
                if not any(
                    candidate.object == entry.object for candidate in self._entries()
                ):
                    self.object_path(entry.object).unlink(missing_ok=True)
                deleted.append(entry.id)
        return deleted

    def object_path(self, identity: ObjectIdentity) -> Path:
        return self.root / 'objects' / 'sha256' / identity.sha256[:2] / identity.sha256

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

    @contextmanager
    def _stage(self, contents: bytes) -> Iterator[Path]:
        with NamedTemporaryFile(dir=self.root / 'staging', delete=False) as file:
            staged = Path(file.name)
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        try:
            yield staged
        finally:
            staged.unlink(missing_ok=True)

    def _metadata_lock(self) -> Path:
        return self.root / 'state' / 'metadata.lock'

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
            or (
                isinstance(rule.retain, RetentionDuration)
                and now < self._deadline(rule.retain, entry.created_at, access)
            )
            for rule in matching
        )
        return RetentionDecision(
            entry_id=entry.id,
            rooted=entry.id in roots,
            protected=protected,
            retained=retained,
            matching_rules=[rule.name for rule in matching],
        )

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
