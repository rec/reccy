from __future__ import annotations

import time
from logging import Logger
from pathlib import Path
from sys import executable
from typing import ClassVar

from pydantic import BaseModel, Field, PrivateAttr

from . import logging, models, paths, renderers, rpc, service, settings
from .errors import ReccyError


class ErrorRecord(BaseModel, frozen=True):
    timestamp: float = Field(default_factory=time.time)
    message: str


class ReccyStatus(BaseModel):
    running: bool = False
    updated_at: float = Field(default_factory=time.time)
    errors: list[ErrorRecord] = Field(default_factory=list)


class Reccy(BaseModel, frozen=True):
    service_spec: ClassVar[models.ServiceSpec | None] = None
    settings_model: ClassVar[type[BaseModel] | None] = None
    status_model: ClassVar[type[ReccyStatus] | None] = None
    rpc_enabled: ClassVar[bool] = False
    rpc_role: ClassVar[str | None] = None
    logger_name: ClassVar[str | None] = None

    platform: models.Platform = Field(default_factory=paths.current_platform)
    home: Path = Field(default_factory=Path.home)

    _errors: list[ErrorRecord] = PrivateAttr(default_factory=list)
    _rpc_server: rpc.Server | None = PrivateAttr(default=None)
    _started: bool = PrivateAttr(default=False)

    @property
    def name(self) -> str:
        if self.service_spec is not None:
            return self.service_spec.name
        return type(self).__name__.lower()

    @property
    def logger(self) -> Logger:
        return logging.get_logger(self.logger_name or type(self).__name__)

    @property
    def paths(self) -> models.ServicePaths:
        if self.service_spec is None:
            raise ReccyError('service_spec is required for service paths')
        return paths.service_paths(self.service_spec, self.platform, self.home)

    @property
    def settings_path(self) -> Path:
        return self.home / '.config' / self.name / 'settings.json'

    @property
    def status_path(self) -> Path:
        if self.service_spec is not None:
            return self.paths.status
        return self.home / '.local/state' / self.name / 'status.json'

    @property
    def control_endpoint(self) -> Path | str:
        if self.service_spec is not None:
            return self.paths.control_endpoint
        return self.home / '.local/state' / self.name / 'control.sock'

    @property
    def event_endpoint(self) -> Path | str:
        if self.service_spec is not None and self.paths.event_endpoint is not None:
            return self.paths.event_endpoint
        return self.home / '.local/state' / self.name / 'events.sock'

    def load_settings(self) -> BaseModel | None:
        if self.settings_model is None:
            raise ReccyError('settings_model is required to load settings')
        return settings.load(self.settings_path, self.settings_model)

    def save_settings(self, value: BaseModel) -> None:
        if self.settings_model is None:
            raise ReccyError('settings_model is required to save settings')
        if not isinstance(value, self.settings_model):
            raise ReccyError(f'settings must be {self.settings_model.__name__}')
        settings.save(self.settings_path, value)

    def service_controller(self) -> service.ServiceController:
        if self.service_spec is None:
            raise ReccyError('service_spec is required for service control')
        return service.ServiceController(self.service_spec, self.platform, self.home)

    def service_metadata(self, daemon_argv: list[str]) -> models.DaemonMetadata:
        return renderers.service_metadata(
            self.daemon_executable(), self.platform, daemon_argv, self.paths
        )

    def daemon_executable(self) -> Path:
        return Path(executable)

    def install_service(self, daemon_argv: list[str]) -> models.StatusResult:
        return self.service_controller().install(self.service_metadata(daemon_argv))

    def uninstall_service(self) -> models.StatusResult:
        return self.service_controller().uninstall()

    def start_service(self) -> models.StatusResult:
        return self.service_controller().start()

    def stop_service(self) -> models.StatusResult:
        return self.service_controller().stop()

    def restart_service(self) -> models.StatusResult:
        return self.service_controller().restart()

    def service_status(self) -> models.StatusResult:
        return self.service_controller().status()

    def start(self) -> None:
        logging.configure()
        if self.rpc_enabled:
            self._rpc_server = rpc.Server(
                self.control_endpoint,
                self.event_endpoint,
                self.rpc_response,
                role=self.rpc_role or self.name,
            )
            self._rpc_server.start()
        self._started = True
        self.publish_status()
        self.on_started()

    def close(self) -> None:
        if not self._started:
            return
        self.on_stopping()
        self._started = False
        self.publish_status()
        self.publish_event('stopped')
        if self._rpc_server is not None:
            self._rpc_server.close()
            self._rpc_server = None
        self.on_closed()

    def rpc_response(self, request: rpc.Request) -> rpc.Response:
        return rpc.Response(
            id=request.id,
            ok=False,
            message=f'unknown command {request.command}',
        )

    def status_snapshot(self) -> ReccyStatus:
        return ReccyStatus(running=self._started, errors=self._errors.copy())

    def publish_status(self) -> None:
        if self.status_model is None:
            return
        status = self.status_snapshot()
        settings.save(self.status_path, status)
        self.publish_event('status', status=status.model_dump(mode='json'))

    def publish_event(self, name: str, **data: object) -> None:
        if self._rpc_server is not None:
            self._rpc_server.publish(name, **data)

    def publish_error(self, message: str) -> None:
        self._errors.append(ErrorRecord(message=message))
        self.publish_status()
        self.publish_event('error', message=message)

    def on_started(self) -> None:
        pass

    def on_stopping(self) -> None:
        pass

    def on_closed(self) -> None:
        pass
