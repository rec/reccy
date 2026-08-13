import json
import plistlib
import shlex
import subprocess
from pathlib import Path

from . import models


def service_metadata(
    executable: Path,
    platform: models.Platform,
    daemon_argv: list[str],
    paths: models.ServicePaths,
) -> models.DaemonMetadata:
    return models.DaemonMetadata(
        argv=daemon_argv,
        executable=executable,
        platform=platform,
        control_endpoint=str(paths.control_endpoint),
        event_endpoint=str(paths.event_endpoint) if paths.event_endpoint else None,
    )


def metadata_json(value: models.DaemonMetadata) -> str:
    return json.dumps(value.model_dump(mode='json'), indent=2) + '\n'


def macos_launch_agent(
    value: models.DaemonMetadata,
    paths: models.ServicePaths,
    service: models.ServiceSpec,
) -> models.ServiceDefinition:
    plist = {
        'KeepAlive': True,
        'Label': service.launchd_label,
        'ProgramArguments': [_posix(value.executable), *value.argv],
        'RunAtLoad': True,
        'StandardErrorPath': _posix(paths.stderr_log),
        'StandardOutPath': _posix(paths.stdout_log),
        'WorkingDirectory': str(Path.home()),
        'EnvironmentVariables': {service.daemon_env_var: '1'},
    }
    content = plistlib.dumps(plist, sort_keys=True).decode()
    return models.ServiceDefinition(path=paths.service, content=content)


def linux_systemd_unit(
    value: models.DaemonMetadata,
    paths: models.ServicePaths,
    service: models.ServiceSpec,
) -> models.ServiceDefinition:
    command = shlex.join([_posix(value.executable), *value.argv])
    content = '\n'.join(
        [
            '[Unit]',
            f'Description={service.description}',
            'After=default.target',
            '',
            '[Service]',
            f'ExecStart={command}',
            f'Environment={service.daemon_env_var}=1',
            'Restart=always',
            'RestartSec=5',
            'WorkingDirectory=%h',
            'StandardOutput=journal',
            'StandardError=journal',
            '',
            '[Install]',
            'WantedBy=default.target',
            '',
        ]
    )
    return models.ServiceDefinition(path=paths.service, content=content)


def linux_xdg_autostart(
    value: models.DaemonMetadata,
    home: Path,
    service: models.ServiceSpec,
) -> models.ServiceDefinition:
    command = shlex.join([_posix(value.executable), *value.argv])
    path = home / '.config/autostart' / service.desktop_file
    content = '\n'.join(
        [
            '[Desktop Entry]',
            'Type=Application',
            f'Name={service.display_name}',
            f'Comment={service.description}',
            f'Exec={command}',
            'Terminal=false',
            'X-GNOME-Autostart-enabled=true',
            '',
        ]
    )
    return models.ServiceDefinition(path=path, content=content)


def windows_task(
    value: models.DaemonMetadata,
    paths: models.ServicePaths,
    service: models.ServiceSpec,
) -> models.WindowsTaskDefinition:
    return models.WindowsTaskDefinition(
        task_name=service.name,
        executable=value.executable,
        arguments=value.argv,
        argument_string=subprocess.list2cmdline(value.argv),
        working_directory=Path.home(),
        stdout_log=paths.stdout_log,
        stderr_log=paths.stderr_log,
    )


def _posix(path: Path) -> str:
    return path.as_posix()
