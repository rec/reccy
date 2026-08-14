import time
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from reccy import ipc, rpc

WINDOWS_PIPE = r'\\.\pipe\reccy-test'


class AppMessage(BaseModel):
    type: typing.Literal['app']
    value: str


MESSAGE = TypeAdapter(ipc.Hello | ipc.Shutdown | ipc.Error | AppMessage)


def parse_message(line: str) -> object:
    return MESSAGE.validate_json(line)


def test_backend_selects_unix_socket_for_path() -> None:
    backend = ipc.server_backend(Path('/tmp/reccy.sock'))

    assert isinstance(backend, ipc.UnixSocketServerBackend)


def test_backend_selects_windows_pipe_for_string() -> None:
    backend = ipc.server_backend(WINDOWS_PIPE)

    assert isinstance(backend, ipc.WindowsPipeServerBackend)


def test_windows_pipe_server_accepts_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = FakeListener(WINDOWS_PIPE, family='AF_PIPE')
    monkeypatch.setattr(
        ipc.connection,
        'Listener',
        lambda endpoint, family: listener,
    )
    backend = ipc.WindowsPipeServerBackend(WINDOWS_PIPE)

    backend.start()
    connection = backend.accept()
    backend.close()

    assert connection is not None
    assert listener.endpoint == WINDOWS_PIPE
    assert listener.family == 'AF_PIPE'
    assert listener.closed


def test_windows_pipe_client_uses_named_pipe_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = FakePipe()
    calls: list[tuple[str, str]] = []

    def connect(endpoint: str, family: str) -> FakePipe:
        calls.append((endpoint, family))
        return pipe

    monkeypatch.setattr(ipc.connection, 'Client', connect)
    connection = ipc.WindowsPipeConnection.connect(WINDOWS_PIPE)

    connection.write('hello\n')

    assert calls == [(WINDOWS_PIPE, 'AF_PIPE')]
    assert pipe.sent == ['hello\n']


def test_windows_pipe_client_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def connect(endpoint: str, family: str) -> FakePipe:
        nonlocal calls
        calls += 1
        time.sleep(0.1)
        return FakePipe()

    monkeypatch.setattr(ipc, 'PIPE_CONNECT_TIMEOUT', 0.01)
    monkeypatch.setattr(ipc.connection, 'Client', connect)

    with pytest.raises(TimeoutError, match='Timed out connecting'):
        ipc.WindowsPipeConnection.connect(WINDOWS_PIPE)
    with pytest.raises(TimeoutError, match='Timed out connecting'):
        ipc.WindowsPipeConnection.connect(WINDOWS_PIPE)
    assert calls == 1


def test_protocol_listener_replies_to_supported_hello() -> None:
    connection = FakeConnection(['{"type":"hello","role":"gui","version":1}\n'])
    listener = ipc.ProtocolListener(
        connection,
        parse=parse_message,
        version=1,
        peer_role='GUI',
        local_role='daemon',
        on_message=lambda listener, message: None,
    )

    listener.read()

    assert connection.sent == ['{"type":"hello","role":"daemon","version":1}\n']


def test_protocol_listener_requires_hello() -> None:
    connection = FakeConnection(['{"type":"app","value":"x"}\n'])
    messages: list[object] = []
    listener = ipc.ProtocolListener(
        connection,
        parse=parse_message,
        version=1,
        peer_role='GUI',
        local_role='daemon',
        on_message=lambda listener, message: messages.append(message),
    )

    listener.read()

    assert connection.sent == [
        '{"type":"error","message":"GUI hello required before other messages"}\n'
    ]
    assert connection.closed
    assert messages == []


def test_protocol_listener_passes_app_messages_after_hello() -> None:
    connection = FakeConnection(
        [
            '{"type":"hello","role":"gui","version":1}\n',
            '{"type":"app","value":"x"}\n',
        ]
    )
    messages: list[object] = []
    listener = ipc.ProtocolListener(
        connection,
        parse=parse_message,
        version=1,
        peer_role='GUI',
        local_role='daemon',
        on_message=lambda listener, message: messages.append(message),
    )

    listener.read()

    assert connection.sent == ['{"type":"hello","role":"daemon","version":1}\n']
    assert messages == [AppMessage(type='app', value='x')]


def test_protocol_listener_rejects_unsupported_hello() -> None:
    connection = FakeConnection(['{"type":"hello","role":"gui","version":2}\n'])
    listener = ipc.ProtocolListener(
        connection,
        parse=parse_message,
        version=1,
        peer_role='GUI',
        local_role='daemon',
        on_message=lambda listener, message: None,
    )

    listener.read()

    assert connection.sent == [
        (
            '{"type":"error","message":"GUI protocol version 2 is not supported; '
            'daemon requires 1"}\n'
        )
    ]
    assert connection.closed


def test_protocol_listener_requests_shutdown_once_message_arrives() -> None:
    shutdowns = 0
    connection = FakeConnection(
        [
            '{"type":"hello","role":"gui","version":1}\n',
            '{"type":"shutdown"}\n',
        ]
    )

    def request_shutdown() -> None:
        nonlocal shutdowns
        shutdowns += 1

    listener = ipc.ProtocolListener(
        connection,
        parse=parse_message,
        version=1,
        peer_role='GUI',
        local_role='daemon',
        on_message=lambda listener, message: None,
        request_shutdown=request_shutdown,
    )

    listener.read()

    assert shutdowns == 1


def test_protocol_client_sends_hello_and_reads_messages() -> None:
    received: list[object] = []
    connection = FakeConnection(['{"type":"app","value":"x"}\n'])

    def append_message(message: object) -> bool:
        received.append(message)
        return True

    client = ipc.ProtocolClient(
        Path('/tmp/reccy.sock'),
        parse=parse_message,
        version=1,
        local_role='gui',
        peer_role='Daemon',
        connect=lambda endpoint: connection,
        on_message=append_message,
    )

    client.start()

    assert _eventually(lambda: received == [AppMessage(type='app', value='x')])
    assert connection.sent == ['{"type":"hello","role":"gui","version":1}\n']


def test_protocol_client_reports_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = FakeConnection(['{"type":"error","message":"bad"}\n'])
    client = ipc.ProtocolClient(
        Path('/tmp/reccy.sock'),
        parse=parse_message,
        version=1,
        local_role='gui',
        peer_role='Daemon',
        connect=lambda endpoint: connection,
        on_message=lambda message: True,
    )

    client.start()

    assert _eventually(lambda: client.closed)
    assert capsys.readouterr().err == 'bad\n'


def test_parse_message_uses_supplied_adapter() -> None:
    parsed = ipc.parse_message('{"type":"app","value":"x"}', MESSAGE)

    assert parsed == AppMessage(type='app', value='x')
    with pytest.raises(ValidationError):
        ipc.parse_message('{"type":"missing"}', MESSAGE)


def test_rpc_server_handles_requests_and_publishes_events() -> None:
    received: list[rpc.Event] = []
    server = rpc.Server(
        Path('/tmp/reccy-rpc-control.sock'),
        Path('/tmp/reccy-rpc-events.sock'),
        lambda request: {'command': request.command},
        role='test',
    )
    server.start()
    subscriber = rpc.EventClient(Path('/tmp/reccy-rpc-events.sock'), received.append)
    try:
        subscriber.start()
        assert _eventually(lambda: len(server.event_connections) == 1)
        response = rpc.Client(Path('/tmp/reccy-rpc-control.sock')).call('status')
        server.publish('error', message='disk full')
        assert response == {'command': 'status'}
        assert _eventually(
            lambda: received == [rpc.Event(name='error', data={'message': 'disk full'})]
        )
    finally:
        subscriber.close()
        server.close()


class FakeListener:
    def __init__(self, endpoint: str, *, family: str) -> None:
        self.endpoint = endpoint
        self.family = family
        self.closed = False
        self.pipe = FakePipe()

    def accept(self) -> 'FakePipe':
        return self.pipe

    def close(self) -> None:
        self.closed = True


class FakePipe:
    def __init__(self, received: list[str] | None = None) -> None:
        self.received = received or []
        self.sent: list[str] = []
        self.closed = False

    def recv(self) -> str:
        if not self.received:
            raise EOFError
        return self.received.pop(0)

    def send(self, message: str) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(
        self,
        received: list[str] | None = None,
        *,
        broken: bool = False,
    ) -> None:
        self.broken = broken
        self.closed = False
        self.received = received or []
        self.sent: list[str] = []

    def read_lines(self) -> typing.Iterator[str]:
        return iter(self.received)

    def write(self, message: str) -> bool:
        self.sent.append(message)
        return not self.broken

    def close(self) -> None:
        self.closed = True


def _eventually(check: typing.Callable[[], bool]) -> bool:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return False
