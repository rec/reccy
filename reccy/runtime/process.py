from __future__ import annotations

import logging
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence

OUTPUT_CHUNK_SIZE = 4096


class OutputTail:
    def __init__(self, line_count: int = 80) -> None:
        self._lines: deque[str] = deque(maxlen=line_count)
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None

    def append(self, line: str) -> None:
        with self._lock:
            self._lines.append(line[-OUTPUT_CHUNK_SIZE:])

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for capture to finish; return False if the timeout expires."""
        if self._reader is None:
            return True
        self._reader.join(timeout)
        return not self._reader.is_alive()

    def text(self) -> str:
        with self._lock:
            return ''.join(self._lines).strip()


class ManagedProcess:
    def __init__(
        self,
        command: list[str],
        *,
        run_process: Callable[[list[str]], subprocess.Popen[bytes]] = (
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
    tail._reader = threading.Thread(
        target=_read_stderr,
        args=(process, tail, on_line),
        name=thread_name,
        daemon=True,
    )
    tail._reader.start()
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
    try:
        while line := process.stderr.readline(OUTPUT_CHUNK_SIZE):
            text = line.decode(errors='replace')
            tail.append(text)
            if on_line is not None:
                on_line(text)
    finally:
        # A callback failure must not leave a child blocked on its stderr pipe.
        # The original exception reaches threading.excepthook after EOF.
        while line := process.stderr.readline(OUTPUT_CHUNK_SIZE):
            tail.append(line.decode(errors='replace'))


def _write_output(
    logger: logging.Logger, label: str, output: str | bytes | None
) -> None:
    if output is None:
        return
    if isinstance(output, bytes):
        output = output.decode(errors='replace')
    if text := output.strip():
        logger.error('%s:\n%s', label, text)
