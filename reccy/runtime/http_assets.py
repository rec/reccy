"""Acquire immutable HTTPS assets under host URL and credential policy."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from email.message import Message
from http.client import HTTPMessage
from io import BytesIO
from math import isfinite
from typing import IO, BinaryIO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .assets import (
    AssetCacheError,
    AssetCacheMiss,
    AssetCategory,
    AssetIdentityMismatch,
    AssetStore,
    MediaKind,
    ObjectIdentity,
    SourceKind,
    source_fingerprint,
)
from .http_freshness import response_freshness


class AssetAcquisitionDenied(AssetCacheError):
    """Host policy denied a URL or redirect before it was requested."""


@contextmanager
def open_https_asset(
    store: AssetStore,
    url: str,
    expected: ObjectIdentity,
    *,
    allow_url: Callable[[str], bool],
    fingerprint_key: bytes,
    maximum_encoded_bytes: int,
    maximum_decoded_bytes: int,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    media_kind: MediaKind = MediaKind.other,
) -> Iterator[BinaryIO]:
    """Open expected bytes from this scope or acquire an authorized HTTPS body.

    A no-store or Vary:* response is verified in bounded memory for this use but
    never published. The host supplies credentials and authorizes every URL.
    """
    if any(
        type(v) is not int or v <= 0
        for v in (maximum_encoded_bytes, maximum_decoded_bytes)
    ):
        raise ValueError('download byte limits must be positive integers')
    if type(timeout) not in {int, float} or not isfinite(timeout) or timeout <= 0:
        raise ValueError('download timeout must be positive')
    _authorize(url, allow_url)
    with ExitStack() as stack:
        try:
            file = stack.enter_context(store.open_expected(expected))
        except AssetCacheMiss:
            pass
        else:
            yield file
            return
    request_headers = {'User-Agent': 'reccy-asset/1', 'Accept-Encoding': 'gzip'}
    if headers is not None:
        request_headers.update(headers)
    secret = json.dumps(
        [
            url,
            sorted((key.casefold(), value) for key, value in request_headers.items()),
        ],
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    source_key = source_fingerprint(
        {'kind': 'download'},
        {},
        expected,
        {'representation': 'http-content-decoded'},
        lookup_secret=secret,
        fingerprint_key=fingerprint_key,
    )
    with _open_response(url, allow_url, request_headers, timeout) as (
        response,
        status,
        response_headers,
        request_time,
        response_time,
    ):
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
        freshness = response_freshness(
            response_headers,
            request_headers,
            request_time,
            response_time,
            response_time,
        )
        if not freshness.storable or '*' in freshness.vary:
            contents = _read_transient(decoded, maximum_decoded_bytes, expected)
            with BytesIO(contents) as file:
                yield file
            return
        entry = store.import_stream(
            decoded,
            maximum_bytes=maximum_decoded_bytes,
            source_key=source_key,
            category=AssetCategory.acquired,
            source_kind=SourceKind.download,
            media_kind=media_kind,
            expected=expected,
        )
    with store.open_entry(entry.id) as file:
        yield file


@contextmanager
def _open_response(
    url: str,
    allow_url: Callable[[str], bool],
    request_headers: dict[str, str],
    timeout: float,
) -> Iterator[tuple[BinaryIO, int, Message, datetime, datetime]]:
    _authorize(url, allow_url)
    opener = build_opener(_NoRedirect())
    current = url
    for _ in range(6):
        try:
            request_time = datetime.now(UTC)
            response = opener.open(
                Request(current, headers=request_headers), timeout=timeout
            )
        except HTTPError as error:
            if error.code == 304:
                with error:
                    yield (
                        cast(BinaryIO, error),
                        error.code,
                        error.headers,
                        request_time,
                        datetime.now(UTC),
                    )
                return
            try:
                if error.code not in {301, 302, 303, 307, 308}:
                    raise AssetCacheError(
                        f'HTTPS acquisition returned status {error.code}'
                    ) from error
                location = error.headers.get('Location')
                if not location:
                    raise AssetCacheError('HTTPS redirect has no Location') from error
                previous = urlsplit(current)
                current = urljoin(current, location)
                _authorize(current, allow_url)
                following = urlsplit(current)
                if (previous.hostname, previous.port) != (
                    following.hostname,
                    following.port,
                ):
                    request_headers = {
                        key: value
                        for key, value in request_headers.items()
                        if key.casefold()
                        not in {'authorization', 'cookie', 'proxy-authorization'}
                    }
            finally:
                error.close()
            continue
        except (OSError, URLError) as error:
            raise AssetCacheError(
                f'HTTPS acquisition failed: {type(error).__name__}'
            ) from error
        with response:
            yield (
                response,
                response.status,
                response.headers,
                request_time,
                datetime.now(UTC),
            )
        return
    raise AssetCacheError('HTTPS acquisition exceeded five redirects')


def _authorize(url: str, allow_url: Callable[[str], bool]) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise AssetAcquisitionDenied('HTTPS asset URL is invalid') from error
    if (
        parsed.scheme != 'https'
        or not parsed.hostname
        or port == 0
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AssetAcquisitionDenied('HTTPS asset URL is invalid')
    if not allow_url(url):
        raise AssetAcquisitionDenied('Host policy denied the asset URL')


def _read_transient(
    source: gzip.GzipFile | _BoundedReader,
    maximum_bytes: int,
    expected: ObjectIdentity,
) -> bytes:
    digest = hashlib.sha256()
    contents = bytearray()
    while block := source.read(min(65536, maximum_bytes - len(contents) + 1)):
        contents.extend(block)
        if len(contents) > maximum_bytes:
            raise AssetCacheError(f'Asset exceeds maximum_bytes={maximum_bytes}')
        digest.update(block)
    if len(contents) != expected.length or digest.hexdigest() != expected.sha256:
        raise AssetIdentityMismatch('HTTPS body differs from expected content identity')
    return bytes(contents)


def _content_length(headers: Message) -> int | None:
    value = headers.get('Content-Length')
    if value is None or headers.get('Transfer-Encoding') is not None:
        return None
    if len(value) > 20 or not value.isascii() or not value.isdecimal():
        raise AssetCacheError('Invalid HTTP Content-Length')
    return int(value)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        return None


class _BoundedReader:
    def __init__(
        self,
        stream: BinaryIO,
        maximum_bytes: int,
        *,
        expected_length: int | None = None,
    ) -> None:
        self.stream = stream
        self.maximum_bytes = maximum_bytes
        self.expected_length = expected_length
        self.length = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self.maximum_bytes - self.length + 1
        block = self.stream.read(min(size, remaining) if size >= 0 else remaining)
        self.length += len(block)
        if self.length > self.maximum_bytes:
            raise AssetCacheError(
                f'Encoded HTTP body exceeds maximum_encoded_bytes={self.maximum_bytes}'
            )
        if (
            not block
            and self.expected_length is not None
            and self.length != self.expected_length
        ):
            raise AssetCacheError('Incomplete HTTP body differs from Content-Length')
        return block
