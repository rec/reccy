"""Compatibility imports for :mod:`reccy.services.controller`."""

from .services.controller import (
    ServiceController,
    ServiceRegistry,
    print_service_status,
)
from .services.paths import current_platform, service_paths

__all__ = [
    'ServiceController',
    'ServiceRegistry',
    'current_platform',
    'print_service_status',
    'service_paths',
]
