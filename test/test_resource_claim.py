import multiprocessing
import sys
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from reccy.runtime import claims


def hold_claim(path: Path, ready: Connection) -> None:
    with claims.ResourceClaim(path):
        ready.send('acquired')
        ready.recv()


@pytest.mark.parametrize('crash', [False, True])
def test_claim_is_exclusive_and_recovers_after_owner_exit(
    tmp_path: Path, crash: bool
) -> None:
    path = tmp_path / 'resource.lock'
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=hold_claim, args=(path, child))
    process.start()
    child.close()
    try:
        assert parent.poll(5)
        assert parent.recv() == 'acquired'
        with pytest.raises(claims.ResourceClaimConflict):
            claims.ResourceClaim(path).acquire()
        if crash:
            process.terminate()
        else:
            parent.send('release')
        process.join(5)
        assert not process.is_alive()
        with claims.ResourceClaim(path):
            assert path.exists()
        assert path.exists()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()


def test_claim_keeps_existing_contents_and_releases_on_error(tmp_path: Path) -> None:
    path = tmp_path / 'claim'
    path.write_text('not a PID or ownership record')
    claim = claims.ResourceClaim(path)
    with pytest.raises(ValueError, match='body failed'):
        with claim:
            with pytest.raises(RuntimeError, match='already acquired'):
                claim.acquire()
            raise ValueError('body failed')
    claim.release()
    with claim:
        pass
    assert path.read_text() == 'not a PID or ownership record'


def test_permission_failure_is_not_a_claim_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(path: object, flags: int, mode: int) -> int:
        raise PermissionError('permission denied')

    monkeypatch.setattr(claims.os, 'open', denied)
    with pytest.raises(PermissionError):
        claims.ResourceClaim(tmp_path / 'claim').acquire()


@pytest.mark.skipif(
    sys.platform == 'win32', reason='POSIX permits replacing open files'
)
def test_release_does_not_touch_replacement_file(tmp_path: Path) -> None:
    path = tmp_path / 'claim'
    first = claims.ResourceClaim(path).acquire()
    try:
        replacement = tmp_path / 'replacement'
        replacement.write_text('replacement')
        replacement.replace(path)
        with claims.ResourceClaim(path):
            first.release()
            with pytest.raises(claims.ResourceClaimConflict):
                claims.ResourceClaim(path).acquire()
        assert path.read_text() == 'replacement'
    finally:
        first.release()
