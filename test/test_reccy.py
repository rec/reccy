import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel

from reccy.configuration import settings
from reccy.protocol import ipc, rpc
from reccy.reccy import MutableAttribute, Reccy, ReccyStatus
from reccy.services import models


class Settings(BaseModel, frozen=True):
    enabled: bool = False


def test_error_status_retains_latest_thousand(caplog: pytest.LogCaptureFixture) -> None:
    class Application(Reccy):
        name = 'error-retention'

    application = Application()
    for i in range(1005):
        application.publish_error(f'error {i}')
    errors = application.status_snapshot().errors
    assert len(errors) == 1000
    assert errors[0].message == 'error 5'
    assert errors[-1].message == 'error 1004'
    assert len(caplog.records) == 1005
    assert caplog.records[0].message == 'error 0'


class Status(ReccyStatus):
    state: str = 'idle'


class Application(Reccy):
    name = 'application'
    service_spec = models.ServiceSpec(
        name='application',
        display_name='Application',
        description='Test application',
        launchd_label='test.application',
        daemon_env_var='APPLICATION_DAEMON',
        windows_pipe=r'\\.\pipe\application',
    )
    settings_model = Settings
    status_model = Status
    rpc_enabled = True

    def rpc_command(self, request: rpc.Request) -> rpc.Result:
        return {'type': 'application_status', 'command': request.command}

    def status_snapshot(self) -> Status:
        return Status(running=self._started, errors=self._errors.copy(), state='ready')

    def mutable_attributes(self) -> list[MutableAttribute]:
        return [MutableAttribute(address='enabled', value=True)]

    def set_attr(self, address: str, value: object) -> MutableAttribute:
        if address != 'enabled' or not isinstance(value, bool):
            raise ValueError('enabled must be a boolean')
        return MutableAttribute(address=address, value=value)


@pytest.mark.parametrize('has_service', [True, False])
def test_windows_application_uses_paired_named_pipes(has_service: bool) -> None:
    class WindowsApplication(Reccy):
        name = 'application'
        service_spec = Application.service_spec if has_service else None

    application = WindowsApplication(platform=models.Platform.windows)
    assert application.control_endpoint == r'\\.\pipe\application'
    assert application.event_endpoint == r'\\.\pipe\application-events'
    for e in [application.control_endpoint, application.event_endpoint]:
        assert isinstance(ipc.server_backend(e), ipc.WindowsPipeServerBackend)


def test_service_status_reads_configured_model_with_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(
        command: list[str], *, check: bool, text: bool, capture_output: bool
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout='active', stderr='')

    class DefaultSnapshotApplication(Reccy):
        name = 'application'
        service_spec = Application.service_spec
        status_model = Status

    application = DefaultSnapshotApplication(
        home=tmp_path, platform=models.Platform.linux
    )
    monkeypatch.setattr(subprocess, 'run', run)
    application.publish_status()
    status = application.service_status().health
    assert isinstance(status, Status)
    assert status.state == 'idle'
    assert status.errors == []
    application.publish_error('disk full')
    status = application.service_status().health
    assert isinstance(status, Status)
    assert [e.message for e in status.errors] == ['disk full']


def test_settings_are_optional_and_saved_atomically(tmp_path: Path) -> None:
    application = Application(home=tmp_path)

    assert application.load_settings() is None
    application.save_settings(Settings(enabled=True))

    assert application.load_settings() == Settings(enabled=True)
    assert not application.settings_path.with_name('.settings.json.tmp').exists()


def test_write_json_model_writes_compact_json_atomically(tmp_path: Path) -> None:
    path = tmp_path / 'status.json'

    settings.write_json_model(path, Settings(enabled=True))

    assert path.read_text() == '{"enabled":true}\n'
    assert not path.with_name('.status.json.tmp').exists()


def test_write_json_model_can_skip_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings.os, 'fsync', pytest.fail)

    settings.write_json_model(
        tmp_path / 'status.json', Settings(enabled=True), sync=False
    )


def test_reccy_starts_rpc_and_writes_status(tmp_path: Path) -> None:
    application = Application(home=Path('/tmp/reccy-test'))
    application.start()
    try:
        response = rpc.Client(application.control_endpoint).call('status')
        status = Status.model_validate_json(application.status_path.read_text())
        application.publish_error('disk full')

        assert response['errors'] == []
        assert response['running'] is True
        assert response['state'] == 'ready'
        assert isinstance(response['updated_at'], float)
        assert status.running
        assert (
            Status.model_validate_json(application.status_path.read_text())
            .errors[0]
            .message
            == 'disk full'
        )
    finally:
        application.close()


def test_reccy_handles_mutable_attribute_commands() -> None:
    application = Application(home=Path('/tmp/reccy-mutable'))
    application.start()
    try:
        client = rpc.Client(application.control_endpoint)

        assert client.call('mutable_attributes') == {
            'attributes': [{'address': 'enabled', 'value': True}]
        }
        assert client.call('set_attr', address='enabled', value=False) == {
            'address': 'enabled',
            'value': False,
        }
        with pytest.raises(ConnectionError, match='enabled must be a boolean'):
            client.call('set_attr', address='enabled', value='false')
    finally:
        application.close()


def test_reccy_derives_paired_service_endpoints(tmp_path: Path) -> None:
    application = Application(home=tmp_path)

    assert (
        application.control_endpoint == tmp_path / '.local/state/application/gui.sock'
    )
    assert (
        application.event_endpoint == tmp_path / '.local/state/application/events.sock'
    )


def test_reccy_uses_its_name_as_the_default_daemon_module(tmp_path: Path) -> None:
    application = Application(home=tmp_path)

    assert application.service_metadata(['run']).module == 'application'
