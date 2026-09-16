import socket
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from reccy.protocol import ipc, rpc
from reccy.services.models import DaemonMetadata, Platform


@pytest.fixture
def endpoints(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    monkeypatch.setattr(rpc, 'HANDSHAKE_TIMEOUT', 0.1)
    monkeypatch.setattr(rpc, 'MAX_REQUEST_BYTES', 128)
    monkeypatch.setattr(rpc, 'MAX_EVENT_CONNECTIONS', 1)
    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        events = Path(directory) / 'events.sock'
        server = rpc.Server(control, events, lambda request: 'ok', role='test')
        server.start()
        try:
            yield control, events
        finally:
            server.close()


@pytest.mark.parametrize('endpoint_index', [0, 1])
def test_rpc_deadline_includes_waiting_for_first_command(
    endpoints: tuple[Path, Path], endpoint_index: int
) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(1)
        peer.connect(str(endpoints[endpoint_index]))
        with peer.makefile('rb') as lines:
            peer.sendall(b'{"type":"hello","role":"client","version":1}\n')
            assert b'"hello"' in lines.readline()
            assert lines.readline() == b''


@pytest.mark.parametrize('endpoint_index', [0, 1])
@pytest.mark.parametrize('after_hello', [False, True])
def test_rpc_rejects_oversized_input_without_waiting_for_newline(
    endpoints: tuple[Path, Path], endpoint_index: int, after_hello: bool
) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(1)
        peer.connect(str(endpoints[endpoint_index]))
        with peer.makefile('rb') as lines:
            if after_hello:
                peer.sendall(b'{"type":"hello","role":"client","version":1}\n')
                assert b'"hello"' in lines.readline()
            peer.sendall(b'x' * 129)
            assert b'size limit' in lines.readline()
            assert lines.readline() == b''


def test_event_capacity_counts_subscribed_connections(
    endpoints: tuple[Path, Path],
) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as first:
        first.settimeout(1)
        first.connect(str(endpoints[1]))
        first.sendall(b'{"type":"hello","role":"client","version":1}\n')
        assert b'"hello"' in first.recv(128)
        first.sendall(b'{"type":"subscribe"}\n')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as second:
            second.settimeout(1)
            second.connect(str(endpoints[1]))
            assert second.recv(1) == b''


def test_rpc_accepts_filesystem_endpoint_loaded_from_metadata(
    endpoints: tuple[Path, Path],
) -> None:
    metadata = DaemonMetadata(
        module='app', platform=Platform.linux, control_endpoint=str(endpoints[0])
    )
    restored = DaemonMetadata.model_validate_json(metadata.model_dump_json())

    assert rpc.Client(restored.control_endpoint).call('status') == 'ok'


def test_pipe_reader_accepts_existing_serialized_messages_with_a_limit() -> None:
    receiver, sender = ipc.connection.Pipe()
    with receiver, sender:
        sender.send('hello\n')
        lines = ipc.WindowsPipeConnection(receiver).read_lines(max_bytes=128)
        assert next(lines) == 'hello\n'


def test_pipe_reader_rejects_oversized_serialized_frames() -> None:
    receiver, sender = ipc.connection.Pipe()
    with receiver, sender:
        sender.send('x' * 129)
        assert list(ipc.WindowsPipeConnection(receiver).read_lines(max_bytes=128)) == []
