from pathlib import Path

from pydantic import BaseModel

from reccy import models, rpc, settings
from reccy.reccy import Reccy, ReccyStatus


class Settings(BaseModel, frozen=True):
    enabled: bool = False


class Status(ReccyStatus):
    state: str = 'idle'


class Application(Reccy):
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

    def rpc_response(self, request: rpc.Request) -> rpc.Response:
        return rpc.Response(id=request.id, ok=True, result={'command': request.command})

    def status_snapshot(self) -> Status:
        return Status(running=self._started, errors=self._errors.copy(), state='ready')


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


def test_reccy_starts_rpc_and_writes_status(tmp_path: Path) -> None:
    application = Application(home=Path('/tmp/reccy-test'))
    application.start()
    try:
        response = rpc.Client(application.control_endpoint).call('status')
        status = Status.model_validate_json(application.status_path.read_text())
        application.publish_error('disk full')

        assert response.result == {'command': 'status'}
        assert status.running
        assert (
            Status.model_validate_json(application.status_path.read_text())
            .errors[0]
            .message
            == 'disk full'
        )
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
