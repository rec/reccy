import plistlib
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from reccy import paths, renderers
from reccy.models import Platform, ServiceSpec


@pytest.fixture(autouse=True)
def executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'executable', '/opt/lyte/bin/lyte')


def test_paths_support_service_identity() -> None:
    service = lyte_service()

    linux = paths.service_paths(service, Platform.linux, Path('/home/tom'))
    macos = paths.service_paths(service, Platform.macos, Path('/Users/tom'))
    windows = paths.service_paths(service, Platform.windows, Path('C:/Users/tom'))

    assert linux.metadata == Path('/home/tom/.config/lyte/daemon.json')
    assert linux.service == Path('/home/tom/.config/systemd/user/lyte.service')
    assert linux.control_endpoint == Path('/home/tom/.local/state/lyte/gui.sock')
    assert macos.service == Path(
        '/Users/tom/Library/LaunchAgents/com.swirly.lyte.plist'
    )
    assert windows.control_endpoint == r'\\.\pipe\lyte'


def test_service_identity_is_validated() -> None:
    with pytest.raises(ValidationError):
        ServiceSpec(
            name='Lyte',
            display_name='lyte',
            description='lyte lighting daemon',
            launchd_label='com.swirly.lyte',
            daemon_env_var='LYTE_DAEMON',
            windows_pipe=r'\\.\pipe\lyte',
        )
    with pytest.raises(ValidationError):
        ServiceSpec(
            name='lyte',
            display_name='lyte',
            description='lyte lighting daemon',
            launchd_label='com.swirly.lyte',
            daemon_env_var='lyte_daemon',
            windows_pipe=r'\\.\pipe\lyte',
        )


def test_macos_launch_agent() -> None:
    service = lyte_service()
    service_paths = paths.service_paths(service, Platform.macos, Path('/Users/tom'))
    metadata = renderers.service_metadata(
        Platform.macos, 'lyte', ['run-daemon'], service_paths
    )

    definition = renderers.macos_launch_agent(metadata, service_paths, service)
    plist = plistlib.loads(definition.content.encode())

    assert definition.path == Path(
        '/Users/tom/Library/LaunchAgents/com.swirly.lyte.plist'
    )
    assert plist['Label'] == 'com.swirly.lyte'
    assert plist['ProgramArguments'] == [
        '/opt/lyte/bin/lyte',
        '-m',
        'reccy.service_runner',
        '/Users/tom/Library/Logs/lyte/lyte.log',
        'lyte',
        'lyte',
        'run-daemon',
    ]
    assert plist['EnvironmentVariables'] == {'LYTE_DAEMON': '1'}
    assert plist['RunAtLoad'] is True
    assert plist['KeepAlive'] is True


def test_linux_systemd_unit() -> None:
    service = lyte_service()
    service_paths = paths.service_paths(service, Platform.linux, Path('/home/tom'))
    metadata = renderers.service_metadata(
        Platform.linux,
        'lyte',
        ['run-daemon', '--midi', 'Launchkey'],
        service_paths,
    )

    definition = renderers.linux_systemd_unit(metadata, service_paths, service)

    assert definition.path == Path('/home/tom/.config/systemd/user/lyte.service')
    assert 'Description=lyte lighting daemon' in definition.content
    assert (
        'ExecStart=/opt/lyte/bin/lyte -m reccy.service_runner '
        '/home/tom/.local/state/lyte/lyte.log lyte lyte run-daemon --midi Launchkey'
        in definition.content
    )
    assert 'Environment=LYTE_DAEMON=1' in definition.content
    assert 'RECCY_LOG_PATH' not in definition.content
    assert 'Restart=always' in definition.content
    assert 'StandardOutput=journal' not in definition.content
    assert 'StandardError=journal' not in definition.content
    assert 'WantedBy=default.target' in definition.content


def test_linux_xdg_autostart() -> None:
    service = lyte_service()
    service_paths = paths.service_paths(service, Platform.linux, Path('/home/tom'))
    metadata = renderers.service_metadata(
        Platform.linux, 'lyte', ['run-daemon'], service_paths
    )

    definition = renderers.linux_xdg_autostart(metadata, Path('/home/tom'), service)

    assert definition.path == Path('/home/tom/.config/autostart/lyte.desktop')
    assert 'Type=Application' in definition.content
    assert (
        'Exec=/opt/lyte/bin/lyte -m reccy.service_runner '
        '/home/tom/.local/state/lyte/lyte.log lyte lyte run-daemon'
        in definition.content
    )
    assert 'Terminal=false' in definition.content


def test_windows_task_definition() -> None:
    service = lyte_service()
    service_paths = paths.service_paths(service, Platform.windows, Path('C:/Users/tom'))
    metadata = renderers.service_metadata(
        Platform.windows,
        'lyte',
        ['run-daemon', 'Main Rig'],
        service_paths,
    )

    task = renderers.windows_task(metadata, service_paths, service)

    assert task.task_name == 'lyte'
    assert task.arguments == [
        '-m',
        'reccy.service_runner',
        'C:/Users/tom/AppData/Local/lyte/logs/lyte.log',
        'lyte',
        'lyte',
        'run-daemon',
        'Main Rig',
    ]
    assert task.log.name == 'lyte.log'


def lyte_service() -> ServiceSpec:
    return ServiceSpec(
        name='lyte',
        display_name='lyte',
        description='lyte lighting daemon',
        launchd_label='com.swirly.lyte',
        daemon_env_var='LYTE_DAEMON',
        windows_pipe=r'\\.\pipe\lyte',
    )
