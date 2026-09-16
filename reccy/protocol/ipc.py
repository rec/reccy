import json
import logging
import pickle
import queue
import socket
import stat
import sys
import threading
import typing
from multiprocessing import connection
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

PIPE_CONNECT_TIMEOUT = 0.2
SOCKET_TIMEOUT = 0.2
WRITE_TIMEOUT = 0.2
HANDSHAKE_TIMEOUT = 1.0
CONNECTING_PIPES: set[str] = set()
CONNECTING_PIPES_LOCK = threading.Lock()


class Connection(typing.Protocol):
    def read_lines(self, *, max_bytes: int | None = None) -> typing.Iterator[str]: ...

    def write(self, message: str) -> bool: ...

    def close(self) -> None: ...


class ServerBackend(typing.Protocol):
    def start(self) -> None: ...

    def accept(self) -> Connection | None: ...

    def close(self) -> None: ...


class Hello(BaseModel):
    type: typing.Literal['hello']
    role: str
    version: int


class Reply(BaseModel):
    type: typing.Literal['reply']
    id: str
    ok: bool
    result: dict[str, object] | None = None
    message: str | None = None


class Shutdown(BaseModel):
    type: typing.Literal['shutdown']


class Error(BaseModel):
    type: typing.Literal['error']
    message: str


def server_backend(endpoint: str | Path) -> ServerBackend:
    if isinstance(endpoint, Path):
        return UnixSocketServerBackend(endpoint)
    return WindowsPipeServerBackend(endpoint)


def client_connection(endpoint: str | Path) -> Connection:
    if isinstance(endpoint, Path):
        return UnixSocketConnection.connect(endpoint)
    return WindowsPipeConnection.connect(endpoint)


def message_json(
    message: BaseModel | dict[str, object] | str, *, exclude_none: bool = False
) -> str:
    if isinstance(message, BaseModel):
        return message.model_dump_json(exclude_none=exclude_none) + '\n'
    return json.dumps(message, separators=(',', ':')) + '\n'


def parse_message(line: str, adapter: TypeAdapter[object]) -> object:
    return adapter.validate_json(line)


class ProtocolListener:
    def __init__(
        self,
        conn: Connection,
        *,
        parse: typing.Callable[[str], object],
        version: int,
        peer_role: str,
        local_role: str,
        on_message: typing.Callable[['ProtocolListener', object], None],
        request_shutdown: typing.Callable[[], None] | None = None,
        on_validation_error: typing.Callable[[str], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.conn = conn
        self.parse = parse
        self.version = version
        self.peer_role = peer_role
        self.local_role = local_role
        self.on_message = on_message
        self.request_shutdown = request_shutdown
        self.on_validation_error = on_validation_error
        self.logger = logger or logging.getLogger(__name__)
        self.handshake_complete = False
        self.lock = threading.Lock()

    def start(self, *, name: str = 'IpcListener') -> None:
        threading.Thread(target=self.read, daemon=True, name=name).start()

    def write(self, message: str) -> bool:
        with self.lock:
            return self.conn.write(message)

    def write_model(self, message: BaseModel, *, exclude_none: bool = False) -> bool:
        return self.write(message_json(message, exclude_none=exclude_none))

    def close(self) -> None:
        self.conn.close()

    def read(self) -> None:
        for line in self.conn.read_lines():
            try:
                message = self.parse(line)
            except ValidationError as e:
                self.logger.warning('Ignoring malformed IPC message')
                if self.on_validation_error is not None:
                    self.on_validation_error(str(e))
                continue
            if isinstance(message, Hello):
                if not self.receive_hello(message):
                    return
                continue
            if not self.handshake_complete:
                self.reject(f'{self.peer_role} hello required before other messages')
                return
            if isinstance(message, Shutdown):
                if self.request_shutdown:
                    self.request_shutdown()
                continue
            self.on_message(self, message)

    def receive_hello(self, message: Hello) -> bool:
        if message.version != self.version:
            error = (
                f'{self.peer_role} protocol version {message.version} is not '
                f'supported; {self.local_role} requires {self.version}'
            )
            self.reject(error)
            return False
        self.handshake_complete = True
        self.write_model(
            Hello(type='hello', role=self.local_role, version=self.version)
        )
        return True

    def reject(self, message: str) -> None:
        self.write_model(Error(type='error', message=message))
        self.close()


class ProtocolClient:
    def __init__(
        self,
        endpoint: str | Path,
        *,
        parse: typing.Callable[[str], object],
        version: int,
        local_role: str,
        peer_role: str,
        on_message: typing.Callable[[object], bool],
        connect: typing.Callable[[str | Path], Connection] = client_connection,
    ) -> None:
        self.endpoint = endpoint
        self.parse = parse
        self.version = version
        self.local_role = local_role
        self.peer_role = peer_role
        self.on_message = on_message
        self.connect = connect
        self.connection: Connection | None = None
        self.closed = True
        self.handshake_complete = False
        self.handshake_timer: threading.Timer | None = None

    def start(self, *, thread_name: str = 'IpcClient') -> None:
        if self.connection is not None:
            raise RuntimeError('IPC client has already been started')
        started = False
        try:
            self.connection = self.connect(self.endpoint)
            self.closed = False
            self.handshake_complete = False
            self.handshake_timer = threading.Timer(HANDSHAKE_TIMEOUT, self.close)
            self.handshake_timer.start()
            if not self.write_model(
                Hello(type='hello', role=self.local_role, version=self.version)
            ):
                raise BrokenPipeError(f'Could not send {self.local_role} hello')
            threading.Thread(target=self.read, daemon=True, name=thread_name).start()
            started = True
        finally:
            if not started:
                self.close()

    def close(self) -> None:
        self.closed = True
        if self.handshake_timer is not None:
            self.handshake_timer.cancel()
        if self.connection is not None:
            self.connection.close()

    def shutdown(self) -> None:
        self.write_model(Shutdown(type='shutdown'))

    def write_model(self, message: BaseModel, *, exclude_none: bool = False) -> bool:
        return self.write(message_json(message, exclude_none=exclude_none))

    def write(self, message: str) -> bool:
        if self.connection is None:
            return False
        return self.connection.write(message)

    def read(self) -> None:
        if self.connection is None:
            return
        try:
            for line in self.connection.read_lines():
                try:
                    message = self.parse(line)
                except ValidationError:
                    continue
                if isinstance(message, Error):
                    print(message.message, file=sys.stderr)
                    return
                if isinstance(message, Hello):
                    if message.version != self.version:
                        print(
                            f'{self.peer_role} protocol version '
                            f'{message.version} is not '
                            f'supported; {self.local_role} requires {self.version}',
                            file=sys.stderr,
                        )
                        return
                    self.handshake_complete = True
                    if self.handshake_timer is not None:
                        self.handshake_timer.cancel()
                if not self.handshake_complete:
                    print(
                        f'{self.peer_role} hello required before other messages',
                        file=sys.stderr,
                    )
                    return
                if isinstance(message, Shutdown) or not self.on_message(message):
                    return
        finally:
            self.close()


class UnixSocketServerBackend:
    def __init__(self, endpoint: Path) -> None:
        self.endpoint = endpoint
        self.socket: socket.socket | None = None

    def start(self) -> None:
        self.endpoint.parent.mkdir(parents=True, exist_ok=True)
        remove_stale_socket(self.endpoint)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.endpoint))
        self.socket.listen()
        self.socket.settimeout(SOCKET_TIMEOUT)

    def accept(self) -> Connection | None:
        if self.socket is None:
            return None
        try:
            conn, _ = self.socket.accept()
        except TimeoutError:
            return None
        except OSError:
            return None
        return UnixSocketConnection(conn)

    def close(self) -> None:
        if self.socket is not None:
            self.socket.close()


class UnixSocketConnection:
    def __init__(self, conn: socket.socket) -> None:
        self.conn = conn
        self.file = conn.makefile('rb')
        self.write_lock = threading.Lock()

    @classmethod
    def connect(cls, endpoint: Path) -> 'UnixSocketConnection':
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(SOCKET_TIMEOUT)
        conn.connect(str(endpoint))
        conn.settimeout(None)
        return cls(conn)

    def read_lines(self, *, max_bytes: int | None = None) -> typing.Iterator[str]:
        try:
            while line := self.file.readline(
                -1 if max_bytes is None else max_bytes + 1
            ):
                if max_bytes is not None and len(line) > max_bytes:
                    raise ValueError('RPC request exceeds the size limit')
                yield line.decode('utf-8')
        except ValueError:
            if not self.file.closed:
                raise

    def write(self, message: str) -> bool:
        return _write_with_timeout(
            lambda: self.conn.sendall(message.encode()), self.close, self.write_lock
        )

    def close(self) -> None:
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.file.close()
        finally:
            self.conn.close()


class WindowsPipeServerBackend:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.listener: connection.Listener | None = None

    def start(self) -> None:
        self.listener = connection.Listener(self.endpoint, family='AF_PIPE')

    def accept(self) -> Connection | None:
        if self.listener is None:
            return None
        try:
            return WindowsPipeConnection(self.listener.accept())
        except OSError:
            return None

    def close(self) -> None:
        if self.listener is not None:
            self.listener.close()


class WindowsPipeConnection:
    def __init__(self, conn: connection.Connection) -> None:
        self.conn = conn
        self.write_lock = threading.Lock()

    @classmethod
    def connect(cls, endpoint: str) -> 'WindowsPipeConnection':
        return cls(connect_windows_pipe(endpoint))

    def read_lines(self, *, max_bytes: int | None = None) -> typing.Iterator[str]:
        while True:
            try:
                frame = self.conn.recv_bytes(maxlength=max_bytes)
            except (EOFError, OSError):
                return
            yield str(pickle.loads(frame))

    def write(self, message: str) -> bool:
        return _write_with_timeout(
            lambda: self.conn.send(message), self.close, self.write_lock
        )

    def close(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass


def remove_stale_socket(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise FileExistsError(f'Refusing to remove non-socket endpoint: {path}')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(SOCKET_TIMEOUT)
            conn.connect(str(path))
    except ConnectionRefusedError:
        path.unlink()


def connect_windows_pipe(endpoint: str) -> connection.Connection:
    with CONNECTING_PIPES_LOCK:
        if endpoint in CONNECTING_PIPES:
            raise TimeoutError(f'Timed out connecting to {endpoint}')
        CONNECTING_PIPES.add(endpoint)

    results: queue.Queue[connection.Connection | OSError | ValueError] = queue.Queue()

    def connect() -> None:
        try:
            result = connection.Client(endpoint, family='AF_PIPE')
        except (OSError, ValueError) as error:
            result = error
        with CONNECTING_PIPES_LOCK:
            CONNECTING_PIPES.discard(endpoint)
        results.put(result)

    threading.Thread(target=connect, daemon=True).start()
    try:
        result = results.get(timeout=PIPE_CONNECT_TIMEOUT)
    except queue.Empty:
        raise TimeoutError(f'Timed out connecting to {endpoint}') from None
    if isinstance(result, OSError | ValueError):
        raise result
    return result


def _write_with_timeout(
    send: typing.Callable[[], None],
    close: typing.Callable[[], None],
    lock: threading.Lock,
) -> bool:
    if not lock.acquire(timeout=WRITE_TIMEOUT):
        close()
        return False
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        close()

    timer = threading.Timer(WRITE_TIMEOUT, expire)
    try:
        timer.start()
        try:
            send()
        except (EOFError, OSError):
            return False
        return not expired.is_set()
    finally:
        timer.cancel()
        timer.join()
        lock.release()
