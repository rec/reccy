import sys

import pytest

from recy import cli, config, subprocess, validators
from recy.errors import RecyError


def test_route_command_dispatches_to_selected_command() -> None:
    calls: list[list[str]] = []

    def run(arguments: list[str]) -> int:
        calls.append(arguments)
        return 7

    assert (
        cli.route_command({'run': run}, ['run', '--host', '127.0.0.1'], prog='app') == 7
    )
    assert calls == [['--host', '127.0.0.1']]


def test_route_command_reports_unknown_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.route_command({}, ['missing'], prog='app') == 2

    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'unknown command: missing' in captured.err
    assert 'Usage: app {} ...' in captured.err


def test_run_main_prints_user_facing_errors(capsys: pytest.CaptureFixture[str]) -> None:
    def fail() -> int:
        raise RecyError('bad config')

    assert cli.run_main(fail) == 1
    assert capsys.readouterr().err == 'ERROR: bad config\n'


def test_prefix_spec_parses_named_values() -> None:
    spec = config.prefix_spec({'fast': 10, 'slow': 1}, 'SPEED')

    assert spec.instance_from_str(['fast']) == 10
    assert spec.str_from_instance(10) == ['10']
    with pytest.raises(ValueError, match='Cannot understand SPEED="medium"'):
        spec.instance_from_str(['medium'])


def test_app_command_uses_module_for_source_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, 'executable', '/venv/bin/python')
    monkeypatch.delattr(sys, 'frozen', raising=False)

    assert subprocess.app_command('lyte', 'run-daemon') == [
        '/venv/bin/python',
        '-m',
        'lyte',
        'run-daemon',
    ]


def test_app_command_uses_executable_for_frozen_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, 'executable', '/Applications/lyte')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)

    assert subprocess.app_command('lyte', 'run-daemon') == [
        '/Applications/lyte',
        'run-daemon',
    ]


def test_validators_reject_bad_values() -> None:
    assert validators.identifier('showco-ui') == 'showco-ui'
    assert validators.environment_variable('SHOWCO_DAEMON') == 'SHOWCO_DAEMON'
    assert validators.positive_number(0.5) == 0.5
    assert validators.non_negative_number(0) == 0
    assert validators.sorted_values([1, 2, 2, 3]) == [1, 2, 2, 3]

    with pytest.raises(ValueError):
        validators.identifier('Showco')
    with pytest.raises(ValueError):
        validators.environment_variable('showco_daemon')
    with pytest.raises(ValueError):
        validators.positive_number(0)
    with pytest.raises(ValueError):
        validators.non_negative_number(-1)
    with pytest.raises(ValueError):
        validators.sorted_values([2, 1])


def test_subprocess_run_uses_no_shell() -> None:
    result = subprocess.run([sys.executable, '-c', 'print("ok")'])
    assert result.stdout == 'ok\n'
