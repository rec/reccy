"""Compatibility imports for :mod:`reccy.runtime.process`."""

from .runtime.process import (
    ManagedProcess,
    OutputTail,
    capture_stderr,
    report_failed_command,
    report_failed_process,
    run_silent,
    terminate,
)

__all__ = [
    'ManagedProcess',
    'OutputTail',
    'capture_stderr',
    'report_failed_command',
    'report_failed_process',
    'run_silent',
    'terminate',
]
