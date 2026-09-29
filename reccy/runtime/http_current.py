"""Import or reuse the current finite representation of an HTTPS URL."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from email.message import Message
from io import BytesIO
from math import isfinite
from pathlib import Path
from typing import BinaryIO, cast

from .assets import (
    AssetCacheError,
    AssetCacheMiss,
    AssetCategory,
    AssetCorruptionError,
    AssetStore,
    MediaKind,
    ObjectIdentity,
    SourceKind,
    source_fingerprint,
)
from .claims import ResourceClaim
from .files import atomic_output
from .http_assets import _authorize, _BoundedReader, _content_length, _open_response
from .http_freshness import (
    HTTPRecord,
    _directives,
    _normalize_headers,
    response_freshness,
)


@contextmanager
def open_current_https_asset(
    store: AssetStore,
    url: str,
    *,
    allow_url: Callable[[str], bool],
    fingerprint_key: bytes,
    maximum_encoded_bytes: int,
    maximum_decoded_bytes: int,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    media_kind: MediaKind = MediaKind.other,
) -> Iterator[tuple[ObjectIdentity, BinaryIO]]:
    """Open the current response, validating stale records without stale fallback.

    The request key includes every supplied header, which is conservative for
    ``Vary``: changed irrelevant headers may miss, but different variants
    cannot collide. URL and request header values are HMACed, not stored.
    """
    if any(
        type(v) is not int or v <= 0
        for v in (maximum_encoded_bytes, maximum_decoded_bytes)
    ):
        raise ValueError('download byte limits must be positive integers')
    if type(timeout) not in {int, float} or not isfinite(timeout) or timeout <= 0:
        raise ValueError('download timeout must be positive')
    _authorize(url, allow_url)
    request_headers = {'User-Agent': 'reccy-asset/1', 'Accept-Encoding': 'gzip'}
    if headers is not None:
        request_headers.update(headers)
    if any(
        key.casefold() in {'if-none-match', 'if-modified-since', 'range'}
        for key in request_headers
    ):
        raise ValueError('conditional and range headers are managed by the cache')
    secret = json.dumps(
        [url, sorted((k.casefold(), v) for k, v in request_headers.items())],
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    source_key = source_fingerprint(
        {'kind': 'current-download'},
        {},
        None,
        {'representation': 'http-content-decoded'},
        lookup_secret=secret,
        fingerprint_key=fingerprint_key,
    )
    record_path = store.root / 'state' / 'http' / f'{source_key[3:]}.json'
    only_if_cached = 'only-if-cached' in _directives(
        _normalize_headers(request_headers).get('cache-control', '')
    )
    claim = store.root / 'state' / 'http-claims' / f'{source_key[3:]}.lock'
    with ExitStack() as stack:
        with ResourceClaim(claim, timeout=6 * timeout + 5):
            for attempt in range(2):
                identity, result = _resolve_current(
                    store,
                    url,
                    allow_url,
                    request_headers,
                    source_key,
                    record_path,
                    maximum_encoded_bytes,
                    maximum_decoded_bytes,
                    timeout,
                    media_kind,
                    only_if_cached,
                )
                if isinstance(result, bytes):
                    file = stack.enter_context(BytesIO(result))
                    break
                try:
                    file = stack.enter_context(store.open_entry(result))
                except AssetCorruptionError:
                    raise
                except AssetCacheError:
                    if _entry_exists(store, result, source_key):
                        raise
                    if attempt:
                        raise AssetCacheMiss(
                            'Cached HTTPS response was collected before opening'
                        ) from None
                else:
                    break
        yield identity, file


def _resolve_current(
    store: AssetStore,
    url: str,
    allow_url: Callable[[str], bool],
    request_headers: dict[str, str],
    source_key: str,
    record_path: Path,
    maximum_encoded_bytes: int,
    maximum_decoded_bytes: int,
    timeout: float,
    media_kind: MediaKind,
    only_if_cached: bool,
) -> tuple[ObjectIdentity, str | bytes]:
    record = _read_record(record_path)
    now = datetime.now(UTC)
    request_policy = response_freshness({}, request_headers, now, now, now)
    if not request_policy.storable:
        record = None
    if record is not None and _entry_exists(store, record.entry_id, source_key):
        policy = response_freshness(
            record.headers,
            request_headers,
            record.request_time,
            record.response_time,
            datetime.now(UTC),
        )
        if policy.fresh:
            return store.entry(record.entry_id).object, record.entry_id
    else:
        record = None
    if only_if_cached:
        raise AssetCacheMiss('No fresh cached response for this HTTPS request')

    for _ in range(2):
        outgoing = request_headers.copy()
        if record is not None:
            if validator := record.headers.get('etag'):
                outgoing['If-None-Match'] = validator
            elif validator := record.headers.get('last-modified'):
                outgoing['If-Modified-Since'] = validator
        with _open_response(url, allow_url, outgoing, timeout) as (
            response,
            status,
            response_headers,
            request_time,
            response_time,
        ):
            if status == 304:
                if record is None:
                    raise AssetCacheError(
                        'Unsolicited HTTP 304 without a stored response'
                    )
                updated = _response_headers(response_headers)
                for name in ('etag', 'last-modified'):
                    if (
                        name in updated
                        and name in record.headers
                        and updated[name] != record.headers[name]
                    ):
                        record = None
                        break
                else:
                    if _entry_exists(store, record.entry_id, source_key):
                        combined = _response_headers(record.headers | updated)
                        policy = response_freshness(
                            combined,
                            request_headers,
                            request_time,
                            response_time,
                            response_time,
                        )
                        if policy.storable and '*' not in policy.vary:
                            _write_record(
                                store,
                                record_path,
                                HTTPRecord(
                                    entry_id=record.entry_id,
                                    headers=combined,
                                    request_time=request_time,
                                    response_time=response_time,
                                ),
                            )
                        else:
                            _remove_record(store, record_path)
                        return store.entry(record.entry_id).object, record.entry_id
                    record = None
                continue
            if status != 200:
                raise AssetCacheError(f'HTTPS acquisition returned status {status}')
            encoding = response_headers.get('Content-Encoding', 'identity').casefold()
            if encoding not in {'identity', 'gzip'}:
                raise AssetCacheError('Unsupported HTTP content encoding')
            encoded = _BoundedReader(
                response,
                maximum_encoded_bytes,
                expected_length=_content_length(response_headers),
            )
            decoded = (
                gzip.GzipFile(fileobj=cast(BinaryIO, encoded))
                if encoding == 'gzip'
                else encoded
            )
            policy = response_freshness(
                response_headers,
                request_headers,
                request_time,
                response_time,
                response_time,
            )
            if not policy.storable or '*' in policy.vary:
                _remove_record(store, record_path)
                body = _read_transient(decoded, maximum_decoded_bytes)
                identity = ObjectIdentity(
                    sha256=hashlib.sha256(body).hexdigest(), length=len(body)
                )
                return identity, body
            stored_headers = _response_headers(response_headers)
            entry = store.import_stream(
                decoded,
                maximum_bytes=maximum_decoded_bytes,
                source_key=source_key,
                category=AssetCategory.acquired,
                source_kind=SourceKind.download,
                media_kind=media_kind,
            )
            _write_record(
                store,
                record_path,
                HTTPRecord(
                    entry_id=entry.id,
                    headers=stored_headers,
                    request_time=request_time,
                    response_time=response_time,
                ),
            )
        return entry.object, entry.id
    raise AssetCacheError('HTTP validation did not provide a usable body')


def _read_record(path: Path) -> HTTPRecord | None:
    try:
        return HTTPRecord.model_validate_json(path.read_text())
    except FileNotFoundError:
        return None


def _entry_exists(store: AssetStore, entry_id: str, source_key: str) -> bool:
    try:
        entry = store.entry(entry_id)
    except AssetCorruptionError:
        raise
    except AssetCacheError:
        return False
    if entry.source_key != source_key:
        raise AssetCorruptionError('HTTP record points to another source request')
    return True


def _response_headers(headers: Mapping[str, str] | Message) -> dict[str, str]:
    keep = {
        'cache-control',
        'expires',
        'date',
        'age',
        'etag',
        'last-modified',
        'vary',
    }
    selected = {
        key: value for key, value in _normalize_headers(headers).items() if key in keep
    }
    if sum(len(key) + len(value) for key, value in selected.items()) > 16384:
        raise AssetCacheError('HTTP cache metadata exceeds 16 KiB')
    if any('\r' in value or '\n' in value for value in selected.values()):
        raise AssetCacheError('HTTP cache metadata contains an invalid line break')
    return selected


def _write_record(store: AssetStore, path: Path, record: HTTPRecord) -> None:
    with ResourceClaim(store.root / 'state' / 'metadata.lock', timeout=5):
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_output(path, sync=True) as temporary:
            temporary.write_text(record.model_dump_json() + '\n')


def _remove_record(store: AssetStore, path: Path) -> None:
    with ResourceClaim(store.root / 'state' / 'metadata.lock', timeout=5):
        path.unlink(missing_ok=True)


def _read_transient(
    source: gzip.GzipFile | _BoundedReader, maximum_bytes: int
) -> bytes:
    contents = bytearray()
    while block := source.read(min(65536, maximum_bytes - len(contents) + 1)):
        contents.extend(block)
        if len(contents) > maximum_bytes:
            raise AssetCacheError(f'Asset exceeds maximum_bytes={maximum_bytes}')
    return bytes(contents)
