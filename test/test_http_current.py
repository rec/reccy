import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from io import BytesIO
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from reccy.runtime import assets, http_assets, http_current


class Response(BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.status = 200
        self.headers = Message()
        for key, value in headers.items():
            self.headers[key] = value


class Opener:
    def __init__(self, responses: list[Response | HTTPError | URLError]) -> None:
        self.responses = responses
        self.requests: list[Request] = []

    def open(self, request: Request, timeout: float) -> Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, (HTTPError, URLError)):
            raise response
        return response


def _open(
    store: assets.AssetStore,
    *,
    headers: dict[str, str] | None = None,
) -> object:
    return http_current.open_current_https_asset(
        store,
        'https://example.test/private?token=secret',
        allow_url=lambda url: url.startswith('https://example.test/'),
        fingerprint_key=b'k' * 32,
        maximum_encoded_bytes=100,
        maximum_decoded_bytes=100,
        timeout=1,
        headers=headers,
    )


def _not_modified(headers: dict[str, str]) -> HTTPError:
    message = Message()
    for key, value in headers.items():
        message[key] = value
    return HTTPError('https://example.test/', 304, 'Not Modified', message, BytesIO())


def test_current_https_reuses_fresh_verified_body_without_storing_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = Opener([Response(b'first', {'Cache-Control': 'max-age=3600'})])
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    for _ in range(2):
        with _open(store, headers={'Authorization': 'Bearer secret'}) as (
            identity,
            file,
        ):
            assert file.read() == b'first'
            assert identity.sha256 == hashlib.sha256(b'first').hexdigest()
    assert len(opener.requests) == 1
    metadata = next((store.root / 'state' / 'http').glob('*.json')).read_text()
    assert 'secret' not in metadata
    assert 'example.test' not in metadata


def test_stale_current_https_uses_validator_and_304_refreshes_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = Opener(
        [
            Response(b'first', {'Cache-Control': 'max-age=0', 'ETag': '"one"'}),
            _not_modified({'Cache-Control': 'max-age=3600', 'ETag': '"one"'}),
        ]
    )
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    for _ in range(3):
        with _open(store) as (identity, file):
            assert file.read() == b'first'
            assert identity.length == 5
    assert len(opener.requests) == 2
    assert opener.requests[1].get_header('If-none-match') == '"one"'


def test_current_https_changed_body_gets_a_new_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = Opener(
        [
            Response(b'first', {'Cache-Control': 'max-age=0', 'ETag': '"one"'}),
            Response(b'second', {'Cache-Control': 'max-age=3600'}),
        ]
    )
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    with _open(store) as (first, file):
        assert file.read() == b'first'
    with _open(store) as (second, file):
        assert file.read() == b'second'
    assert first != second


def test_no_store_current_https_is_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = Opener([Response(b'private', {'Cache-Control': 'no-store'})])
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    with _open(store) as (identity, file):
        assert identity.length == 7
        assert file.read() == b'private'
    assert list((store.root / 'entries').glob('*.json')) == []
    assert list((store.root / 'state' / 'http').glob('*.json')) == []


def test_current_https_does_not_reuse_stale_bytes_on_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = Opener(
        [
            Response(b'first', {'Cache-Control': 'max-age=0'}),
            URLError('offline'),
        ]
    )
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    with _open(store):
        pass
    with pytest.raises(assets.AssetCacheError, match='HTTPS acquisition failed'):
        with _open(store):
            pass


def test_current_https_keeps_request_variants_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = Opener(
        [
            Response(
                b'english',
                {'Cache-Control': 'max-age=3600', 'Vary': 'Accept-Language'},
            ),
            Response(
                b'french',
                {'Cache-Control': 'max-age=3600', 'Vary': 'Accept-Language'},
            ),
        ]
    )
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    for language, expected in [
        ('en', b'english'),
        ('fr', b'french'),
        ('en', b'english'),
    ]:
        with _open(store, headers={'Accept-Language': language}) as (_, file):
            assert file.read() == expected
    assert len(opener.requests) == 2


def test_304_with_collected_body_fetches_an_unconditional_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')

    class CollectingOpener(Opener):
        def open(self, request: Request, timeout: float) -> Response:
            if len(self.requests) == 1:
                store.collect([])
            return super().open(request, timeout)

    opener = CollectingOpener(
        [
            Response(b'first', {'Cache-Control': 'max-age=0', 'ETag': '"one"'}),
            _not_modified({'ETag': '"one"'}),
            Response(b'second', {'Cache-Control': 'max-age=3600'}),
        ]
    )
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    with _open(store) as (_, file):
        assert file.read() == b'first'
    with _open(store) as (_, file):
        assert file.read() == b'second'
    assert opener.requests[1].get_header('If-none-match') == '"one"'
    assert opener.requests[2].get_header('If-none-match') is None


def test_incomplete_current_https_body_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = Opener([Response(b'partial', {'Content-Length': '10'})])
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    with pytest.raises(assets.AssetCacheError, match='Incomplete HTTP body'):
        with _open(store):
            pass
    assert list((store.root / 'entries').glob('*.json')) == []


def test_concurrent_current_https_requests_share_one_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = Event()
    release = Event()

    class WaitingOpener(Opener):
        def open(self, request: Request, timeout: float) -> Response:
            entered.set()
            assert release.wait(5)
            return super().open(request, timeout)

    opener = WaitingOpener([Response(b'bytes', {'Cache-Control': 'max-age=3600'})])
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')

    def read() -> bytes:
        with _open(store) as (_, file):
            return file.read()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read)
        assert entered.wait(5)
        second = pool.submit(read)
        release.set()
        assert first.result() == b'bytes'
        assert second.result() == b'bytes'
    assert len(opener.requests) == 1


def test_http_record_cannot_reuse_another_source_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = Opener([Response(b'first', {'Cache-Control': 'max-age=3600'})])
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    with _open(store):
        pass
    unrelated = store.import_bytes(
        b'unrelated',
        source_key=assets.source_fingerprint({'kind': 'unrelated'}, {}, None, {}),
        category=assets.AssetCategory.acquired,
        source_kind=assets.SourceKind.download,
    )
    record_path = next((store.root / 'state' / 'http').glob('*.json'))
    record = json.loads(record_path.read_text())
    record['entry_id'] = unrelated.id
    record_path.write_text(json.dumps(record))
    with pytest.raises(assets.AssetCorruptionError, match='another source request'):
        with _open(store):
            pass
