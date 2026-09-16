import re
from enum import auto
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator
from strenum import StrEnum

from ..configuration import validators


class Platform(StrEnum):
    linux = auto()
    macos = auto()
    windows = auto()


class ServiceSpec(BaseModel, frozen=True):
    name: Annotated[str, AfterValidator(validators.identifier)]
    display_name: Annotated[str, AfterValidator(validators.non_empty_string)]
    description: Annotated[str, AfterValidator(validators.non_empty_string)]
    launchd_label: Annotated[str, AfterValidator(validators.non_empty_string)]
    daemon_env_var: Annotated[str, AfterValidator(validators.environment_variable)]
    windows_pipe: Annotated[str, AfterValidator(validators.non_empty_string)]

    @field_validator('launchd_label')
    @classmethod
    def validate_launchd_label(cls, value: str) -> str:
        if value in {'.', '..'} or not re.fullmatch(r'[A-Za-z0-9_.-]+', value):
            raise ValueError('launchd_label must be a filename-safe service identifier')
        return value

    @field_validator('display_name', 'description')
    @classmethod
    def validate_single_line(cls, value: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('service text must not contain control characters')
        return value

    @property
    def systemd_unit(self) -> str:
        return f'{self.name}.service'

    @property
    def desktop_file(self) -> str:
        return f'{self.name}.desktop'

    @property
    def metadata_file(self) -> str:
        return f'{self.name}/daemon.json'

    @property
    def status_file(self) -> str:
        return f'{self.name}/status.json'

    @property
    def scheduled_task_file(self) -> str:
        return f'{self.name}/{self.name}-scheduled-task.json'

    @property
    def socket_file(self) -> str:
        return f'{self.name}/gui.sock'

    @property
    def log_file(self) -> str:
        return f'{self.name}/{self.name}.log'


class DaemonMetadata(BaseModel, frozen=True):
    version: Literal[1] = 1
    argv: list[str] = Field(default_factory=list)
    module: str
    platform: Platform
    control_endpoint: str
    event_endpoint: str | None = None


class DaemonStatus(BaseModel):
    client_count: int = 0
    errors: list[str] = Field(default_factory=list)
    ipc_error: str | None = None
    running: bool = False
    updated_at: float = 0.0
    fields: dict[str, object] = Field(default_factory=dict)


class ServicePaths(BaseModel, frozen=True):
    home: Path = Field(default_factory=Path.home)
    metadata: Path
    service: Path
    status: Path
    log: Path
    control_endpoint: Path | str
    event_endpoint: Path | str | None = None


class ServiceDefinition(BaseModel, frozen=True):
    path: Path
    content: str


class WindowsTaskDefinition(BaseModel, frozen=True):
    task_name: str
    arguments: list[str]
    argument_string: str
    working_directory: Path
    log: Path


class StatusResult(BaseModel):
    installed: bool
    running: bool | None = None
    details: str = ''
    health: BaseModel | None = None
