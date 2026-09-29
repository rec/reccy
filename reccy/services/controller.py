import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ValidationError

from ..configuration.settings import write_text_atomically
from ..runtime.files import atomic_output
from . import models, renderers
from .paths import current_platform, service_paths

SERVICE_COMMAND_TIMEOUT = 30


class ServiceController:
    def __init__(
        self,
        service: models.ServiceSpec,
        platform: models.Platform,
        home: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        status_model: type[BaseModel] = models.DaemonStatus,
        status_error_attribute: str = 'ipc_error',
        status_error_label: str = 'IPC error',
    ) -> None:
        self.service = service
        self.platform = platform
        self.paths = service_paths(service, platform, home)
        self.runner = runner or subprocess.run
        self.status_model = status_model
        self.status_error_attribute = status_error_attribute
        self.status_error_label = status_error_label

    def install(self, metadata: models.DaemonMetadata) -> None:
        if getattr(sys, 'frozen', False):
            raise ValueError(
                'Service installation does not support frozen applications'
            )
        if metadata.platform != self.platform:
            raise ValueError('Service metadata platform does not match controller')
        if metadata.control_endpoint != str(self.paths.control_endpoint):
            raise ValueError('Service metadata control endpoint does not match service')
        expected_event = (
            str(self.paths.event_endpoint) if self.paths.event_endpoint else None
        )
        if metadata.event_endpoint != expected_event:
            raise ValueError('Service metadata event endpoint does not match service')
        original = {
            path: path.read_bytes() if path.exists() else None
            for path in (self.paths.metadata, self.paths.service)
        }
        log_existed = self.paths.log.exists()
        attempted: list[str] = []
        completed: list[str] = []
        try:
            self._write_metadata(metadata)
            self._ensure_log()
            if self.platform == models.Platform.macos:
                self._write_definition(
                    renderers.macos_launch_agent(metadata, self.paths, self.service)
                )
                attempted.append('bootstrap')
                self._run(
                    ['launchctl', 'bootstrap', f'gui/{_uid()}', str(self.paths.service)]
                )
                completed.append('bootstrap')
            elif self.platform == models.Platform.windows:
                self._write_windows_task(metadata)
                attempted.append('register')
                self._run(
                    [
                        'powershell',
                        '-NoProfile',
                        '-Command',
                        _register_windows_task_command(self.paths.service),
                    ]
                )
                completed.append('register')
                attempted.append('start')
                self.start()
                completed.append('start')
            else:
                self._write_definition(
                    renderers.linux_systemd_unit(metadata, self.paths, self.service)
                )
                attempted.append('reload')
                self._run(['systemctl', '--user', 'daemon-reload'])
                completed.append('reload')
                attempted.append('enable')
                self._run(['systemctl', '--user', 'enable', self.service.systemd_unit])
                completed.append('enable')
                attempted.append('start')
                self._run(['systemctl', '--user', 'start', self.service.systemd_unit])
                completed.append('start')
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as error:
            self._rollback_install(original, log_existed, attempted, completed, error)
            raise

    def uninstall(self) -> None:
        original = {
            path: path.read_bytes() if path.exists() else None
            for path in (self.paths.service, self.paths.metadata, self.paths.status)
        }
        was_enabled = was_running = False
        if self.platform == models.Platform.linux:
            enabled = self._run(
                ['systemctl', '--user', 'is-enabled', self.service.systemd_unit],
                check=False,
                capture_output=True,
            )
            was_enabled = enabled.returncode == 0 and (
                enabled.stdout or ''
            ).strip() in {
                'enabled',
                'enabled-runtime',
            }
            was_running = (
                self._run(
                    ['systemctl', '--user', 'is-active', self.service.systemd_unit],
                    check=False,
                ).returncode
                == 0
            )
        attempted = False
        try:
            if self.platform == models.Platform.macos:
                attempted = True
                self._run(
                    ['launchctl', 'bootout', f'gui/{_uid()}', str(self.paths.service)],
                )
            elif self.platform == models.Platform.windows:
                attempted = True
                self._run(
                    [
                        'powershell',
                        '-NoProfile',
                        '-Command',
                        _unregister_windows_task_command(self.service.name),
                    ],
                )
            else:
                attempted = True
                self._run(
                    ['systemctl', '--user', 'stop', self.service.systemd_unit],
                )
                self._run(
                    ['systemctl', '--user', 'disable', self.service.systemd_unit],
                )

            self.paths.service.unlink(missing_ok=True)
            if self.platform == models.Platform.linux:
                self._run(['systemctl', '--user', 'daemon-reload'])
            for path in (self.paths.metadata, self.paths.status):
                path.unlink(missing_ok=True)
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as error:
            self._restore_files(original, error)
            if attempted and original[self.paths.service] is not None:
                self._rollback_uninstall(was_enabled, was_running, error)
            raise

    def start(self) -> None:
        self._ensure_log()
        if self.platform == models.Platform.macos:
            domain = f'gui/{_uid()}'
            target = f'{domain}/{self.service.launchd_label}'
            loaded = self._run(
                ['launchctl', 'print', target],
                check=False,
                capture_output=True,
            )
            command = (
                ['launchctl', 'kickstart', target]
                if loaded.returncode == 0
                else ['launchctl', 'bootstrap', domain, str(self.paths.service)]
            )
            self._run(command)
        elif self.platform == models.Platform.windows:
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

    def stop(self) -> None:
        if self.platform == models.Platform.macos:
            self._run(
                ['launchctl', 'bootout', f'gui/{_uid()}', str(self.paths.service)]
            )
        elif self.platform == models.Platform.windows:
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

    def restart(self) -> None:
        self.stop()
        self.start()

    def status(self) -> models.StatusResult:
        installed = self.paths.metadata.exists() or self.paths.service.exists()
        if self.platform == models.Platform.macos:
            result = self._run(
                ['launchctl', 'print', f'gui/{_uid()}/{self.service.launchd_label}'],
                check=False,
                capture_output=True,
            )
        elif self.platform == models.Platform.windows:
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
        return models.StatusResult(
            health=status,
            installed=installed,
            running=_is_running(self.platform, result),
            details=details,
        )

    def _write_metadata(self, metadata: models.DaemonMetadata) -> None:
        self.paths.metadata.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(self.paths.metadata, renderers.metadata_json(metadata))

    def _write_definition(self, definition: models.ServiceDefinition) -> None:
        definition.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(definition.path, definition.content)

    def _write_windows_task(self, metadata: models.DaemonMetadata) -> None:
        task = renderers.windows_task(metadata, self.paths, self.service)
        self.paths.service.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(
            self.paths.service,
            json.dumps(task.model_dump(mode='json'), indent=2) + '\n',
        )

    def _rollback_install(
        self,
        original: dict[Path, bytes | None],
        log_existed: bool,
        attempted: list[str],
        completed: list[str],
        error: BaseException,
    ) -> None:
        commands: list[list[str]] = []
        if attempted and attempted[-1] not in completed:
            error.add_note(
                f'{attempted[-1]} may have partially completed; inspect manager state'
            )
        was_installed = original[self.paths.service] is not None
        if (
            self.platform == models.Platform.macos
            and 'bootstrap' in completed
            and not was_installed
        ):
            commands.append(
                ['launchctl', 'bootout', f'gui/{_uid()}', str(self.paths.service)]
            )
        elif (
            self.platform == models.Platform.windows
            and 'register' in completed
            and not was_installed
        ):
            commands.append(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    _unregister_windows_task_command(self.service.name),
                ]
            )
        elif self.platform == models.Platform.linux and not was_installed:
            if 'start' in attempted and 'enable' in completed:
                commands.append(
                    ['systemctl', '--user', 'stop', self.service.systemd_unit]
                )
            if 'enable' in completed:
                commands.append(
                    ['systemctl', '--user', 'disable', self.service.systemd_unit]
                )
        for command in commands:
            try:
                self._run(command)
            except (OSError, subprocess.SubprocessError) as rollback_error:
                error.add_note(f'Rollback command failed: {rollback_error}')
        self._restore_files(original, error)
        if not log_existed:
            try:
                if self.paths.log.exists() and self.paths.log.stat().st_size == 0:
                    self.paths.log.unlink()
            except OSError as rollback_error:
                error.add_note(f'Could not remove empty log: {rollback_error}')
        if self.platform == models.Platform.linux and attempted:
            commands = [['systemctl', '--user', 'daemon-reload']]
        elif self.platform == models.Platform.windows and attempted and was_installed:
            commands = [
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    _register_windows_task_command(self.paths.service),
                ]
            ]
        else:
            commands = []
        for command in commands:
            try:
                self._run(command)
            except (OSError, subprocess.SubprocessError) as rollback_error:
                error.add_note(f'Rollback command failed: {rollback_error}')

    def _restore_files(
        self, original: dict[Path, bytes | None], error: BaseException
    ) -> None:
        for path, content in original.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    with atomic_output(path, sync=True) as temporary:
                        temporary.write_bytes(content)
            except OSError as rollback_error:
                error.add_note(f'Could not restore {path}: {rollback_error}')

    def _rollback_uninstall(
        self, was_enabled: bool, was_running: bool, error: BaseException
    ) -> None:
        if self.platform == models.Platform.macos:
            commands = [
                ['launchctl', 'bootstrap', f'gui/{_uid()}', str(self.paths.service)]
            ]
        elif self.platform == models.Platform.windows:
            commands = [
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    _register_windows_task_command(self.paths.service),
                ]
            ]
        else:
            commands = [['systemctl', '--user', 'daemon-reload']]
            if was_enabled:
                commands.append(
                    ['systemctl', '--user', 'enable', self.service.systemd_unit]
                )
            if was_running:
                commands.append(
                    ['systemctl', '--user', 'start', self.service.systemd_unit]
                )
        for command in commands:
            try:
                self._run(command)
            except (OSError, subprocess.SubprocessError) as rollback_error:
                error.add_note(f'Rollback command failed: {rollback_error}')

    def _ensure_log(self) -> None:
        self.paths.log.parent.mkdir(parents=True, exist_ok=True)
        self.paths.log.touch(exist_ok=True)

    def _run(
        self,
        command: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(
            command,
            check=check,
            text=True,
            capture_output=capture_output,
            timeout=SERVICE_COMMAND_TIMEOUT,
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
        services: Mapping[str, models.ServiceSpec],
        *,
        platform: models.Platform | None = None,
        home: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        status_models: Mapping[str, type[BaseModel]] | None = None,
        status_error_attributes: Mapping[str, str] | None = None,
        status_error_labels: Mapping[str, str] | None = None,
    ) -> None:
        self.services = dict(services)
        self.platform = platform or current_platform()
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
            status_model=self.status_models.get(name, models.DaemonStatus),
            status_error_attribute=self.status_error_attributes.get(name, 'ipc_error'),
            status_error_label=self.status_error_labels.get(name, 'IPC error'),
        )

    def status(self, name: str) -> models.StatusResult:
        return self.controller(name).status()

    def report_status(
        self,
        service_names: list[str],
        *,
        output: TextIO | None = None,
        error_output: TextIO | None = None,
    ) -> int:
        output = sys.stdout if output is None else output
        error_output = sys.stderr if error_output is None else error_output
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
    name: str, result: models.StatusResult, *, output: TextIO | None = None
) -> None:
    output = sys.stdout if output is None else output
    state = 'active' if result.running else 'inactive'
    print(f'{name}: {state}', file=output)
    if result.details:
        print(result.details, file=output)


def _register_windows_task_command(path: Path) -> str:
    python = _powershell_value(sys.executable)
    return (
        '$task = Get-Content '
        + _powershell_string(path)
        + ' | ConvertFrom-Json; '
        + f'$action = New-ScheduledTaskAction -Execute {python} '
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
    return f'(Get-ScheduledTask -TaskName {_powershell_value(name)}).State'


def _is_running(
    platform: models.Platform, result: subprocess.CompletedProcess[str]
) -> bool:
    if result.returncode:
        return False
    if platform == models.Platform.macos:
        return any(
            line.strip() == 'state = running' for line in result.stdout.splitlines()
        )
    if platform == models.Platform.windows:
        return result.stdout.strip().casefold() == 'running'
    return True


def _powershell_string(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _powershell_value(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _uid() -> int:
    try:
        return os.getuid()
    except AttributeError:
        return 0
