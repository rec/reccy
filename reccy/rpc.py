from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter

from . import ipc

VERSION = 1


class Request(BaseModel):
    type: Literal['request'] = 'request'
    id: str
    command: str
    params: dict[str, object] = Field(default_factory=dict)


class Response(BaseModel):
    type: Literal['response'] = 'response'
    id: str
    ok: bool
    result: dict[str, object] = Field(default_factory=dict)
    message: str | None = None


class CommandResult(BaseModel, frozen=True):
    ok: bool
    message: str
    result: dict[str, object]


class Event(BaseModel):
    type: Literal['event'] = 'event'
    name: str
    data: dict[str, object] = Field(default_factory=dict)


class Subscribe(BaseModel):
    type: Literal['subscribe'] = 'subscribe'


MESSAGE = TypeAdapter(ipc.Hello | ipc.Error | Request | Response | Event | Subscribe)


class Client:
    def __init__(self, endpoint: Path | str, *, role: str = 'client') -> None:
        self.endpoint = endpoint
        self.role = role

    def call(self, command: str, **params: object) -> Response:
        connection = ipc.client_connection(self.endpoint)
        try:
            lines = connection.read_lines()
            _hello(connection, self.role, lines)
            request = Request(id=str(uuid.uuid4()), command=command, params=params)
            if not connection.write(ipc.message_json(request)):
                raise BrokenPipeError('Could not send RPC request')
            for line in lines:
                message = MESSAGE.validate_json(line)
                if isinstance(message, Response) and message.id == request.id:
                    return message
                if isinstance(message, ipc.Error):
                    raise ConnectionError(message.message)
            raise ConnectionError('RPC server closed the connection')
        finally:
            connection.close()


class ClientAdapter:
    def __init__(
        self,
        endpoint: Path | str,
        *,
        role: str = 'client',
        error_prefix: str = 'RPC command failed',
    ) -> None:
        self.endpoint = endpoint
        self.role = role
        self.error_prefix = error_prefix

    def command(self, command: str, **params: object) -> CommandResult:
        try:
            response = self._call(command, **params)
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return CommandResult(
                ok=False,
                message=f'{self.error_prefix}: {error}',
                result={},
            )
        if response.ok:
            return CommandResult(ok=True, message='ok', result=response.result)
        return CommandResult(
            ok=False,
            message=response.message or self.error_prefix,
            result=response.result,
        )

    def _call(self, command: str, **params: object) -> Response:
        return Client(self.endpoint, role=self.role).call(command, **params)


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
        handle: Callable[[Request], Response],
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
        try:
            lines = connection.read_lines()
            _receive_hello(connection, self.role, lines)
            for line in lines:
                message = MESSAGE.validate_json(line)
                if isinstance(message, Request):
                    connection.write(ipc.message_json(self.handle(message)))
                    return
        finally:
            connection.close()

    def _serve_events(self, connection: ipc.Connection) -> None:
        try:
            lines = connection.read_lines()
            _receive_hello(connection, self.role, lines)
            for line in lines:
                if isinstance(MESSAGE.validate_json(line), Subscribe):
                    with self.lock:
                        self.event_connections.append(connection)
                    for _ in lines:
                        pass
                    return
        finally:
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
