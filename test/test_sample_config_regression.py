import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

if TYPE_CHECKING:
    from reccy.pytest_plugin import SampleConfigRegression

pytest_plugins = ['reccy.pytest_plugin']


class Settings(BaseModel, frozen=True):
    count: int = 3


@pytest.mark.parametrize('marker', ['.git', 'doc'])
def test_default_reference_uses_nearest_project_directory(
    sample_config_regression: 'SampleConfigRegression',
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    root = tmp_path / 'project'
    if marker == '.git':
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
    else:
        (root / 'doc').mkdir(parents=True)
    test_path = root / 'test' / 'nested' / 'test_settings.py'
    test_path.parent.mkdir(parents=True)
    monkeypatch.setattr(request.node, 'path', test_path)

    with pytest.raises(pytest.fail.Exception):
        sample_config_regression(Settings)

    reference = root / 'doc' / 'sample-config.toml'
    assert reference.read_text() == 'count = 3\n'
    assert sample_config_regression(Settings) == reference.read_text()
    assert list((root / 'test').rglob('*.toml')) == []


def test_nearer_doc_directory_takes_precedence(
    sample_config_regression: 'SampleConfigRegression',
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(['git', 'init', '--quiet', str(tmp_path)], check=True)
    project = tmp_path / 'nested'
    (project / 'doc').mkdir(parents=True)
    monkeypatch.setattr(request.node, 'path', project / 'test' / 'test_settings.py')

    with pytest.raises(pytest.fail.Exception):
        sample_config_regression(Settings)

    assert (project / 'doc' / 'sample-config.toml').read_text() == 'count = 3\n'
    assert not (tmp_path / 'doc').exists()


def test_explicit_reference_path_preserves_mismatched_content(
    sample_config_regression: 'SampleConfigRegression', tmp_path: Path
) -> None:
    path = tmp_path / 'examples' / 'config.toml'
    with pytest.raises(pytest.fail.Exception):
        sample_config_regression(Settings, path=path)
    assert path.read_text() == 'count = 3\n'
    assert sample_config_regression(Settings, path=path) == path.read_text()

    path.write_text('count = 5\n')
    with pytest.raises(AssertionError):
        sample_config_regression(Settings, path=path)
    assert path.read_text() == 'count = 5\n'


def test_default_reference_requires_a_project_marker(
    sample_config_regression: 'SampleConfigRegression',
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(request.node, 'path', Path('test') / 'test_settings.py')
    with pytest.raises(ValueError, match=r'No \.git/ or doc/ directory'):
        sample_config_regression(Settings)
