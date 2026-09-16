import socket
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from reccy.protocol import ipc, rpc


def test_failed_server_start_releases_control_listener() -> None:
    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        events = Path(directory) / 'events.sock'
        events.write_text('not a socket')
        server = rpc.Server(control, events, lambda request: 'ok', role='test')

        with pytest.raises(FileExistsError):
            server.start()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(1)
            with pytest.raises(ConnectionRefusedError):
                peer.connect(str(control))
        assert events.read_text() == 'not a socket'


@pytest.mark.parametrize('event_endpoint', [False, True])
def test_server_close_disconnects_clients_waiting_after_hello(
    event_endpoint: bool,
) -> None:
    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        events = Path(directory) / 'events.sock'
        server = rpc.Server(control, events, lambda request: 'ok', role='test')
        server.start()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(1)
                peer.connect(str(events if event_endpoint else control))
                peer.sendall(b'{"type":"hello","role":"client","version":1}\n')
                assert b'"hello"' in peer.recv(128)

                server.close()

                assert peer.recv(1) == b''
        finally:
            server.close()


def test_server_close_disconnects_request_while_handler_finishes() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def handle(request: rpc.Request) -> str:
        started.set()
        release.wait(1)
        finished.set()
        return 'ok'

    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        server = rpc.Server(
            control, Path(directory) / 'events.sock', handle, role='test'
        )
        server.start()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(1)
                peer.connect(str(control))
                peer.sendall(b'{"type":"hello","role":"client","version":1}\n')
                assert b'"hello"' in peer.recv(128)
                peer.sendall(ipc.message_json(rpc.Request(command='slow')).encode())
                assert started.wait(0.5)

                server.close()

                assert peer.recv(1) == b''
        finally:
            release.set()
            server.close()
            assert finished.wait(1)
