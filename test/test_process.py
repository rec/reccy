import subprocess
import threading
from io import BytesIO
from types import SimpleNamespace

import pytest

from reccy.runtime import process


def test_callback_failure_still_drains_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    errors: list[BaseException] = []
    calls: list[str] = []

    def failed(line: str) -> None:
        calls.append(line)
        raise ValueError('callback failed')

    monkeypatch.setattr(threading, 'excepthook', lambda e: errors.append(e.exc_value))
    stderr = BytesIO(b'first\nsecond\nlast\n')
    tail = process.capture_stderr(SimpleNamespace(stderr=stderr), failed)
    assert tail.wait(2)
    assert calls == ['first\n']
    assert tail.text().splitlines() == ['first', 'second', 'last']
    assert stderr.read() == b''
    assert len(errors) == 1
    assert str(errors[0]) == 'callback failed'


def test_stderr_without_newlines_has_bounded_capture() -> None:
    sizes: list[int] = []
    stderr = BytesIO(b'x' * 1_000_000)
    tail = process.capture_stderr(
        SimpleNamespace(stderr=stderr), lambda s: sizes.append(len(s))
    )
    assert tail.wait(2)
    assert max(sizes) <= process.OUTPUT_CHUNK_SIZE
    assert len(tail.text()) <= 80 * process.OUTPUT_CHUNK_SIZE
    assert sum(sizes) == 1_000_000


class FakeProcess:
    def __init__(self, *, wait_raises: bool = False) -> None:
        self.returncode: int | None = None
        self.wait_raises = wait_raises
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_raises:
            self.wait_raises = False
            raise subprocess.TimeoutExpired('command', timeout)
        self.returncode = 0
        return self.returncode


def test_managed_process_reuses_running_process() -> None:
    processes: list[FakeProcess] = []

    def run_process(command: object) -> FakeProcess:
        processes.append(FakeProcess())
        return processes[-1]

    managed = process.ManagedProcess(['command'], run_process=run_process)

    assert managed.start() is managed.start()
    assert len(processes) == 1


def test_managed_process_kills_after_termination_timeout() -> None:
    fake = FakeProcess(wait_raises=True)
    managed = process.ManagedProcess(['command'], run_process=lambda command: fake)
    managed.start()

    managed.close()

    assert fake.terminated
    assert fake.killed
    assert managed.process is None


def test_run_silent_reports_command_output(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def run(*args: object, **kwargs: object) -> object:
        raise subprocess.CalledProcessError(
            1,
            ['command'],
            output=b'stdout output',
            stderr=b'stderr output',
        )

    monkeypatch.setattr(process.subprocess, 'run', run)

    with pytest.raises(subprocess.CalledProcessError):
        process.run_silent(['command'])

    assert 'Command failed: command' in caplog.messages
    assert 'stdout:\nstdout output' in caplog.messages
    assert 'stderr:\nstderr output' in caplog.messages
