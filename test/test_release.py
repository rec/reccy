from pathlib import Path
from unittest.mock import Mock, call

import pytest

from reccy import release
from reccy.documentation import Documentation
from reccy.errors import ReccyError
from reccy.release import ReleaseCommand, bump_cargo_version, checked_paths


@pytest.mark.parametrize('dynamic', [False, True])
@pytest.mark.parametrize('publish', [False, True])
def test_release_preserves_python_and_maturin_publication_order(
    dynamic: bool,
    publish: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project: dict[str, object] = {'name': 'example', 'requires-python': '>=3.13'}
    if dynamic:
        project['dynamic'] = ['version']
    actions = Mock()
    monkeypatch.setattr(Documentation, 'publish', actions.docs)
    monkeypatch.setattr(release, 'run', actions.run)
    monkeypatch.setattr(release, 'run_linter', actions.lint)
    monkeypatch.setattr(release, 'run_pyupgrade', actions.upgrade)
    monkeypatch.setattr(release, 'build_project', actions.build)
    monkeypatch.setattr(release, 'clean_dist', actions.clean_dist)
    monkeypatch.setattr(
        release,
        'git_output',
        Mock(side_effect=['', 'main', 'git@github.com:rec/example.git', '']),
    )
    monkeypatch.setattr(
        release,
        'read_pyproject',
        lambda: {'project': project, 'build-system': {'build-backend': 'maturin'}},
    )
    monkeypatch.setattr(release, 'command_output', Mock(return_value='1.2.3'))
    monkeypatch.setattr(release, 'command_fails', Mock(return_value=False))
    monkeypatch.setattr(release, 'bump_cargo_version', actions.bump)
    actions.bump.return_value = '1.2.3'
    monkeypatch.setattr(
        release,
        'checked_paths',
        lambda name: (['example', 'test'], ['example', 'test']),
    )
    monkeypatch.delenv('VIRTUAL_ENV', raising=False)

    assert release.release(ReleaseCommand(part='minor', publish=publish)) == 0

    assert actions.mock_calls[0] == call.docs()
    actions.run.assert_any_call('uv', 'run', 'pytest')
    actions.lint.assert_any_call('uv', 'run', 'ty', 'check', 'example')
    if dynamic:
        actions.bump.assert_any_call('minor', dry_run=True)
        actions.bump.assert_any_call('minor')
        actions.run.assert_any_call('git', 'add', 'Cargo.toml', 'Cargo.lock')
    else:
        actions.run.assert_any_call('uv', 'version', '--bump', 'minor', '--no-sync')
        actions.run.assert_any_call('git', 'add', 'pyproject.toml', 'uv.lock')
    tag_push = actions.mock_calls.index(call.run('git', 'push', 'origin', 'v1.2.3'))
    github_release = actions.mock_calls.index(
        call.run(
            'gh',
            'release',
            'create',
            'v1.2.3',
            '--repo',
            'git@github.com:rec/example.git',
            '--verify-tag',
            '--generate-notes',
        )
    )
    cleanup = actions.mock_calls.index(call.clean_dist())
    assert tag_push < github_release < cleanup
    if publish:
        assert actions.mock_calls[-2] == call.run('uv', 'build')
        assert actions.mock_calls[-1] == call.run('uv', 'publish')
    else:
        assert actions.mock_calls[-1] == call.clean_dist()


def test_documentation_failure_stops_before_release_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(['', 'main'])

    def git_output(*args: str) -> str:
        return next(states)

    def publish(self: Documentation, init: bool = False) -> str:
        raise ReccyError('documentation failed')

    def read_pyproject() -> dict[str, object]:
        pytest.fail('release work started before documentation succeeded')

    monkeypatch.setattr(release, 'git_output', git_output)
    monkeypatch.setattr(Documentation, 'publish', publish)
    monkeypatch.setattr(release, 'read_pyproject', read_pyproject)
    monkeypatch.delenv('VIRTUAL_ENV', raising=False)

    with pytest.raises(ReccyError, match='documentation failed'):
        release.release(ReleaseCommand())


@pytest.mark.parametrize(
    'part, expected', [('patch', '1.2.4'), ('minor', '1.3.0'), ('major', '2.0.0')]
)
def test_cargo_version_bump_preserves_other_manifest_content(
    part: str,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / 'Cargo.toml'
    original = '[package]\nname = "example"\nversion = "1.2.3" # release\n'
    manifest.write_text(original)

    assert bump_cargo_version(part, dry_run=True) == expected
    assert manifest.read_text() == original
    assert bump_cargo_version(part) == expected
    assert manifest.read_text() == original.replace('1.2.3', expected)


def test_verification_includes_install_and_test_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ['example', 'test', 'install']:
        (tmp_path / name).mkdir()

    lint, upgrade = checked_paths('example')

    assert lint == ['example', 'install', 'test']
    assert upgrade == ['example', 'install', 'test']
