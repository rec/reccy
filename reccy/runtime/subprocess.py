import subprocess
import sys
from collections.abc import Mapping, Sequence


def app_command(module: str, *args: str) -> list[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    return [sys.executable, '-m', module, *args]


def run(
    command: Sequence[str],
    *,
    capture_output: bool = True,
    check: bool = True,
    env: Mapping[str, str] | None = None,
    text: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Run a command with str output in text mode, bytes otherwise.

    With capture_output=False, stdout and stderr on the result are None.
    """
    return subprocess.run(
        command,
        capture_output=capture_output,
        check=check,
        env=env,
        text=text,
        timeout=timeout,
    )
