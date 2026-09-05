"""Compatibility imports for :mod:`reccy.services.renderers`."""

from .services.renderers import (
    linux_systemd_unit,
    linux_xdg_autostart,
    macos_launch_agent,
    metadata_json,
    service_metadata,
    windows_task,
)

__all__ = [
    'linux_systemd_unit',
    'linux_xdg_autostart',
    'macos_launch_agent',
    'metadata_json',
    'service_metadata',
    'windows_task',
]
