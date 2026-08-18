import json
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from . import models, paths


def service_metadata(
    platform: models.Platform,
    module: str,
    daemon_argv: list[str],
    paths: models.ServicePaths,
) -> models.DaemonMetadata:
    return models.DaemonMetadata(
        argv=daemon_argv,
        module=module,
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
        'ProgramArguments': _service_runner_arguments(value, paths, service),
        'RunAtLoad': True,
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
    command = shlex.join(_service_runner_arguments(value, paths, service))
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
    service_paths = paths.service_paths(service, value.platform, home)
    command = shlex.join(_service_runner_arguments(value, service_paths, service))
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
    arguments = _service_runner_arguments(value, paths, service)[1:]
    return models.WindowsTaskDefinition(
        task_name=service.name,
        arguments=arguments,
        argument_string=subprocess.list2cmdline(arguments),
        working_directory=Path.home(),
        log=paths.log,
    )


def _service_runner_arguments(
    value: models.DaemonMetadata,
    paths: models.ServicePaths,
    service: models.ServiceSpec,
) -> list[str]:
    return [
        sys.executable,
        '-m',
        'reccy.service_runner',
        str(paths.log),
        service.name,
        value.module,
        *value.argv,
    ]
