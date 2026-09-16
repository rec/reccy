from pathlib import Path

import pytest

from reccy.protocol import rpc
from reccy.reccy import Reccy


@pytest.mark.parametrize('failure', ['on_started', 'publish_status', 'on_stopping'])
def test_lifecycle_failure_releases_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls: list[str] = []

    class Application(Reccy):
        name = 'lifecycle'
        rpc_enabled = True

        def on_closed(self) -> None:
            calls.append('closed')

    def start_server(self: rpc.Server) -> None:
        calls.append('start')

    def close_server(self: rpc.Server) -> None:
        calls.append('stop')

    def fail(self: Application) -> None:
        raise ValueError('failed hook')

    monkeypatch.setattr(rpc.Server, 'start', start_server)
    monkeypatch.setattr(rpc.Server, 'close', close_server)
    application = Application(home=tmp_path)
    if failure != 'on_started':
        application.start()
        with pytest.raises(RuntimeError, match='already started'):
            application.start()
    monkeypatch.setattr(Application, failure, fail)
    with pytest.raises(ValueError, match='failed hook'):
        if failure == 'on_started':
            application.start()
        else:
            application.close()
    application.close()
    assert calls == ['start', 'stop', 'closed']
    assert not application.status_snapshot().running
