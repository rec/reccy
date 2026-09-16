"""Run in a consumer environment with pytest-regressions installed."""

from pathlib import Path
from shutil import copyfile
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from reccy.pytest_plugin import CliHelp, FileRegression

pytest_plugins = ['reccy.pytest_plugin']


@pytest.fixture
def file_regression(tmp_path: Path, request: pytest.FixtureRequest) -> 'FileRegression':
    module = pytest.importorskip('pytest_regressions.file_regression')
    return module.FileRegressionFixture(tmp_path, tmp_path / 'baseline', request)


def test_real_plugin_creates_baseline_and_detects_changes(
    cli_help: 'CliHelp', tmp_path: Path
) -> None:
    help_text = 'original help'

    def invoke() -> None:
        print(help_text)

    with pytest.raises(pytest.fail.Exception):
        cli_help('consumer', invoke)
    baseline = (
        tmp_path / 'baseline/test_real_plugin_creates_baseline_and_detects_changes.txt'
    )
    assert baseline.read_text() == '$ consumer --help\noriginal help'
    # A subsequent consumer run copies the stored baseline into its data directory.
    copyfile(baseline, tmp_path / baseline.name)
    cli_help('consumer', invoke)
    help_text = 'changed help'
    with pytest.raises(AssertionError):
        cli_help('consumer', invoke)
    assert baseline.read_text() == '$ consumer --help\noriginal help'
