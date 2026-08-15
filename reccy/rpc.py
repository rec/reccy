from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from . import ipc

VERSION = 1
HANDSHAKE_TIMEOUT = 1.0
LOGGER = logging.getLogger(__name__)


class Request(BaseModel):
    type: Literal['request'] = 'request'
    command: str
    params: dict[str, object] = Field(default_factory=dict)


Result = str | dict[str, object] | ipc.Error


class Event(BaseModel):
    type: Literal['event'] = 'event'
    name: str
    data: dict[str, object] = Field(default_factory=dict)


class Subscribe(BaseModel):
    type: Literal['subscribe'] = 'subscribe'


MESSAGE = TypeAdapter(ipc.Hello | ipc.Error | Request | Event | Subscribe)


class Client:
    def __init__(
        self, endpoint: Path | str, *, role: str = 'client', timeout: float = 1.0
    ) -> None:
        self.endpoint = endpoint
        self.role = role
        self.timeout = timeout

    def call(self, command: str, **params: object) -> str | dict[str, object]:
        connection = ipc.client_connection(self.endpoint)
        expired = threading.Event()

        def close_for_timeout() -> None:
            expired.set()
            connection.close()

        timer = threading.Timer(self.timeout, close_for_timeout)
        timer.start()
        try:
            lines = connection.read_lines()
            _hello(connection, self.role, lines)
            request = Request(command=command, params=params)
            if not connection.write(ipc.message_json(request)):
                raise BrokenPipeError('Could not send RPC request')
            try:
                for line in lines:
                    if expired.is_set():
                        raise TimeoutError(
                            f'RPC request timed out after {self.timeout}s'
                        )
                    message = MESSAGE.validate_json(line)
                    if isinstance(message, ipc.Error):
                        raise ConnectionError(message.message)
                    result = TypeAdapter(str | dict[str, object]).validate_json(line)
                    return result
            except OSError:
                if expired.is_set():
                    raise TimeoutError(
                        f'RPC request timed out after {self.timeout}s'
                    ) from None
                raise
            if expired.is_set():
                raise TimeoutError(f'RPC request timed out after {self.timeout}s')
            raise ConnectionError('RPC server closed the connection')
        finally:
            timer.cancel()
            connection.close()


class EventClient:
    def __init__(
        self,
        endpoint: Path | str,
        on_event: Callable[[Event], None],
        *,
        role: str = 'client',
    ) -> None:
        self.endpoint = endpoint
        self.on_event = on_event
        self.role = role
        self.connection: ipc.Connection | None = None
        self.lines: Iterator[str] | None = None

    def start(self) -> None:
        self.connection = ipc.client_connection(self.endpoint)
        self.lines = self.connection.read_lines()
        _hello(self.connection, self.role, self.lines)
        if not self.connection.write(ipc.message_json(Subscribe())):
            raise BrokenPipeError('Could not subscribe to RPC events')
        threading.Thread(target=self._read, daemon=True, name='RpcEvents').start()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()

    def _read(self) -> None:
        assert self.connection is not None
        assert self.lines is not None
        for line in self.lines:
            message = MESSAGE.validate_json(line)
            if isinstance(message, Event):
                self.on_event(message)


class Server:
    def __init__(
        self,
        control_endpoint: Path | str,
        event_endpoint: Path | str,
        handle: Callable[[Request], Result],
        *,
        role: str,
    ) -> None:
        self.control_backend = ipc.server_backend(control_endpoint)
        self.event_backend = ipc.server_backend(event_endpoint)
        self.handle = handle
        self.role = role
        self.event_connections: list[ipc.Connection] = []
        self.lock = threading.Lock()
        self.running = False

    def start(self) -> None:
        self.control_backend.start()
        self.event_backend.start()
        self.running = True
        threading.Thread(
            target=self._accept_control, daemon=True, name='RpcControl'
        ).start()
        threading.Thread(
            target=self._accept_events, daemon=True, name='RpcEvents'
        ).start()

    def close(self) -> None:
        self.running = False
        self.control_backend.close()
        self.event_backend.close()
        with self.lock:
            connections, self.event_connections = self.event_connections, []
        for connection in connections:
            connection.close()

    def publish(self, name: str, **data: object) -> None:
        message = ipc.message_json(Event(name=name, data=data))
        with self.lock:
            connections = list(self.event_connections)
        for connection in connections:
            if not connection.write(message):
                self._remove_event_connection(connection)

    def _accept_control(self) -> None:
        while self.running:
            if (connection := self.control_backend.accept()) is not None:
                threading.Thread(
                    target=self._serve_control,
                    args=(connection,),
                    daemon=True,
                    name='RpcRequest',
                ).start()

    def _accept_events(self) -> None:
        while self.running:
            if (connection := self.event_backend.accept()) is not None:
                threading.Thread(
                    target=self._serve_events,
                    args=(connection,),
                    daemon=True,
                    name='RpcSubscription',
                ).start()

    def _serve_control(self, connection: ipc.Connection) -> None:
        timer = threading.Timer(HANDSHAKE_TIMEOUT, connection.close)
        timer.start()
        try:
            lines = connection.read_lines()
            try:
                _receive_hello(connection, self.role, lines)
            except ValidationError as error:
                _write_error(connection, error)
                return
            timer.cancel()
            for line in lines:
                try:
                    message = MESSAGE.validate_json(line)
                except ValidationError as error:
                    _write_error(connection, error)
                    return
                if isinstance(message, Request):
                    connection.write(ipc.message_json(self.handle(message)))
                    return
        finally:
            timer.cancel()
            connection.close()

    def _serve_events(self, connection: ipc.Connection) -> None:
        timer = threading.Timer(HANDSHAKE_TIMEOUT, connection.close)
        timer.start()
        try:
            lines = connection.read_lines()
            try:
                _receive_hello(connection, self.role, lines)
            except ValidationError as error:
                _write_error(connection, error)
                return
            timer.cancel()
            for line in lines:
                try:
                    message = MESSAGE.validate_json(line)
                except ValidationError as error:
                    _write_error(connection, error)
                    return
                if isinstance(message, Subscribe):
                    with self.lock:
                        self.event_connections.append(connection)
                    for _ in lines:
                        pass
                    return
        finally:
            timer.cancel()
            self._remove_event_connection(connection)
            connection.close()

    def _remove_event_connection(self, connection: ipc.Connection) -> None:
        with self.lock:
            if connection in self.event_connections:
                self.event_connections.remove(connection)
        connection.close()


def _hello(connection: ipc.Connection, role: str, lines: Iterator[str]) -> None:
    hello = ipc.Hello(type='hello', role=role, version=VERSION)
    if not connection.write(ipc.message_json(hello)):
        raise BrokenPipeError(f'Could not send {role} hello')
    for line in lines:
        message = MESSAGE.validate_json(line)
        if isinstance(message, ipc.Hello) and message.version == VERSION:
            return
        if isinstance(message, ipc.Error):
            raise ConnectionError(message.message)
        break
    raise ConnectionError('RPC server did not send hello')


def _receive_hello(connection: ipc.Connection, role: str, lines: Iterator[str]) -> None:
    for line in lines:
        message = MESSAGE.validate_json(line)
        if isinstance(message, ipc.Hello) and message.version == VERSION:
            hello = ipc.Hello(type='hello', role=role, version=VERSION)
            connection.write(ipc.message_json(hello))
            return
        break
    connection.write(
        ipc.message_json(ipc.Error(type='error', message='RPC hello required'))
    )
    raise ConnectionError('RPC hello required')


def _write_error(connection: ipc.Connection, error: ValidationError) -> None:
    LOGGER.error('Invalid RPC message: %s', error)
    connection.write(ipc.message_json(ipc.Error(type='error', message=str(error))))
