import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from pydantic import BaseModel

from reccy import service
from reccy.models import DaemonMetadata, DaemonStatus, Platform, ServiceSpec
from reccy.renderers import service_metadata
from reccy.service import ServiceController, ServiceRegistry


@pytest.fixture(autouse=True)
def executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'executable', '/opt/lyte/bin/lyte')


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
    metadata = service_metadata(Platform.linux, ['run-daemon'], controller.paths)

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
    service_spec = lyte_service()
    runner = FakeRunner()
    monkeypatch.setattr(service, '_uid', lambda: 501)
    controller = ServiceController(service_spec, Platform.macos, tmp_path, runner)
    metadata = service_metadata(Platform.macos, ['run-daemon'], controller.paths)

    controller.install(metadata)

    assert controller.paths.metadata.exists()
    assert controller.paths.service.exists()
    assert runner.commands == [
        ['launchctl', 'bootstrap', 'gui/501', str(controller.paths.service)]
    ]


def test_controller_writes_metadata_atomically(tmp_path: Path) -> None:
    service = lyte_service()
    controller = ServiceController(service, Platform.linux, tmp_path, FakeRunner())
    metadata = service_metadata(Platform.linux, ['run-daemon'], controller.paths)

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


def test_status_supports_custom_status_model(tmp_path: Path) -> None:
    service = lyte_service()
    controller = ServiceController(
        service,
        Platform.linux,
        tmp_path,
        FakeRunner(),
        status_model=CustomStatus,
        status_error_attribute='gui_ipc_error',
        status_error_label='GUI IPC error',
    )
    controller.paths.status.parent.mkdir(parents=True)
    controller.paths.status.write_text(
        CustomStatus(recording=True, gui_ipc_error='address in use').model_dump_json()
    )

    result = controller.status()

    assert result.details == 'active\nGUI IPC error: address in use'
    assert isinstance(result.health, CustomStatus)
    assert result.health.recording


def test_service_registry_reports_statuses(tmp_path: Path) -> None:
    runner = FakeRunner()
    registry = ServiceRegistry(
        {'lyte': lyte_service()},
        platform=Platform.linux,
        home=tmp_path,
        runner=runner,
    )
    output = StringIO()

    result = registry.report_status(['lyte'], output=output)

    assert result == 0
    assert output.getvalue() == 'lyte: active\nactive\n'
    assert runner.commands == [['systemctl', '--user', 'is-active', 'lyte.service']]


def test_service_registry_reports_unknown_services(tmp_path: Path) -> None:
    registry = ServiceRegistry(
        {'lyte': lyte_service()},
        platform=Platform.linux,
        home=tmp_path,
        runner=FakeRunner(),
    )
    error_output = StringIO()

    result = registry.report_status(['missing'], error_output=error_output)

    assert result == 1
    assert error_output.getvalue() == 'unknown service: missing\n'


def test_service_registry_supports_custom_status_model(tmp_path: Path) -> None:
    registry = ServiceRegistry(
        {'lyte': lyte_service()},
        platform=Platform.linux,
        home=tmp_path,
        runner=FakeRunner(),
        status_models={'lyte': CustomStatus},
        status_error_attributes={'lyte': 'gui_ipc_error'},
        status_error_labels={'lyte': 'GUI IPC error'},
    )
    controller = registry.controller('lyte')
    controller.paths.status.parent.mkdir(parents=True)
    controller.paths.status.write_text(
        CustomStatus(recording=True, gui_ipc_error='address in use').model_dump_json()
    )

    result = registry.status('lyte')

    assert result.details == 'active\nGUI IPC error: address in use'
    assert isinstance(result.health, CustomStatus)
    assert result.health.recording


def lyte_service() -> ServiceSpec:
    return ServiceSpec(
        name='lyte',
        display_name='lyte',
        description='lyte lighting daemon',
        launchd_label='com.swirly.lyte',
        daemon_env_var='LYTE_DAEMON',
        windows_pipe=r'\\.\pipe\lyte',
    )


class CustomStatus(BaseModel):
    recording: bool = False
    gui_ipc_error: str | None = None
