import socket
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from reccy.protocol import ipc, rpc

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32', reason='Unix socket filesystem tests'
)


def test_failed_server_start_releases_control_listener() -> None:
    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        events = Path(directory) / 'events.sock'
        events.write_text('not a socket')
        server = rpc.Server(
            control, events, lambda request, cancelled: 'ok', role='test'
        )

        with pytest.raises(FileExistsError):
            server.start()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(1)
            with pytest.raises(ConnectionRefusedError):
                peer.connect(str(control))
        assert events.read_text() == 'not a socket'


def test_closed_server_cannot_restart() -> None:
    with TemporaryDirectory(dir='/tmp') as directory:
        server = rpc.Server(
            Path(directory) / 'control.sock',
            Path(directory) / 'events.sock',
            lambda request, cancelled: 'ok',
            role='test',
        )
        server.start()
        server.close()
        with pytest.raises(RuntimeError, match='cannot be restarted'):
            server.start()


@pytest.mark.parametrize('event_endpoint', [False, True])
def test_server_close_disconnects_clients_waiting_after_hello(
    event_endpoint: bool,
) -> None:
    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        events = Path(directory) / 'events.sock'
        server = rpc.Server(
            control, events, lambda request, cancelled: 'ok', role='test'
        )
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

    def handle(request: rpc.Request, cancelled: threading.Event) -> str:
        started.set()
        assert cancelled.wait(1)
        release.wait()
        finished.set()
        return 'ok'

    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        server = rpc.Server(
            control, Path(directory) / 'events.sock', handle, role='test'
        )
        server.start()
        watchdog = threading.Timer(2, release.set)
        watchdog.start()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(1)
                peer.connect(str(control))
                peer.sendall(b'{"type":"hello","role":"client","version":1}\n')
                assert b'"hello"' in peer.recv(128)
                peer.sendall(ipc.message_json(rpc.Request(command='slow')).encode())
                assert started.wait(0.5)

                with pytest.raises(TimeoutError, match='workers did not stop'):
                    server.close(timeout=0.01)

                assert peer.recv(1) == b''
                assert not finished.is_set()
        finally:
            release.set()
            watchdog.cancel()
            server.close()
            assert finished.wait(1)


def test_request_deadline_cancels_handler_and_releases_slot() -> None:
    cancelled_seen = threading.Event()

    def handle(request: rpc.Request, cancelled: threading.Event) -> str:
        assert cancelled.wait(1)
        cancelled_seen.set()
        return 'ok'

    with TemporaryDirectory(dir='/tmp') as directory:
        server = rpc.Server(
            Path(directory) / 'control.sock',
            Path(directory) / 'events.sock',
            handle,
            role='test',
            request_timeout=0.02,
        )
        server.start()
        try:
            with pytest.raises(ConnectionError):
                rpc.Client(Path(directory) / 'control.sock', timeout=1).call('slow')
            assert cancelled_seen.wait(1)
            deadline = time.monotonic() + 1
            while len(server.threads) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not server.threads
        finally:
            server.close()


def test_server_close_waits_for_cooperative_handler() -> None:
    started = threading.Event()
    finished = threading.Event()

    def handle(request: rpc.Request, cancelled: threading.Event) -> str:
        started.set()
        assert cancelled.wait(1)
        finished.set()
        return 'ok'

    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        server = rpc.Server(
            control, Path(directory) / 'events.sock', handle, role='test'
        )
        server.start()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(1)
            peer.connect(str(control))
            peer.sendall(b'{"type":"hello","role":"client","version":1}\n')
            assert b'"hello"' in peer.recv(128)
            peer.sendall(ipc.message_json(rpc.Request(command='slow')).encode())
            assert started.wait(1)
            server.close()
            assert finished.is_set()


def test_slow_subscriber_does_not_delay_other_subscribers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TemporaryDirectory(dir='/tmp') as directory:
        control = Path(directory) / 'control.sock'
        events = Path(directory) / 'events.sock'
        server = rpc.Server(
            control, events, lambda request, cancelled: 'ok', role='test'
        )
        server.start()
        slow = rpc.EventClient(events, lambda event: None)
        received: list[rpc.Event] = []
        fast = rpc.EventClient(events, received.append)
        entered = threading.Event()
        release = threading.Event()
        try:
            slow.start()
            deadline = time.monotonic() + 1
            while len(server.event_connections) != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(server.event_connections) == 1
            fast.start()
            deadline = time.monotonic() + 1
            while len(server.event_connections) != 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(server.event_connections) == 2
            slow_connection = server.event_connections[0]
            original_write = slow_connection.write

            def slow_write(message: str) -> bool:
                entered.set()
                assert release.wait(1)
                return original_write(message)

            monkeypatch.setattr(slow_connection, 'write', slow_write)
            server.publish('first')
            assert entered.wait(1)
            for index in range(rpc.MAX_PENDING_EVENTS + 1):
                server.publish('next', index=index)
                deadline = time.monotonic() + 1
                while len(received) < index + 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert len(received) == index + 2
            assert slow_connection not in server.event_connections
            server.publish('last')
            deadline = time.monotonic() + 1
            while (
                len(received) < rpc.MAX_PENDING_EVENTS + 3
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert received[-1].name == 'last'
        finally:
            release.set()
            slow.close()
            fast.close()
            server.close()
