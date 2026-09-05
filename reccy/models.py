"""Compatibility imports for :mod:`reccy.services.models`."""

from .services.models import (
    DaemonMetadata,
    DaemonStatus,
    Platform,
    ServiceDefinition,
    ServicePaths,
    ServiceSpec,
    StatusResult,
    WindowsTaskDefinition,
)

__all__ = [
    'DaemonMetadata',
    'DaemonStatus',
    'Platform',
    'ServiceDefinition',
    'ServicePaths',
    'ServiceSpec',
    'StatusResult',
    'WindowsTaskDefinition',
]
