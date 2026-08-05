import subprocess
from pathlib import Path

import pytest

from recy import service as service_module
from recy.models import DaemonMetadata, DaemonStatus, Platform, ServiceSpec
from recy.renderers import service_metadata
from recy.service import ServiceController


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        *,
        check: bool,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='active\n' if capture_output else '',
            stderr='',
        )


def test_linux_controller_installs_user_service(tmp_path: Path) -> None:
    service = lyte_service()
    runner = FakeRunner()
    controller = ServiceController(service, Platform.linux, tmp_path, runner)
    metadata = service_metadata(
        Path('/opt/lyte/bin/lyte'), Platform.linux, ['run-daemon'], controller.paths
    )

    result = controller.install(metadata)

    assert result.installed
    assert result.running
    assert controller.paths.metadata.exists()
    assert controller.paths.service.exists()
    assert runner.commands == [
        ['systemctl', '--user', 'daemon-reload'],
        ['systemctl', '--user', 'enable', 'lyte.service'],
        ['systemctl', '--user', 'start', 'lyte.service'],
    ]


def test_macos_controller_installs_launch_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = lyte_service()
    runner = FakeRunner()
    monkeypatch.setattr(service_module, '_uid', lambda: 501)
    controller = ServiceController(service, Platform.macos, tmp_path, runner)
    metadata = service_metadata(
        Path('/opt/lyte/bin/lyte'), Platform.macos, ['run-daemon'], controller.paths
    )

    controller.install(metadata)

    assert controller.paths.metadata.exists()
    assert controller.paths.service.exists()
    assert runner.commands == [
        ['launchctl', 'bootstrap', 'gui/501', str(controller.paths.service)]
    ]


def test_controller_writes_metadata_atomically(tmp_path: Path) -> None:
    service = lyte_service()
    controller = ServiceController(service, Platform.linux, tmp_path, FakeRunner())
    metadata = service_metadata(
        Path('/opt/lyte/bin/lyte'), Platform.linux, ['run-daemon'], controller.paths
    )

    controller.install(metadata)

    assert (
        DaemonMetadata.model_validate_json(controller.paths.metadata.read_text())
        == metadata
    )
    assert not controller.paths.metadata.with_name('.daemon.json.tmp').exists()


def test_status_uses_platform_command(tmp_path: Path) -> None:
    service = lyte_service()
    runner = FakeRunner()
    controller = ServiceController(service, Platform.linux, tmp_path, runner)

    result = controller.status()

    assert not result.installed
    assert result.running
    assert result.details == 'active'
    assert runner.commands == [['systemctl', '--user', 'is-active', 'lyte.service']]


def test_status_reports_ipc_errors(tmp_path: Path) -> None:
    service = lyte_service()
    controller = ServiceController(service, Platform.linux, tmp_path, FakeRunner())
    controller.paths.status.parent.mkdir(parents=True)
    controller.paths.status.write_text(
        DaemonStatus(running=True, ipc_error='address in use').model_dump_json()
    )

    result = controller.status()

    assert result.details == 'active\nIPC error: address in use'
    assert result.health is not None
    assert result.health.ipc_error == 'address in use'


def lyte_service() -> ServiceSpec:
    return ServiceSpec(
        name='lyte',
        display_name='lyte',
        description='lyte lighting daemon',
        launchd_label='com.swirly.lyte',
        daemon_env_var='LYTE_DAEMON',
        windows_pipe=r'\\.\pipe\lyte',
    )
