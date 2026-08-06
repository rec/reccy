import json
import os
import subprocess as sp
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ValidationError

from . import paths as paths_module
from . import renderers
from .models import (
    DaemonMetadata,
    DaemonStatus,
    Platform,
    ServiceDefinition,
    ServiceSpec,
    StatusResult,
)


class ServiceController:
    def __init__(
        self,
        service: ServiceSpec,
        platform: Platform,
        home: Path | None = None,
        runner: Callable[..., sp.CompletedProcess[str]] | None = None,
        status_model: type[BaseModel] = DaemonStatus,
        status_error_attribute: str = 'ipc_error',
        status_error_label: str = 'IPC error',
    ) -> None:
        self.service = service
        self.platform = platform
        self.paths = paths_module.service_paths(service, platform, home)
        self.runner = runner or sp.run
        self.status_model = status_model
        self.status_error_attribute = status_error_attribute
        self.status_error_label = status_error_label

    def install(self, metadata: DaemonMetadata) -> StatusResult:
        self._write_metadata(metadata)
        if self.platform == Platform.macos:
            self._write_definition(
                renderers.macos_launch_agent(metadata, self.paths, self.service)
            )
            self._run(
                ['launchctl', 'bootstrap', f'gui/{_uid()}', str(self.paths.service)]
            )
        elif self.platform == Platform.windows:
            self._write_windows_task(metadata)
            self._run(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    _register_windows_task_command(self.paths.service),
                ]
            )
        else:
            self._write_definition(
                renderers.linux_systemd_unit(metadata, self.paths, self.service)
            )
            self._run(['systemctl', '--user', 'daemon-reload'])
            self._run(['systemctl', '--user', 'enable', self.service.systemd_unit])
            self._run(['systemctl', '--user', 'start', self.service.systemd_unit])
        return StatusResult(installed=True, running=True)

    def uninstall(self) -> StatusResult:
        if self.platform == Platform.macos:
            self._run(
                ['launchctl', 'bootout', f'gui/{_uid()}', str(self.paths.service)],
                check=False,
            )
        elif self.platform == Platform.windows:
            self._run(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    _unregister_windows_task_command(self.service.name),
                ],
                check=False,
            )
        else:
            self._run(
                ['systemctl', '--user', 'stop', self.service.systemd_unit],
                check=False,
            )
            self._run(
                ['systemctl', '--user', 'disable', self.service.systemd_unit],
                check=False,
            )
            self._run(['systemctl', '--user', 'daemon-reload'], check=False)

        for path in [self.paths.service, self.paths.metadata, self.paths.status]:
            path.unlink(missing_ok=True)
        return StatusResult(installed=False, running=False)

    def start(self) -> StatusResult:
        if self.platform == Platform.macos:
            self._run(
                ['launchctl', 'bootstrap', f'gui/{_uid()}', str(self.paths.service)]
            )
        elif self.platform == Platform.windows:
            self._run(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    _start_windows_task_command(self.service.name),
                ]
            )
        else:
            self._run(['systemctl', '--user', 'start', self.service.systemd_unit])
        return StatusResult(installed=True, running=True)

    def stop(self) -> StatusResult:
        if self.platform == Platform.macos:
            self._run(
                ['launchctl', 'bootout', f'gui/{_uid()}', str(self.paths.service)]
            )
        elif self.platform == Platform.windows:
            self._run(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    _stop_windows_task_command(self.service.name),
                ]
            )
        else:
            self._run(['systemctl', '--user', 'stop', self.service.systemd_unit])
        return StatusResult(installed=True, running=False)

    def restart(self) -> StatusResult:
        self.stop()
        return self.start()

    def status(self) -> StatusResult:
        installed = self.paths.metadata.exists() or self.paths.service.exists()
        if self.platform == Platform.macos:
            result = self._run(
                ['launchctl', 'print', f'gui/{_uid()}/{self.service.launchd_label}'],
                check=False,
                capture_output=True,
            )
        elif self.platform == Platform.windows:
            result = self._run(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    _get_windows_task_command(self.service.name),
                ],
                check=False,
                capture_output=True,
            )
        else:
            result = self._run(
                ['systemctl', '--user', 'is-active', self.service.systemd_unit],
                check=False,
                capture_output=True,
            )
        details = (result.stdout or result.stderr or '').strip()
        status = self._read_status(self.paths.status)
        if status and (error := getattr(status, self.status_error_attribute, None)):
            details = '\n'.join(
                p for p in [details, f'{self.status_error_label}: {error}'] if p
            )
        return StatusResult(
            health=status,
            installed=installed,
            running=result.returncode == 0,
            details=details,
        )

    def _write_metadata(self, metadata: DaemonMetadata) -> None:
        self.paths.metadata.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomically(self.paths.metadata, renderers.metadata_json(metadata))

    def _write_definition(self, definition: ServiceDefinition) -> None:
        definition.path.parent.mkdir(parents=True, exist_ok=True)
        definition.path.write_text(definition.content)
        self.paths.stdout_log.parent.mkdir(parents=True, exist_ok=True)

    def _write_windows_task(self, metadata: DaemonMetadata) -> None:
        task = renderers.windows_task(metadata, self.paths, self.service)
        self.paths.service.parent.mkdir(parents=True, exist_ok=True)
        self.paths.service.write_text(
            json.dumps(task.model_dump(mode='json'), indent=2) + '\n'
        )
        self.paths.stdout_log.parent.mkdir(parents=True, exist_ok=True)

    def _run(
        self,
        command: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
    ) -> sp.CompletedProcess[str]:
        return self.runner(
            command,
            check=check,
            text=True,
            capture_output=capture_output,
        )

    def _read_status(self, path: Path) -> BaseModel | None:
        if not path.exists():
            return None
        try:
            return self.status_model.model_validate_json(path.read_text())
        except ValidationError:
            return None


class ServiceRegistry:
    def __init__(
        self,
        services: Mapping[str, ServiceSpec],
        *,
        platform: Platform | None = None,
        home: Path | None = None,
        runner: Callable[..., sp.CompletedProcess[str]] | None = None,
        status_models: Mapping[str, type[BaseModel]] | None = None,
        status_error_attributes: Mapping[str, str] | None = None,
        status_error_labels: Mapping[str, str] | None = None,
    ) -> None:
        self.services = dict(services)
        self.platform = platform or paths_module.current_platform()
        self.home = home
        self.runner = runner
        self.status_models = dict(status_models or {})
        self.status_error_attributes = dict(status_error_attributes or {})
        self.status_error_labels = dict(status_error_labels or {})

    def controller(self, name: str) -> ServiceController:
        return ServiceController(
            self.services[name],
            self.platform,
            self.home,
            self.runner,
            status_model=self.status_models.get(name, DaemonStatus),
            status_error_attribute=self.status_error_attributes.get(name, 'ipc_error'),
            status_error_label=self.status_error_labels.get(name, 'IPC error'),
        )

    def status(self, name: str) -> StatusResult:
        return self.controller(name).status()

    def report_status(
        self,
        service_names: list[str],
        *,
        output: TextIO = sys.stdout,
        error_output: TextIO = sys.stderr,
    ) -> int:
        failures = 0
        for name in service_names:
            if name not in self.services:
                print(f'unknown service: {name}', file=error_output)
                failures += 1
                continue
            result = self.status(name)
            print_service_status(name, result, output=output)
            if result.running is not True:
                failures += 1
        return 0 if failures == 0 else 1


def print_service_status(
    name: str, result: StatusResult, *, output: TextIO = sys.stdout
) -> None:
    state = 'active' if result.running else 'inactive'
    print(f'{name}: {state}', file=output)
    if result.details:
        print(result.details, file=output)


def _register_windows_task_command(path: Path) -> str:
    return (
        '$task = Get-Content '
        + _powershell_string(path)
        + ' | ConvertFrom-Json; '
        + '$action = New-ScheduledTaskAction -Execute $task.executable '
        + '-Argument $task.argument_string '
        + '-WorkingDirectory $task.working_directory; '
        + '$trigger = New-ScheduledTaskTrigger -AtLogOn; '
        + '$settings = New-ScheduledTaskSettingsSet -RestartCount 3 '
        + '-RestartInterval (New-TimeSpan -Minutes 1); '
        + 'Register-ScheduledTask -TaskName $task.task_name '
        + '-Action $action -Trigger $trigger -Settings $settings -Force'
    )


def _unregister_windows_task_command(name: str) -> str:
    return (
        f'Unregister-ScheduledTask -TaskName {_powershell_value(name)} -Confirm:$false'
    )


def _start_windows_task_command(name: str) -> str:
    return f'Start-ScheduledTask -TaskName {_powershell_value(name)}'


def _stop_windows_task_command(name: str) -> str:
    return f'Stop-ScheduledTask -TaskName {_powershell_value(name)}'


def _get_windows_task_command(name: str) -> str:
    return f'Get-ScheduledTask -TaskName {_powershell_value(name)}'


def _powershell_string(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _powershell_value(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _uid() -> int:
    try:
        return os.getuid()
    except AttributeError:
        return 0


def _write_text_atomically(path: Path, content: str) -> None:
    tmp = path.with_name(f'.{path.name}.tmp')
    with tmp.open('w') as fp:
        fp.write(content)
        fp.flush()
        os.fsync(fp.fileno())
    tmp.replace(path)
