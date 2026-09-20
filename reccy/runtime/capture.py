"""Bounded finite capture sessions backed by the verified asset store."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Buffer, Callable
from datetime import UTC, datetime
from enum import auto
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator
from strenum import StrEnum

from .assets import (
    AssetCategory,
    AssetEntry,
    AssetStore,
    MediaKind,
    ObjectIdentity,
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

    source_key: str = Field(min_length=1)
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
    source_key: str
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
    id: str
    source_key: str
    observed_frames: int
    stored_bytes: int
    reason: str
    fragments: list[CaptureFragment]
    gaps: list[CaptureGap]


class CaptureReference(BaseModel, frozen=True):
    capture_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class CapturePin(BaseModel, frozen=True):
    capture_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class CaptureStore:
    """Capture manifests and versions sharing an ``AssetStore`` root."""

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

    def set_reference(self, name: str, capture_id: str) -> None:
        self._validate_name(name)
        self.capture(capture_id)
        with ResourceClaim(self._metadata_lock()):
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
        with ResourceClaim(self._metadata_lock()):
            (self.root / 'state' / 'capture-references' / f'{name}.json').unlink(
                missing_ok=True
            )

    def pin_reference(self, name: str) -> str:
        capture = self.referenced(name)
        pin_id = uuid4().hex
        with ResourceClaim(self._metadata_lock()):
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

    def _publish(self, manifest: CaptureManifest) -> None:
        self._prepare_directories()
        with ResourceClaim(self._metadata_lock()):
            self._write_new_model(self._capture_path(manifest.id), manifest)

    def _record_recovery(self, recovery: CaptureRecovery) -> None:
        self._prepare_directories()
        with ResourceClaim(self._metadata_lock()):
            self._write_model(self._recovery_path(recovery.id), recovery)

    def _prepare_directories(self) -> None:
        for path in (
            self.root / 'captures',
            self.root / 'staging',
            self.root / 'state' / 'capture-references',
            self.root / 'state' / 'capture-pins',
        ):
            path.mkdir(parents=True, exist_ok=True)

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
        if (
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', name)
            or '..' in Path(name).parts
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
        self._fragments: list[CaptureFragment] = []
        self._gaps: list[CaptureGap] = []
        self._observed_frames = 0
        self._stored_bytes = 0
        self._bound_reached = False
        self._accepting = True
        self._closed = False
        self._lock = Lock()

    def queue_fragment(self, borrowed: Buffer, *, frame_count: int) -> bool:
        """Copy borrowed storage into the bounded queue without filesystem I/O."""
        if frame_count <= 0:
            raise ValueError('frame_count must be positive')
        view = memoryview(borrowed).cast('B')
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
            incoming_bytes = self._stored_bytes + self._queued_bytes + len(view)
            if incoming_bytes > self.spec.maximum_bytes:
                raise CaptureCapacityError('capture exceeded its maximum byte budget')
            start = self._observed_frames
            self._observed_frames += frame_count
            if len(view) > len(self._buffer) - self._queued_bytes:
                if self.spec.overflow is CaptureOverflowPolicy.fail:
                    raise CaptureQueueOverflow('capture queue is full')
                self._gaps.append(
                    CaptureGap(
                        native_start=start,
                        frame_count=frame_count,
                        reason=CaptureGapReason.queue_overflow,
                    )
                )
                return False
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
        queued = self._take_queue()
        entries: list[AssetEntry] = []
        for payload, item in queued:
            entry = self.store.assets.import_bytes(
                payload,
                source_key=self.spec.source_key,
                category=AssetCategory.acquired,
                source_kind=self.spec.source_kind,
                media_kind=self.spec.media_kind,
            )
            pin_id = self.store.assets.add_pin(entry.id)
            self._fragments.append(
                CaptureFragment(
                    entry_id=entry.id,
                    pin_id=pin_id,
                    object=entry.object,
                    native_start=item.native_start,
                    frame_count=item.frame_count,
                )
            )
            self._stored_bytes += entry.object.length
            entries.append(entry)
        return entries

    def finish(self, termination: CaptureTermination) -> CaptureManifest:
        """Finalize EOF, reached-bound, clean-stop, or explicit salvage."""
        if termination is CaptureTermination.salvaged_failure:
            return self.salvage('explicit salvage')
        if termination is CaptureTermination.bound and not self._bound_reached:
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
        self._require_open()
        self._stop_accepting()
        self.drain()
        recovery = self._recovery(reason)
        self.store._record_recovery(recovery)
        self._closed = True
        return recovery

    def salvage(self, reason: str) -> CaptureManifest:
        """Explicitly publish verified fragments with failure termination."""
        self._require_open()
        self._stop_accepting()
        self.drain()
        self.store._record_recovery(self._recovery(reason))
        manifest = self._manifest(CaptureTermination.salvaged_failure)
        self.store._publish(manifest)
        self._closed = True
        return manifest

    def _take_queue(self) -> list[tuple[bytes, _QueuedFragment]]:
        with self._lock:
            queued: list[tuple[bytes, _QueuedFragment]] = []
            while self._queued:
                item = self._queued.popleft()
                first = min(item.length, len(self._buffer) - item.offset)
                payload = bytes(self._buffer[item.offset : item.offset + first])
                if item.length > first:
                    payload += bytes(self._buffer[: item.length - first])
                queued.append((payload, item))
            self._queued_bytes = 0
            return queued

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
