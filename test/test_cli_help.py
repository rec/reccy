from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from reccy.pytest_plugin import CliHelp

pytest_plugins = ['reccy.pytest_plugin']


class TestFileRegression:
    contents: str | None = None

    def check(self, contents: str) -> None:
        self.contents = contents


@pytest.fixture
def file_regression() -> TestFileRegression:
    return TestFileRegression()


def test_cli_help_records_top_level_and_sorted_subcommands(
    cli_help: CliHelp,
    file_regression: TestFileRegression,
) -> None:
    calls: list[list[str]] = []

    def invoke() -> int:
        calls.append(sys.argv[1:])
        print(f'help for {" ".join(sys.argv[1:])}    ')
        return 0

    cli_help('app', invoke, subcommands=['zebra', 'apple'])

    assert file_regression.contents == (
        '$ app --help\n'
        'help for --help\n'
        '\n'
        '$ app apple --help\n'
        'help for apple --help\n'
        '\n'
        '$ app zebra --help\n'
        'help for zebra --help'
    )
    assert calls == [['--help'], ['apple', '--help'], ['zebra', '--help']]


def test_cli_help_accepts_system_exit_zero(
    cli_help: CliHelp,
    file_regression: TestFileRegression,
) -> None:
    def invoke() -> int:
        print('help')
        raise SystemExit(0)

    cli_help('app', invoke)

    assert file_regression.contents == '$ app --help\nhelp'


def test_cli_help_rejects_error_output(
    cli_help: CliHelp,
) -> None:
    def invoke() -> int:
        print('help', file=sys.stderr)
        return 0

    with pytest.raises(AssertionError, match='standard error'):
        cli_help('app', invoke)


@pytest.mark.parametrize('exit_kind', ['return', 'exit'])
def test_cli_help_accepts_none_success(
    cli_help: CliHelp, file_regression: TestFileRegression, exit_kind: str
) -> None:
    def invoke() -> None:
        print('help')
        if exit_kind == 'exit':
            raise SystemExit()

    cli_help('app', invoke)
    assert file_regression.contents == '$ app --help\nhelp'


def test_cli_help_isolates_capture_and_normalizes_output(
    cli_help: CliHelp, file_regression: TestFileRegression
) -> None:
    print('unrelated output')
    print('unrelated error', file=sys.stderr)

    def invoke() -> None:
        assert os.environ['COLUMNS'] == '120'
        assert os.environ['NO_COLOR'] == '1'
        print('    • one   \n    � two   ')

    cli_help('app', invoke)
    assert file_regression.contents == '$ app --help\n    - one\n    - two'


@pytest.mark.parametrize('result', [2, 'failed'])
def test_failed_help_drains_capture(
    cli_help: CliHelp, capsys: pytest.CaptureFixture[str], result: int | str
) -> None:
    def invoke() -> None:
        print('partial help')
        print('failure', file=sys.stderr)
        raise SystemExit(result)

    with pytest.raises(AssertionError, match='exited with'):
        cli_help('app', invoke)
    assert capsys.readouterr() == ('', '')
