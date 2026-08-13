from __future__ import annotations

import logging
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence


class OutputTail:
    def __init__(self, line_count: int = 80) -> None:
        self._lines: deque[str] = deque(maxlen=line_count)
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def text(self) -> str:
        with self._lock:
            return ''.join(self._lines).strip()


class ManagedProcess:
    def __init__(
        self,
        command: Sequence[str],
        *,
        run_process: Callable[[Sequence[str]], subprocess.Popen[bytes]] = (
            subprocess.Popen
        ),
    ) -> None:
        self.command = command
        self.run_process = run_process
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> subprocess.Popen[bytes]:
        if self.process is None or self.process.poll() is not None:
            self.process = self.run_process(self.command)
        return self.process

    def close(self) -> None:
        if self.process is not None:
            terminate(self.process)
        self.process = None


def capture_stderr(
    process: subprocess.Popen[bytes],
    on_line: Callable[[str], None] | None = None,
    *,
    thread_name: str = 'ProcessOutput',
) -> OutputTail:
    tail = OutputTail()
    if process.stderr is None:
        return tail
    threading.Thread(
        target=_read_stderr,
        args=(process, tail, on_line),
        name=thread_name,
        daemon=True,
    ).start()
    return tail


def run_silent(
    command: Sequence[str], *, text: bool = False
) -> subprocess.CompletedProcess[object]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=text,
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        report_failed_command(command, error.stdout, error.stderr)
        raise


def terminate(process: subprocess.Popen[bytes], *, timeout: float = 5) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def report_failed_process(
    command: Sequence[str],
    tail: OutputTail,
    *,
    logger: logging.Logger | None = None,
) -> None:
    report_failed_command(command, None, tail.text(), logger=logger)


def report_failed_command(
    command: Sequence[str],
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    *,
    logger: logging.Logger | None = None,
) -> None:
    logger = logger or logging.getLogger(__name__)
    logger.error('Command failed: %s', ' '.join(command))
    _write_output(logger, 'stdout', stdout)
    _write_output(logger, 'stderr', stderr)


def _read_stderr(
    process: subprocess.Popen[bytes],
    tail: OutputTail,
    on_line: Callable[[str], None] | None,
) -> None:
    assert process.stderr is not None
    for line in process.stderr:
        text = line.decode(errors='replace')
        tail.append(text)
        if on_line is not None:
            on_line(text)


def _write_output(
    logger: logging.Logger, label: str, output: str | bytes | None
) -> None:
    if output is None:
        return
    if isinstance(output, bytes):
        output = output.decode(errors='replace')
    if text := output.strip():
        logger.error('%s:\n%s', label, text)
