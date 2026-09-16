import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from reccy.protocol import ipc, rpc


@pytest.fixture
def connected_peer(monkeypatch: pytest.MonkeyPatch) -> Iterator[socket.socket]:
    local, peer = socket.socketpair()
    transport = ipc.UnixSocketConnection(local)
    monkeypatch.setattr(ipc, 'client_connection', lambda endpoint: transport)
    with peer:
        peer.settimeout(1)
        try:
            yield peer
        finally:
            transport.close()


def test_rpc_call_reports_handshake_timeout(connected_peer: socket.socket) -> None:
    with pytest.raises(TimeoutError, match='RPC request timed out'):
        rpc.Client(Path('/unused.sock'), timeout=0.02).call('status')


def test_event_subscription_times_out_during_hello(
    connected_peer: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rpc, 'HANDSHAKE_TIMEOUT', 0.02)
    client = rpc.EventClient(Path('/unused.sock'), lambda event: None)

    with pytest.raises(TimeoutError, match='subscription timed out'):
        client.start()

    assert client.closed
    assert client.wait_closed(1)
    assert client.terminal_reason == rpc.EventCloseReason.timeout


def test_event_subscription_closes_after_invalid_hello(
    connected_peer: socket.socket,
) -> None:
    connected_peer.sendall(b'{"type":"event","name":"premature"}\n')
    client = rpc.EventClient(Path('/unused.sock'), lambda event: None)

    with pytest.raises(ConnectionError, match='did not send hello'):
        client.start()

    assert client.closed
    assert client.wait_closed(0)
    assert client.terminal_reason == rpc.EventCloseReason.protocol_error
    with connected_peer.makefile('rb') as lines:
        assert b'"hello"' in lines.readline()
        assert lines.readline() == b''


def test_event_reader_closes_after_malformed_event(
    connected_peer: socket.socket,
) -> None:
    connected_peer.sendall(b'{"type":"hello","role":"test","version":1}\ninvalid\n')
    client = rpc.EventClient(Path('/unused.sock'), lambda event: None)
    client.start()

    with connected_peer.makefile('rb') as lines:
        assert b'"hello"' in lines.readline()
        assert b'"subscribe"' in lines.readline()
        assert lines.readline() == b''
    assert client.closed
    assert client.wait_closed(1)
    assert client.terminal_reason == rpc.EventCloseReason.protocol_error


@pytest.mark.parametrize('error_type', [RuntimeError, OSError])
def test_event_reader_closes_after_callback_failure(
    connected_peer: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    failed = threading.Event()

    def on_event(event: rpc.Event) -> None:
        raise error_type('callback failed')

    def on_thread_error(args: threading.ExceptHookArgs) -> None:
        assert isinstance(args.exc_value, error_type)
        failed.set()

    monkeypatch.setattr(threading, 'excepthook', on_thread_error)
    connected_peer.sendall(
        b'{"type":"hello","role":"test","version":1}\n{"type":"event","name":"test"}\n'
    )
    client = rpc.EventClient(Path('/unused.sock'), on_event)
    client.start()

    assert failed.wait(1)
    assert client.closed
    assert client.wait_closed(1)
    assert client.terminal_reason == rpc.EventCloseReason.callback_error
    with connected_peer.makefile('rb') as lines:
        assert b'"hello"' in lines.readline()
        assert b'"subscribe"' in lines.readline()
        assert lines.readline() == b''


@pytest.mark.parametrize('local_close', [False, True])
def test_event_connection_reports_terminal_reason(
    connected_peer: socket.socket, local_close: bool
) -> None:
    connected_peer.sendall(b'{"type":"hello","role":"test","version":1}\n')
    client = rpc.EventClient(Path('/unused.sock'), lambda event: None)
    assert not client.wait_closed(0)
    client.start()
    if local_close:
        client.close()
    else:
        connected_peer.shutdown(socket.SHUT_WR)
    assert client.wait_closed(1)
    expected = (
        rpc.EventCloseReason.local_close
        if local_close
        else rpc.EventCloseReason.peer_eof
    )
    assert client.terminal_reason == expected
    client.close()
    assert client.terminal_reason == expected


def test_connection_failure_notifies_event_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(endpoint: Path | str) -> ipc.Connection:
        raise FileNotFoundError('missing endpoint')

    monkeypatch.setattr(ipc, 'client_connection', fail)
    client = rpc.EventClient(Path('/unused.sock'), lambda event: None)
    with pytest.raises(FileNotFoundError):
        client.start()
    assert client.wait_closed(0)
    assert client.terminal_reason == rpc.EventCloseReason.transport_error
