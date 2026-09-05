"""Compatibility imports for :mod:`reccy.runtime.logging`."""

from .runtime.logging import RotatingLogStream, configure, get_logger

__all__ = ['RotatingLogStream', 'configure', 'get_logger']
