from __future__ import annotations

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
