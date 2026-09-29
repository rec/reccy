"""Bounded finite capture sessions backed by the verified asset store."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Buffer, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import auto
from pathlib import Path
from threading import Lock, RLock
from time import monotonic
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator
from strenum import StrEnum

from .assets import (
    AssetCategory,
    AssetEntry,
    AssetPin,
    AssetStore,
    MediaKind,
    ObjectIdentity,
    RetentionDuration,
    RetentionNewest,
    RetentionSince,
    SourceKind,
)
from .claims import ResourceClaim
from .files import atomic_output


class CaptureError(RuntimeError):
    """A capture session cannot continue or finalize."""


class CaptureQueueOverflow(CaptureError):
    """A bounded callback queue could not accept a complete fragment."""


class CaptureCapacityError(CaptureError):
    """A capture exceeded its declared maximum byte budget."""


class CaptureStateError(CaptureError):
    """An operation is incompatible with the capture's current state."""


class CaptureTermination(StrEnum):
    eof = auto()
    bound = auto()
    clean_stop = auto()
    salvaged_failure = auto()


class CaptureOverflowPolicy(StrEnum):
    fail = auto()
    gap = auto()


class CaptureGapReason(StrEnum):
    queue_overflow = auto()


class CaptureSpec(BaseModel, frozen=True):
    """Resolved bounds and media facts for one finite capture request."""

    source_key: str = Field(pattern=r'^v1:[0-9a-f]{64}$')
    source_kind: SourceKind
    media_kind: MediaKind = MediaKind.other
    maximum_bytes: int = Field(gt=0)
    maximum_queue_bytes: int = Field(gt=0)
    frame_limit: int | None = Field(default=None, gt=0)
    duration_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    manual_stop: bool = False
    sample_rate: int | None = Field(default=None, gt=0)
    channels: list[str] = Field(default_factory=list)
    adapter_version: str = Field(min_length=1)
    encoder_version: str = Field(min_length=1)
    overflow: CaptureOverflowPolicy = CaptureOverflowPolicy.fail

    @model_validator(mode='after')
    def validate_bounds_and_source(self) -> CaptureSpec:
        if (
            sum(
                (
                    self.frame_limit is not None,
                    self.duration_limit is not None,
                    self.manual_stop,
                )
            )
            != 1
        ):
            raise ValueError(
                'capture requires one frame limit, duration limit or manual stop'
            )
        if self.source_kind not in {
            SourceKind.streaming_url,
            SourceKind.callback,
            SourceKind.client_buffer,
        }:
            raise ValueError('capture requires a streaming or callback source')
        if self.maximum_queue_bytes > self.maximum_bytes:
            raise ValueError('capture queue cannot exceed its maximum byte budget')
        if len(self.channels) != len(set(self.channels)) or any(
            not channel for channel in self.channels
        ):
            raise ValueError('capture channels must be nonempty and unique')
        return self


class CaptureFragment(BaseModel, frozen=True):
    entry_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    pin_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    object: ObjectIdentity
    native_start: int = Field(ge=0)
    frame_count: int = Field(gt=0)


class CaptureGap(BaseModel, frozen=True):
    native_start: int = Field(ge=0)
    frame_count: int = Field(gt=0)
    reason: CaptureGapReason


class CaptureManifest(BaseModel, frozen=True):
    """One immutable, explicitly selected capture version."""

    version: int = 1
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    source_key: str = Field(pattern=r'^v1:[0-9a-f]{64}$')
    source_kind: SourceKind
    media_kind: MediaKind
    maximum_bytes: int
    requested_frames: int | None
    requested_duration: float | None
    observed_frames: int
    stored_bytes: int
    sample_rate: int | None
    channels: list[str]
    adapter_version: str
    encoder_version: str
    started_at: datetime
    ended_at: datetime
    termination: CaptureTermination
    fragments: list[CaptureFragment]
    gaps: list[CaptureGap]


class CaptureRecovery(BaseModel, frozen=True):
    """Evidence for a session that did not publish a successful capture."""

    version: int = 1
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    source_key: str = Field(pattern=r'^v1:[0-9a-f]{64}$')
    observed_frames: int
    stored_bytes: int
    reason: str
    fragments: list[CaptureFragment]
    gaps: list[CaptureGap]


class CaptureReference(BaseModel, frozen=True):
    capture_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class CapturePin(BaseModel, frozen=True):
    capture_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class CaptureRecordKind(StrEnum):
    capture = auto()
    recovery = auto()


class CaptureLease(BaseModel, frozen=True):
    kind: CaptureRecordKind
    capture_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class CaptureRetentionRule(BaseModel, frozen=True):
    """An additive rule for completed captures and recovery evidence."""

    name: str = Field(min_length=1)
    kinds: list[CaptureRecordKind] | None = None
    source_keys: list[str] | None = None
    protect: RetentionDuration | str | None = None
    retain: RetentionDuration | str | None = None
    newest: RetentionNewest | None = None

    @model_validator(mode='after')
    def validate_rule(self) -> CaptureRetentionRule:
        if self.protect is not None and (self.retain is not None or self.newest):
            raise ValueError('capture retention rule requires one action family')
        if self.protect is None and self.retain is None and self.newest is None:
            raise ValueError('capture retention rule requires an action')
        for action in (self.protect, self.retain):
            if isinstance(action, str) and action != 'forever':
                raise ValueError(
                    'capture retention action must be forever or a duration'
                )
            if (
                isinstance(action, RetentionDuration)
                and action.since is not RetentionSince.created
            ):
                raise ValueError('capture retention duration must start at creation')
        if self.retain == 'forever' and self.newest is not None:
            raise ValueError('newest cannot be combined with retain=forever')
        return self

    def matches(self, record: _StoredRecord) -> bool:
        return (self.kinds is None or record.kind in self.kinds) and (
            self.source_keys is None or record.source_key in self.source_keys
        )


class CaptureRetentionDecision(BaseModel, frozen=True):
    kind: CaptureRecordKind
    capture_id: str
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


class _StoredRecord(BaseModel, frozen=True):
    kind: CaptureRecordKind
    capture_id: str
    source_key: str
    ended_at: datetime
    fragments: list[CaptureFragment]


class CaptureStore:
    """Capture manifests and versions sharing an ``AssetStore`` root.

    Fragment pins persist through aborted captures and process restarts.
    """

    def __init__(self, assets: AssetStore) -> None:
        self.assets = assets
        self.root = assets.root

    def start(
        self,
        spec: CaptureSpec,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> CaptureSession:
        return CaptureSession(self, spec, clock=clock)

    def capture(self, capture_id: str) -> CaptureManifest:
        return self._read_model(self._capture_path(capture_id), CaptureManifest)

    def recovery(self, capture_id: str) -> CaptureRecovery:
        return self._read_model(self._recovery_path(capture_id), CaptureRecovery)

    @contextmanager
    def open_record(
        self, kind: CaptureRecordKind, capture_id: str
    ) -> Iterator[CaptureManifest | CaptureRecovery]:
        """Keep a selected record and its fragment pins while consuming it."""
        self._prepare_directories()
        lease_path = self.root / 'state' / 'capture-leases' / f'{uuid4().hex}.json'
        with ResourceClaim(self._metadata_lock(), timeout=5):
            record = (
                self.capture(capture_id)
                if kind is CaptureRecordKind.capture
                else self.recovery(capture_id)
            )
            self._write_new_model(
                lease_path, CaptureLease(kind=kind, capture_id=capture_id)
            )
        try:
            yield record
        finally:
            with ResourceClaim(self._metadata_lock(), timeout=5):
                lease_path.unlink(missing_ok=True)

    def plan_collection(
        self,
        rules: list[CaptureRetentionRule],
        *,
        pressure: bool = False,
        now: datetime | None = None,
    ) -> list[CaptureRetentionDecision]:
        """Explain record deletion candidates without changing the store."""
        current = self._collection_time(now)
        self._validate_rule_names(rules)
        with ResourceClaim(self._metadata_lock(), timeout=5):
            records = self._records()
            roots = self._record_roots()
            ranks = self._newest_ranks(records, rules)
            candidates: list[CaptureRetentionDecision] = []
            for record in records:
                decision = self._retention_decision(
                    record, rules, current, roots, ranks
                )
                eligible = (
                    decision.eligible_for_pressure_collection
                    if pressure
                    else decision.eligible_for_ordinary_collection
                )
                if eligible:
                    candidates.append(decision)
            return candidates

    def collect(
        self,
        rules: list[CaptureRetentionRule],
        *,
        pressure: bool = False,
        now: datetime | None = None,
    ) -> list[CaptureRetentionDecision]:
        """Remove eligible records and release pins unused by surviving records.

        Fragment bytes are subsequently reclaimed by ``AssetStore.collect``.
        """
        current = self._collection_time(now)
        planned = self.plan_collection(rules, pressure=pressure, now=current)
        deleted: list[CaptureRetentionDecision] = []
        with ResourceClaim(self._metadata_lock(), timeout=5):
            records = self._records()
            roots = self._record_roots()
            ranks = self._newest_ranks(records, rules)
            by_key = {(r.kind, r.capture_id): r for r in records}
            for decision in planned:
                record = by_key.get((decision.kind, decision.capture_id))
                if record is None:
                    continue
                renewed = self._retention_decision(record, rules, current, roots, ranks)
                eligible = (
                    renewed.eligible_for_pressure_collection
                    if pressure
                    else renewed.eligible_for_ordinary_collection
                )
                if not eligible:
                    continue
                deleted.append(renewed)
            if deleted:
                deleted_keys = {(d.kind, d.capture_id) for d in deleted}
                surviving_pins = {
                    f.pin_id
                    for r in records
                    if (r.kind, r.capture_id) not in deleted_keys
                    for f in r.fragments
                }
                released = {
                    f.pin_id
                    for decision in deleted
                    for f in by_key[(decision.kind, decision.capture_id)].fragments
                } - surviving_pins
                for pin_id in released:
                    pin_path = self.root / 'state' / 'pins' / f'{pin_id}.json'
                    if pin_path.exists():
                        pin = self._read_model(pin_path, AssetPin)
                        if pin.entry_id != pin_id:
                            raise CaptureError(
                                'Capture fragment pin points to another entry'
                            )
                for decision in deleted:
                    self._record_path(decision.kind, decision.capture_id).unlink()
                for pin_id in released:
                    (self.root / 'state' / 'pins' / f'{pin_id}.json').unlink(
                        missing_ok=True
                    )
        return deleted

    def set_reference(self, name: str, capture_id: str) -> None:
        self._validate_name(name)
        with ResourceClaim(self._metadata_lock(), timeout=5):
            self.capture(capture_id)
            self._write_model(
                self.root / 'state' / 'capture-references' / f'{name}.json',
                CaptureReference(capture_id=capture_id),
            )

    def referenced(self, name: str) -> CaptureManifest:
        self._validate_name(name)
        reference = self._read_model(
            self.root / 'state' / 'capture-references' / f'{name}.json',
            CaptureReference,
        )
        return self.capture(reference.capture_id)

    def remove_reference(self, name: str) -> None:
        self._validate_name(name)
        with ResourceClaim(self._metadata_lock(), timeout=5):
            (self.root / 'state' / 'capture-references' / f'{name}.json').unlink(
                missing_ok=True
            )

    def pin_reference(self, name: str) -> str:
        self._validate_name(name)
        pin_id = uuid4().hex
        with ResourceClaim(self._metadata_lock(), timeout=5):
            capture = self.referenced(name)
            self._write_new_model(
                self.root / 'state' / 'capture-pins' / f'{pin_id}.json',
                CapturePin(capture_id=capture.id),
            )
        return pin_id

    def pinned(self, pin_id: str) -> CaptureManifest:
        self._validate_id(pin_id)
        pin = self._read_model(
            self.root / 'state' / 'capture-pins' / f'{pin_id}.json', CapturePin
        )
        return self.capture(pin.capture_id)

    def remove_pin(self, pin_id: str) -> None:
        self._validate_id(pin_id)
        with ResourceClaim(self._metadata_lock(), timeout=5):
            (self.root / 'state' / 'capture-pins' / f'{pin_id}.json').unlink(
                missing_ok=True
            )

    def _publish(self, manifest: CaptureManifest) -> None:
        self._prepare_directories()
        with ResourceClaim(self._metadata_lock(), timeout=5):
            self._write_new_model(self._capture_path(manifest.id), manifest)

    def _record_recovery(self, recovery: CaptureRecovery) -> None:
        self._prepare_directories()
        with ResourceClaim(self._metadata_lock(), timeout=5):
            self._write_model(self._recovery_path(recovery.id), recovery)

    def _salvage_publish(
        self, recovery: CaptureRecovery, manifest: CaptureManifest
    ) -> None:
        self._prepare_directories()
        with ResourceClaim(self._metadata_lock(), timeout=5):
            self._write_model(self._recovery_path(recovery.id), recovery)
            self._write_new_model(self._capture_path(manifest.id), manifest)

    def _prepare_directories(self) -> None:
        for path in (
            self.root / 'captures',
            self.root / 'staging',
            self.root / 'state' / 'capture-references',
            self.root / 'state' / 'capture-pins',
            self.root / 'state' / 'capture-leases',
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _records(self) -> list[_StoredRecord]:
        records: list[_StoredRecord] = []
        for path in (self.root / 'captures').glob('*.json'):
            manifest = self._read_model(path, CaptureManifest)
            records.append(
                _StoredRecord(
                    kind=CaptureRecordKind.capture,
                    capture_id=manifest.id,
                    source_key=manifest.source_key,
                    ended_at=manifest.ended_at,
                    fragments=manifest.fragments,
                )
            )
        for path in (self.root / 'staging').glob('capture-*.json'):
            recovery = self._read_model(path, CaptureRecovery)
            records.append(
                _StoredRecord(
                    kind=CaptureRecordKind.recovery,
                    capture_id=recovery.id,
                    source_key=recovery.source_key,
                    ended_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                    fragments=recovery.fragments,
                )
            )
        return records

    def _record_roots(self) -> set[tuple[CaptureRecordKind, str]]:
        state = self.root / 'state'
        references = [
            self._read_model(p, CaptureReference)
            for p in (state / 'capture-references').rglob('*.json')
        ]
        pins = [
            self._read_model(p, CapturePin)
            for p in (state / 'capture-pins').glob('*.json')
        ]
        leases = [
            self._read_model(p, CaptureLease)
            for p in (state / 'capture-leases').glob('*.json')
        ]
        return {
            *((CaptureRecordKind.capture, r.capture_id) for r in references),
            *((CaptureRecordKind.capture, p.capture_id) for p in pins),
            *((lease.kind, lease.capture_id) for lease in leases),
        }

    def _newest_ranks(
        self, records: list[_StoredRecord], rules: list[CaptureRetentionRule]
    ) -> dict[str, dict[tuple[CaptureRecordKind, str], int]]:
        result: dict[str, dict[tuple[CaptureRecordKind, str], int]] = {}
        for rule in rules:
            if rule.newest is None:
                continue
            groups: dict[str, list[_StoredRecord]] = {}
            for record in records:
                if rule.matches(record):
                    key = record.source_key if rule.newest.group_by == 'source' else ''
                    groups.setdefault(key, []).append(record)
            result[rule.name] = {
                (record.kind, record.capture_id): rank
                for group in groups.values()
                for rank, record in enumerate(
                    sorted(
                        group,
                        key=lambda r: (r.ended_at, r.capture_id),
                        reverse=True,
                    ),
                    start=1,
                )
            }
        return result

    def _retention_decision(
        self,
        record: _StoredRecord,
        rules: list[CaptureRetentionRule],
        now: datetime,
        roots: set[tuple[CaptureRecordKind, str]],
        ranks: dict[str, dict[tuple[CaptureRecordKind, str], int]],
    ) -> CaptureRetentionDecision:
        matching = [r for r in rules if r.matches(record)]
        key = (record.kind, record.capture_id)

        def active(action: RetentionDuration | str | None) -> bool:
            return action == 'forever' or (
                isinstance(action, RetentionDuration)
                and now < record.ended_at + timedelta(seconds=action.total_seconds())
            )

        return CaptureRetentionDecision(
            kind=record.kind,
            capture_id=record.capture_id,
            rooted=key in roots,
            protected=any(active(r.protect) for r in matching),
            retained=any(
                active(r.retain)
                or (
                    r.newest is not None
                    and (rank := ranks.get(r.name, {}).get(key)) is not None
                    and rank <= r.newest.count
                )
                for r in matching
            ),
            matching_rules=[r.name for r in matching],
            newest_ranks={
                r.name: rank
                for r in matching
                if (rank := ranks.get(r.name, {}).get(key)) is not None
            },
        )

    def _record_path(self, kind: CaptureRecordKind, capture_id: str) -> Path:
        return (
            self._capture_path(capture_id)
            if kind is CaptureRecordKind.capture
            else self._recovery_path(capture_id)
        )

    def _collection_time(self, value: datetime | None) -> datetime:
        current = datetime.now(UTC) if value is None else value
        if current.tzinfo is None or current.utcoffset() != UTC.utcoffset(current):
            raise ValueError('now must be UTC')
        return current

    def _validate_rule_names(self, rules: list[CaptureRetentionRule]) -> None:
        if len({r.name for r in rules}) != len(rules):
            raise ValueError('capture retention rule names must be unique')

    def _metadata_lock(self) -> Path:
        return self.root / 'state' / 'metadata.lock'

    def _capture_path(self, capture_id: str) -> Path:
        self._validate_id(capture_id)
        return self.root / 'captures' / f'{capture_id}.json'

    def _recovery_path(self, capture_id: str) -> Path:
        self._validate_id(capture_id)
        return self.root / 'staging' / f'capture-{capture_id}.json'

    def _read_model[Model: BaseModel](self, path: Path, model: type[Model]) -> Model:
        try:
            return model.model_validate_json(path.read_text())
        except FileNotFoundError as error:
            raise CaptureError(f'Unknown capture record {path.name}') from error

    def _write_new_model(self, path: Path, value: BaseModel) -> None:
        if path.exists():
            raise CaptureError(f'Capture record already exists: {path.name}')
        self._write_model(path, value)

    def _write_model(self, path: Path, value: BaseModel) -> None:
        with atomic_output(path, sync=True) as temporary:
            temporary.write_text(value.model_dump_json() + '\n')

    def _validate_name(self, name: str) -> None:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', name) or any(
            part in {'', '.', '..'} for part in name.split('/')
        ):
            raise ValueError('capture reference names must be safe relative paths')

    def _validate_id(self, value: str) -> None:
        if not re.fullmatch(r'[0-9a-f]{32}', value):
            raise ValueError('capture IDs must be opaque asset IDs')


class CaptureSession:
    """A bounded session whose producer path only copies into preallocated memory."""

    def __init__(
        self,
        store: CaptureStore,
        spec: CaptureSpec,
        *,
        clock: Callable[[], float],
    ) -> None:
        self.id = uuid4().hex
        self.store = store
        self.spec = spec
        self._clock = clock
        self._started = clock()
        self._started_at = datetime.now(UTC)
        self._buffer = bytearray(spec.maximum_queue_bytes)
        self._write_offset = 0
        self._queued_bytes = 0
        self._queued: deque[_QueuedFragment] = deque()
        self._pending: deque[tuple[bytes, _QueuedFragment]] = deque()
        self._pending_bytes = 0
        self._fragments: list[CaptureFragment] = []
        self._gaps: list[CaptureGap] = []
        self._observed_frames = 0
        self._stored_bytes = 0
        self._bound_reached = False
        self._accepting = True
        self._closed = False
        self._lock = Lock()
        self._drain_lock = RLock()

    def queue_fragment(self, borrowed: Buffer, *, frame_count: int) -> bool:
        """Copy borrowed storage into the bounded queue without filesystem I/O."""
        if frame_count <= 0:
            raise ValueError('frame_count must be positive')
        view = memoryview(borrowed).cast('B')
        if not view:
            raise ValueError('fragment must contain bytes')
        with self._lock:
            if not self._accepting:
                raise CaptureStateError('capture is no longer accepting fragments')
            if self._duration_reached():
                self._bound_reached = True
                return False
            if (
                self.spec.frame_limit is not None
                and self._observed_frames + frame_count > self.spec.frame_limit
            ):
                raise CaptureStateError('fragment crosses the requested frame limit')
            incoming_bytes = (
                self._stored_bytes
                + self._pending_bytes
                + self._queued_bytes
                + len(view)
            )
            if incoming_bytes > self.spec.maximum_bytes:
                raise CaptureCapacityError('capture exceeded its maximum byte budget')
            start = self._observed_frames
            if len(view) > len(self._buffer) - self._queued_bytes:
                if self.spec.overflow is CaptureOverflowPolicy.fail:
                    raise CaptureQueueOverflow('capture queue is full')
                self._observed_frames += frame_count
                self._gaps.append(
                    CaptureGap(
                        native_start=start,
                        frame_count=frame_count,
                        reason=CaptureGapReason.queue_overflow,
                    )
                )
                if self._observed_frames == self.spec.frame_limit:
                    self._bound_reached = True
                return False
            self._observed_frames += frame_count
            offset = self._write_offset
            first = min(len(view), len(self._buffer) - offset)
            self._buffer[offset : offset + first] = view[:first]
            remaining = len(view) - first
            if remaining:
                self._buffer[:remaining] = view[first:]
            self._write_offset = (offset + len(view)) % len(self._buffer)
            self._queued_bytes += len(view)
            self._queued.append(
                _QueuedFragment(
                    offset=offset,
                    length=len(view),
                    native_start=start,
                    frame_count=frame_count,
                )
            )
            if self._observed_frames == self.spec.frame_limit:
                self._bound_reached = True
            return True

    def drain(self) -> list[AssetEntry]:
        """Persist queued copies outside the producer callback."""
        with self._drain_lock:
            self._take_queue()
            entries: list[AssetEntry] = []
            while self._pending:
                payload, item = self._pending[0]
                entry = self.store.assets.import_bytes(
                    payload,
                    source_key=self.spec.source_key,
                    category=AssetCategory.acquired,
                    source_kind=self.spec.source_kind,
                    media_kind=self.spec.media_kind,
                    pin=True,
                )
                self._fragments.append(
                    CaptureFragment(
                        entry_id=entry.id,
                        pin_id=entry.id,
                        object=entry.object,
                        native_start=item.native_start,
                        frame_count=item.frame_count,
                    )
                )
                with self._lock:
                    self._stored_bytes += entry.object.length
                    self._pending_bytes -= len(payload)
                self._pending.popleft()
                entries.append(entry)
            return entries

    def finish(self, termination: CaptureTermination) -> CaptureManifest:
        """Finalize EOF, reached-bound, clean-stop, or explicit salvage."""
        with self._drain_lock:
            if termination is CaptureTermination.salvaged_failure:
                return self.salvage('explicit salvage')
            if termination is CaptureTermination.bound and not (
                self._bound_reached or self._duration_reached()
            ):
                raise CaptureStateError('capture has not reached its requested bound')
            if termination not in {
                CaptureTermination.eof,
                CaptureTermination.bound,
                CaptureTermination.clean_stop,
            }:
                raise CaptureStateError('unsupported successful termination')
            self._require_open()
            self._stop_accepting()
            self.drain()
            manifest = self._manifest(termination)
            self.store._publish(manifest)
            self._closed = True
            return manifest

    def abort(self, reason: str) -> CaptureRecovery:
        """Preserve diagnostics without publishing a successful capture."""
        with self._drain_lock:
            self._require_open()
            self._stop_accepting()
            self.drain()
            recovery = self._recovery(reason)
            self.store._record_recovery(recovery)
            self._closed = True
            return recovery

    def salvage(self, reason: str) -> CaptureManifest:
        """Explicitly publish verified fragments with failure termination."""
        with self._drain_lock:
            self._require_open()
            self._stop_accepting()
            self.drain()
            recovery = self._recovery(reason)
            manifest = self._manifest(CaptureTermination.salvaged_failure)
            self.store._salvage_publish(recovery, manifest)
            self._closed = True
            return manifest

    def _take_queue(self) -> None:
        with self._lock:
            while self._queued:
                item = self._queued[0]
                first = min(item.length, len(self._buffer) - item.offset)
                payload = bytes(self._buffer[item.offset : item.offset + first])
                if item.length > first:
                    payload += bytes(self._buffer[: item.length - first])
                self._pending.append((payload, item))
                self._pending_bytes += len(payload)
                self._queued.popleft()
            self._queued_bytes = 0

    def _duration_reached(self) -> bool:
        return self.spec.duration_limit is not None and (
            self._clock() - self._started >= self.spec.duration_limit
        )

    def _manifest(self, termination: CaptureTermination) -> CaptureManifest:
        return CaptureManifest(
            id=self.id,
            source_key=self.spec.source_key,
            source_kind=self.spec.source_kind,
            media_kind=self.spec.media_kind,
            maximum_bytes=self.spec.maximum_bytes,
            requested_frames=self.spec.frame_limit,
            requested_duration=self.spec.duration_limit,
            observed_frames=self._observed_frames,
            stored_bytes=self._stored_bytes,
            sample_rate=self.spec.sample_rate,
            channels=self.spec.channels,
            adapter_version=self.spec.adapter_version,
            encoder_version=self.spec.encoder_version,
            started_at=self._started_at,
            ended_at=datetime.now(UTC),
            termination=termination,
            fragments=self._fragments,
            gaps=self._gaps,
        )

    def _recovery(self, reason: str) -> CaptureRecovery:
        return CaptureRecovery(
            id=self.id,
            source_key=self.spec.source_key,
            observed_frames=self._observed_frames,
            stored_bytes=self._stored_bytes,
            reason=reason,
            fragments=self._fragments,
            gaps=self._gaps,
        )

    def _require_open(self) -> None:
        if self._closed:
            raise CaptureStateError('capture session is already closed')

    def _stop_accepting(self) -> None:
        with self._lock:
            self._accepting = False


class _QueuedFragment:
    def __init__(
        self, *, offset: int, length: int, native_start: int, frame_count: int
    ) -> None:
        self.offset = offset
        self.length = length
        self.native_start = native_start
        self.frame_count = frame_count
