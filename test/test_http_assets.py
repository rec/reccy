import gzip
import hashlib
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from reccy.runtime import assets, http_assets


class Response(BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.status = 200
        self.headers = Message()
        for key, value in headers.items():
            self.headers[key] = value


class Opener:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.calls = 0

    def open(self, request: object, timeout: float) -> Response:
        self.calls += 1
        return self.response


def _identity(contents: bytes) -> assets.ObjectIdentity:
    return assets.ObjectIdentity(
        sha256=hashlib.sha256(contents).hexdigest(), length=len(contents)
    )


def test_https_asset_decodes_and_retains_verified_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = b'finite HTTP asset'
    encoded = gzip.compress(contents)
    opener = Opener(Response(encoded, {'Content-Encoding': 'gzip'}))
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    allowed = ['https://example.test/take.bin']
    with http_assets.open_https_asset(
        store,
        allowed[0],
        _identity(contents),
        allow_url=lambda url: url in allowed,
        fingerprint_key=b'k' * 32,
        maximum_encoded_bytes=len(encoded),
        maximum_decoded_bytes=len(contents),
        timeout=1,
    ) as file:
        assert file.read() == contents
    assert opener.calls == 1
    assert len(list((store.root / 'entries').glob('*.json'))) == 1
    with http_assets.open_https_asset(
        store,
        allowed[0],
        _identity(contents),
        allow_url=lambda url: url in allowed,
        fingerprint_key=b'k' * 32,
        maximum_encoded_bytes=len(encoded),
        maximum_decoded_bytes=len(contents),
        timeout=1,
    ) as file:
        assert file.read() == contents
    assert opener.calls == 1


def test_https_no_store_uses_bounded_transient_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = b'private bytes'
    opener = Opener(Response(contents, {'Cache-Control': 'private, no-store'}))
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='private')
    with http_assets.open_https_asset(
        store,
        'https://example.test/private',
        _identity(contents),
        allow_url=lambda url: True,
        fingerprint_key=b'k' * 32,
        maximum_encoded_bytes=len(contents),
        maximum_decoded_bytes=len(contents),
        timeout=1,
    ) as file:
        assert file.read() == contents
    assert list((store.root / 'entries').glob('*.json')) == []


def test_https_denial_and_body_limit_prevent_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = b'oversized'
    opener = Opener(Response(contents, {}))
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    with pytest.raises(http_assets.AssetAcquisitionDenied):
        with http_assets.open_https_asset(
            store,
            'https://denied.test/file',
            _identity(contents),
            allow_url=lambda url: False,
            fingerprint_key=b'k' * 32,
            maximum_encoded_bytes=4,
            maximum_decoded_bytes=4,
            timeout=1,
        ):
            pass
    assert opener.calls == 0
    with pytest.raises(assets.AssetCacheError, match='maximum_encoded_bytes'):
        with http_assets.open_https_asset(
            store,
            'https://allowed.test/file',
            _identity(contents),
            allow_url=lambda url: True,
            fingerprint_key=b'k' * 32,
            maximum_encoded_bytes=4,
            maximum_decoded_bytes=len(contents),
            timeout=1,
        ):
            pass
    assert list((store.root / 'entries').glob('*.json')) == []


def test_https_redirect_reauthorizes_and_drops_cross_origin_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = b'redirected bytes'

    class RedirectOpener:
        def __init__(self) -> None:
            self.requests: list[Request] = []

        def open(self, request: Request, timeout: float) -> Response:
            self.requests.append(request)
            if len(self.requests) == 1:
                headers = Message()
                headers['Location'] = 'https://cdn.test/object'
                raise HTTPError(request.full_url, 302, 'Found', headers, BytesIO())
            return Response(contents, {})

    opener = RedirectOpener()
    monkeypatch.setattr(http_assets, 'build_opener', lambda handler: opener)
    store = assets.AssetStore(tmp_path / 'cache', credential_scope='public')
    with http_assets.open_https_asset(
        store,
        'https://origin.test/object',
        _identity(contents),
        allow_url=lambda url: (
            url
            in {
                'https://origin.test/object',
                'https://cdn.test/object',
            }
        ),
        fingerprint_key=b'k' * 32,
        maximum_encoded_bytes=len(contents),
        maximum_decoded_bytes=len(contents),
        timeout=1,
        headers={'Authorization': 'Bearer private'},
    ) as file:
        assert file.read() == contents
    assert opener.requests[0].get_header('Authorization') == 'Bearer private'
    assert opener.requests[1].get_header('Authorization') is None
