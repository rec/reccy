import os
import sys
from pathlib import Path

from .models import Platform, ServicePaths, ServiceSpec


def current_platform() -> Platform:
    if sys.platform == 'darwin':
        return Platform.macos
    if sys.platform == 'win32':
        return Platform.windows
    return Platform.linux


def service_paths(
    service: ServiceSpec, platform: Platform, home: Path | None = None
) -> ServicePaths:
    home = home or Path.home()
    if platform == Platform.macos:
        return ServicePaths(
            metadata=home / '.config' / service.metadata_file,
            service=home / 'Library/LaunchAgents' / f'{service.launchd_label}.plist',
            status=home / '.local/state' / service.status_file,
            log=home / 'Library/Logs' / service.log_file,
            control_endpoint=home / '.local/state' / service.socket_file,
            event_endpoint=home / '.local/state' / service.name / 'events.sock',
        )
    if platform == Platform.windows:
        appdata = Path(os.environ.get('APPDATA', home / 'AppData/Roaming'))
        local = Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local'))
        return ServicePaths(
            metadata=appdata / service.metadata_file,
            service=appdata / service.scheduled_task_file,
            status=local / service.status_file,
            log=local / service.name / 'logs' / f'{service.name}.log',
            control_endpoint=service.windows_pipe,
            event_endpoint=None,
        )
    return ServicePaths(
        metadata=home / '.config' / service.metadata_file,
        service=home / '.config/systemd/user' / service.systemd_unit,
        status=home / '.local/state' / service.status_file,
        log=home / '.local/state' / service.log_file,
        control_endpoint=home / '.local/state' / service.socket_file,
        event_endpoint=home / '.local/state' / service.name / 'events.sock',
    )
