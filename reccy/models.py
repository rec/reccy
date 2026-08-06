from enum import auto
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field
from strenum import StrEnum

from . import validators


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
    def stdout_log_file(self) -> str:
        return f'{self.name}/{self.name}.out.log'

    @property
    def stderr_log_file(self) -> str:
        return f'{self.name}/{self.name}.err.log'


class DaemonMetadata(BaseModel, frozen=True):
    version: int = 1
    argv: list[str] = Field(default_factory=list)
    executable: Path
    platform: Platform
    control_endpoint: str


class DaemonStatus(BaseModel):
    client_count: int = 0
    errors: list[str] = Field(default_factory=list)
    ipc_error: str | None = None
    running: bool = False
    updated_at: float = 0.0
    fields: dict[str, object] = Field(default_factory=dict)


class ServicePaths(BaseModel, frozen=True):
    metadata: Path
    service: Path
    status: Path
    stdout_log: Path
    stderr_log: Path
    control_endpoint: Path | str


class ServiceDefinition(BaseModel, frozen=True):
    path: Path
    content: str


class WindowsTaskDefinition(BaseModel, frozen=True):
    task_name: str
    executable: Path
    arguments: list[str]
    argument_string: str
    working_directory: Path
    stdout_log: Path
    stderr_log: Path


class StatusResult(BaseModel):
    installed: bool
    running: bool | None = None
    details: str = ''
    health: BaseModel | None = None
