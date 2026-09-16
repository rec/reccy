import sys

import pytest

from reccy import testing


def test_cli_help_records_top_level_and_sorted_subcommands(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def invoke(arguments: list[str]) -> int:
        calls.append(arguments)
        print(f'help for {" ".join(arguments)}    ')
        return 0

    assert testing.cli_help(
        'app', invoke, capsys, monkeypatch, subcommands=['zebra', 'apple']
    ) == (
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
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invoke(arguments: list[str]) -> int:
        print('help')
        raise SystemExit(0)

    assert testing.cli_help('app', invoke, capsys, monkeypatch) == '$ app --help\nhelp'


def test_cli_help_rejects_error_output(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invoke(arguments: list[str]) -> int:
        print('help', file=sys.stderr)
        return 0

    with pytest.raises(AssertionError, match='standard error'):
        testing.cli_help('app', invoke, capsys, monkeypatch)
