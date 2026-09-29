import threading
from pathlib import Path

import pytest

from reccy.protocol import ipc, rpc
from reccy.reccy import Reccy


@pytest.mark.parametrize('failure', ['on_started', 'publish_status', 'on_stopping'])
def test_lifecycle_failure_cleans_up(
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
    assert calls == (
        ['closed'] if failure == 'on_started' else ['start', 'stop', 'closed']
    )
    assert not application.status_snapshot().running


def test_rpc_starts_after_application_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class Application(Reccy):
        name = 'lifecycle'
        rpc_enabled = True

        def on_started(self) -> None:
            calls.append('resources ready')

    monkeypatch.setattr(rpc.Server, 'start', lambda server: calls.append('rpc ready'))
    monkeypatch.setattr(rpc.Server, 'close', lambda server: None)
    application = Application(home=tmp_path)
    application.start()
    try:
        assert calls == ['resources ready', 'rpc ready']
    finally:
        application.close()


def test_rpc_rejects_commands_while_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses: list[rpc.Result] = []

    class Application(Reccy):
        name = 'lifecycle'
        rpc_enabled = True

        def on_stopping(self) -> None:
            responses.append(
                self.rpc_response(rpc.Request(command='status'), threading.Event())
            )

    monkeypatch.setattr(rpc.Server, 'start', lambda server: None)
    monkeypatch.setattr(rpc.Server, 'close', lambda server: None)
    application = Application(home=tmp_path)
    application.start()
    application.close()
    assert responses == [ipc.Error(type='error', message='application is not running')]


def test_resources_remain_open_until_rpc_workers_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = 0
    attempts = 0

    class Application(Reccy):
        name = 'lifecycle'
        rpc_enabled = True

        def on_closed(self) -> None:
            nonlocal closed
            closed += 1

    def close_server(self: rpc.Server) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError('RPC workers did not stop')

    monkeypatch.setattr(rpc.Server, 'start', lambda server: None)
    monkeypatch.setattr(rpc.Server, 'close', close_server)
    application = Application(home=tmp_path)
    application.start()

    with pytest.raises(TimeoutError, match='workers did not stop'):
        application.close()
    assert closed == 0

    application.close()
    assert closed == 1
