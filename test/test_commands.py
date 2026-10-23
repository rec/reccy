from pathlib import Path

import pytest

from reccy import cli
from reccy.documentation import Documentation
from reccy.release import ReleaseCommand


@pytest.mark.parametrize('arguments', [[], ['docs'], ['release']])
def test_help_does_not_modify_the_project(
    arguments: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as stopped:
        cli.main([*arguments, '--help'])

    assert stopped.value.code == 0
    assert not list(tmp_path.iterdir())
    assert not capsys.readouterr().err


@pytest.mark.parametrize('init', [False, True])
@pytest.mark.parametrize('force_flag', [None, '-f', '--force'])
def test_docs_passes_explicit_initialization(
    init: bool,
    force_flag: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bool, bool]] = []

    def publish(self: Documentation, init: bool = False, force: bool = False) -> str:
        calls.append((init, force))
        return 'https://example.github.io/project/'

    monkeypatch.setattr(Documentation, 'publish', publish)

    assert (
        cli.main(
            [
                'docs',
                *(['--init'] if init else []),
                *([force_flag] if force_flag else []),
            ]
        )
        == 0
    )
    assert calls == [(init, force_flag is not None)]


@pytest.mark.parametrize('part', ['patch', 'minor', 'major'])
def test_release_parses_the_version_part(
    part: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[ReleaseCommand] = []

    def release(command: ReleaseCommand) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(cli, 'release', release)

    assert cli.main(['release', part, '--publish']) == 0
    assert calls[0].part == part
    assert calls[0].publish is True


def test_release_defaults_to_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[ReleaseCommand] = []

    def release(command: ReleaseCommand) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(cli, 'release', release)

    assert cli.main(['release']) == 0
    assert calls[0].part == 'patch'
