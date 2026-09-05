import subprocess

import pytest

from reccy.runtime import process


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
