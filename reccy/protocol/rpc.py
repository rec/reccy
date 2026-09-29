from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from enum import StrEnum, auto
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from . import ipc

VERSION = 1
HANDSHAKE_TIMEOUT = 1.0
MAX_CONCURRENT_REQUESTS = 16
MAX_EVENT_CONNECTIONS = 16
MAX_REQUEST_BYTES = 64 * 1024
MAX_PENDING_EVENTS = 4
REQUEST_TIMEOUT = 30.0
SHUTDOWN_TIMEOUT = 5.0
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
            for line in lines:
                if expired.is_set():
                    raise TimeoutError(f'RPC request timed out after {self.timeout}s')
                try:
                    error = ipc.Error.model_validate_json(line)
                except ValidationError:
                    return TypeAdapter(str | dict[str, object]).validate_json(line)
                raise ConnectionError(error.message)
            if expired.is_set():
                raise TimeoutError(f'RPC request timed out after {self.timeout}s')
            raise ConnectionError('RPC server closed the connection')
        except (OSError, ValidationError, ValueError) as error:
            if expired.is_set():
                raise TimeoutError(
                    f'RPC request timed out after {self.timeout}s'
                ) from None
            if isinstance(error, (ValidationError, ValueError)):
                raise ConnectionError('Invalid RPC response') from error
            raise
        finally:
            timer.cancel()
            timer.join()
            connection.close()


class EventCloseReason(StrEnum):
    local_close = auto()
    peer_eof = auto()
    protocol_error = auto()
    callback_error = auto()
    transport_error = auto()
    timeout = auto()


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
        self.closed = True
        self.terminal_reason: EventCloseReason | None = None
        self._completion = threading.Event()
        self._close_lock = threading.Lock()

    def start(self) -> None:
        if self.connection is not None or self.terminal_reason is not None:
            raise RuntimeError('RPC event client has already been started')
        try:
            self.connection = ipc.client_connection(self.endpoint)
        except OSError:
            self._finish(EventCloseReason.transport_error)
            raise
        self.closed = False
        self.lines = self.connection.read_lines()
        expired = threading.Event()

        def expire() -> None:
            expired.set()
            self._finish(EventCloseReason.timeout)

        timer = threading.Timer(HANDSHAKE_TIMEOUT, expire)
        started = False
        timer.start()
        try:
            _hello(self.connection, self.role, self.lines)
            if not self.connection.write(ipc.message_json(Subscribe())):
                raise BrokenPipeError('Could not subscribe to RPC events')
            timer.cancel()
            timer.join()
            if expired.is_set():
                raise TimeoutError('RPC event subscription timed out')
            threading.Thread(target=self._read, daemon=True, name='RpcEvents').start()
            started = True
        except OSError as error:
            if expired.is_set():
                raise TimeoutError('RPC event subscription timed out') from None
            # _hello uses plain ConnectionError for a rejected/missing hello;
            # socket failures use its OSError subclasses.
            self._finish(
                EventCloseReason.protocol_error
                if type(error) is ConnectionError
                else EventCloseReason.transport_error
            )
            raise
        finally:
            timer.cancel()
            timer.join()
            if not started:
                self._finish(EventCloseReason.protocol_error)

    def close(self) -> None:
        self._finish(EventCloseReason.local_close)

    def wait_closed(self, timeout: float | None = None) -> bool:
        """Wait for transport closure; an already-running callback may finish later."""
        return self._completion.wait(timeout)

    def _finish(self, reason: EventCloseReason) -> None:
        with self._close_lock:
            if self.terminal_reason is not None:
                return
            self.terminal_reason = reason
            self.closed = True
        try:
            if self.connection is not None:
                self.connection.close()
        finally:
            self._completion.set()

    def _read(self) -> None:
        assert self.connection is not None
        assert self.lines is not None
        reason = EventCloseReason.peer_eof
        try:
            for line in self.lines:
                message = MESSAGE.validate_json(line)
                if not isinstance(message, Event):
                    reason = EventCloseReason.protocol_error
                    LOGGER.error(
                        'Unexpected RPC event-stream message: %s',
                        type(message).__name__,
                    )
                    return
                reason = EventCloseReason.callback_error
                self.on_event(message)
                reason = EventCloseReason.peer_eof
        except (OSError, ValidationError, UnicodeError) as error:
            if reason == EventCloseReason.callback_error:
                raise
            reason = (
                EventCloseReason.protocol_error
                if isinstance(error, (ValidationError, UnicodeError))
                else EventCloseReason.transport_error
            )
            LOGGER.error('RPC event stream failed: %s', error)
        finally:
            self._finish(reason)


class Server:
    """RPC server whose handlers receive a cooperative cancellation event."""

    def __init__(
        self,
        control_endpoint: Path | str,
        event_endpoint: Path | str,
        handle: Callable[[Request, threading.Event], Result],
        *,
        role: str,
        request_timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        if request_timeout <= 0:
            raise ValueError('request_timeout must be positive')
        self.control_backend = ipc.server_backend(control_endpoint)
        self.event_backend = ipc.server_backend(event_endpoint)
        self.handle = handle
        self.role = role
        self.request_timeout = request_timeout
        self.event_connections: list[ipc.Connection] = []
        self.event_queues: dict[ipc.Connection, queue.Queue[str | None]] = {}
        self.connections: list[ipc.Connection] = []
        self.threads: set[threading.Thread] = set()
        self.accept_threads: list[threading.Thread] = []
        self.cancellations: dict[ipc.Connection, threading.Event] = {}
        self.lock = threading.Lock()
        self.publish_lock = threading.Lock()
        self.request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self.event_slots = threading.BoundedSemaphore(MAX_EVENT_CONNECTIONS)
        self.running = False
        self._started_once = False

    def start(self) -> None:
        if self._started_once:
            raise RuntimeError('RPC server instances cannot be restarted')
        self._started_once = True
        started = False
        try:
            self.control_backend.start()
            self.event_backend.start()
            self.running = True
            control_thread = threading.Thread(
                target=self._accept_control, daemon=True, name='RpcControl'
            )
            event_thread = threading.Thread(
                target=self._accept_events, daemon=True, name='RpcEvents'
            )
            control_thread.start()
            self.accept_threads.append(control_thread)
            event_thread.start()
            self.accept_threads.append(event_thread)
            started = True
        finally:
            if not started:
                self.close()

    def close(self, timeout: float = SHUTDOWN_TIMEOUT) -> None:
        """Cancel workers and wait up to timeout; raise if any remain active."""
        if timeout < 0:
            raise ValueError('timeout must not be negative')
        deadline = time.monotonic() + timeout
        with self.lock:
            self.running = False
            connections, self.connections = self.connections, []
            self.event_connections = []
            cancellations = list(self.cancellations.values())
            queues, self.event_queues = self.event_queues, {}
        for cancellation in cancellations:
            cancellation.set()
        for pending in queues.values():
            _stop_event_queue(pending)
        self.control_backend.close()
        self.event_backend.close()
        for connection in connections:
            connection.close()
        for thread in self.accept_threads:
            if thread is threading.current_thread():
                raise RuntimeError('Cannot close RPC server from its own worker')
            thread.join(max(0, deadline - time.monotonic()))
        while True:
            with self.lock:
                workers = list(self.threads)
            if not workers:
                break
            for thread in workers:
                if thread is threading.current_thread():
                    raise RuntimeError('Cannot close RPC server from its own worker')
                thread.join(max(0, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                break
        with self.lock:
            unfinished = bool(self.threads)
        if unfinished or any(thread.is_alive() for thread in self.accept_threads):
            raise TimeoutError('RPC workers did not stop before shutdown deadline')

    def publish(self, name: str, **data: object) -> None:
        """Queue an event without waiting for subscribers; disconnect laggards."""
        message = ipc.message_json(Event(name=name, data=data))
        with self.publish_lock:
            with self.lock:
                for connection, pending in list(self.event_queues.items()):
                    try:
                        pending.put_nowait(message)
                    except queue.Full:
                        self.event_connections.remove(connection)
                        del self.event_queues[connection]
                        _stop_event_queue(pending)

    def _accept_control(self) -> None:
        while self.running:
            if (connection := self.control_backend.accept()) is not None:
                with self.lock:
                    if not self.running or not self.request_slots.acquire(
                        blocking=False
                    ):
                        connection.close()
                        continue
                    self.connections.append(connection)
                    thread = threading.Thread(
                        target=self._serve_control,
                        args=(connection,),
                        daemon=True,
                        name='RpcRequest',
                    )
                    self.threads.add(thread)
                    try:
                        thread.start()
                    except RuntimeError:
                        self.threads.remove(thread)
                        self.connections.remove(connection)
                        self.request_slots.release()
                        connection.close()
                        LOGGER.error('Could not start RPC request worker')

    def _accept_events(self) -> None:
        while self.running:
            if (connection := self.event_backend.accept()) is not None:
                with self.lock:
                    if not self.running or not self.event_slots.acquire(blocking=False):
                        connection.close()
                        continue
                    self.connections.append(connection)
                    thread = threading.Thread(
                        target=self._serve_events,
                        args=(connection,),
                        daemon=True,
                        name='RpcSubscription',
                    )
                    self.threads.add(thread)
                    try:
                        thread.start()
                    except RuntimeError:
                        self.threads.remove(thread)
                        self.connections.remove(connection)
                        self.event_slots.release()
                        connection.close()
                        LOGGER.error('Could not start RPC subscription worker')

    def _serve_control(self, connection: ipc.Connection) -> None:
        timer = threading.Timer(HANDSHAKE_TIMEOUT, connection.close)
        try:
            timer.start()
            lines = connection.read_lines(max_bytes=MAX_REQUEST_BYTES)
            try:
                _receive_hello(connection, self.role, lines)
            except (ConnectionError, ValidationError) as error:
                _write_error(connection, str(error))
                return
            for line in lines:
                if len(line.encode()) > MAX_REQUEST_BYTES:
                    _write_error(connection, 'RPC request exceeds the size limit')
                    return
                try:
                    message = MESSAGE.validate_json(line)
                except ValidationError as error:
                    _write_error(connection, str(error))
                    return
                if isinstance(message, Request):
                    timer.cancel()
                    if not self.running:
                        return
                    cancelled = threading.Event()
                    with self.lock:
                        if not self.running:
                            return
                        self.cancellations[connection] = cancelled

                    request_timer = threading.Timer(
                        self.request_timeout,
                        _expire_request,
                        args=(cancelled, connection),
                    )
                    try:
                        request_timer.start()
                        result = self.handle(message, cancelled)
                    except (AttributeError, KeyError, TypeError, ValueError) as error:
                        if not cancelled.is_set():
                            _write_error(connection, f'RPC handler failed: {error}')
                        return
                    finally:
                        request_timer.cancel()
                        with self.lock:
                            self.cancellations.pop(connection, None)
                    if not cancelled.is_set():
                        connection.write(ipc.message_json(result))
                    return
                _write_error(connection, 'RPC request required')
                return
        except ValueError as error:
            _write_error(connection, str(error))
        finally:
            timer.cancel()
            self._close_connection(connection)
            self.request_slots.release()
            with self.lock:
                self.threads.discard(threading.current_thread())

    def _serve_events(self, connection: ipc.Connection) -> None:
        timer = threading.Timer(HANDSHAKE_TIMEOUT, connection.close)
        try:
            timer.start()
            lines = connection.read_lines(max_bytes=MAX_REQUEST_BYTES)
            try:
                _receive_hello(connection, self.role, lines)
            except (ConnectionError, ValidationError) as error:
                _write_error(connection, str(error))
                return
            for line in lines:
                if len(line.encode()) > MAX_REQUEST_BYTES:
                    _write_error(connection, 'RPC request exceeds the size limit')
                    return
                try:
                    message = MESSAGE.validate_json(line)
                except ValidationError as error:
                    _write_error(connection, str(error))
                    return
                if isinstance(message, Subscribe):
                    timer.cancel()
                    with self.lock:
                        if not self.running:
                            return
                        self.event_connections.append(connection)
                        pending: queue.Queue[str | None] = queue.Queue(
                            maxsize=MAX_PENDING_EVENTS
                        )
                        self.event_queues[connection] = pending
                        writer = threading.Thread(
                            target=self._write_events,
                            args=(connection, pending),
                            daemon=True,
                            name='RpcEventWriter',
                        )
                        self.threads.add(writer)
                        try:
                            writer.start()
                        except RuntimeError:
                            self.threads.remove(writer)
                            LOGGER.error('Could not start RPC event writer')
                            return
                    for _ in lines:
                        pass
                    return
                _write_error(connection, 'RPC subscription required')
                return
        except ValueError as error:
            _write_error(connection, str(error))
        finally:
            timer.cancel()
            self._close_connection(connection)
            self.event_slots.release()
            with self.lock:
                self.threads.discard(threading.current_thread())

    def _write_events(
        self, connection: ipc.Connection, pending: queue.Queue[str | None]
    ) -> None:
        try:
            while (message := pending.get()) is not None:
                with self.lock:
                    if connection not in self.event_queues:
                        return
                if not connection.write(message):
                    return
        finally:
            self._close_connection(connection)
            with self.lock:
                self.threads.discard(threading.current_thread())

    def _close_connection(self, connection: ipc.Connection) -> None:
        with self.lock:
            if connection in self.event_connections:
                self.event_connections.remove(connection)
            if (pending := self.event_queues.pop(connection, None)) is not None:
                _stop_event_queue(pending)
            if connection in self.connections:
                self.connections.remove(connection)
        connection.close()


def _stop_event_queue(pending: queue.Queue[str | None]) -> None:
    while True:
        try:
            pending.put_nowait(None)
            return
        except queue.Full:
            try:
                pending.get_nowait()
            except queue.Empty:
                pass


def _expire_request(cancelled: threading.Event, connection: ipc.Connection) -> None:
    cancelled.set()
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


def _write_error(connection: ipc.Connection, message: str) -> None:
    LOGGER.error('Invalid RPC message: %s', message)
    connection.write(ipc.message_json(ipc.Error(type='error', message=message)))
