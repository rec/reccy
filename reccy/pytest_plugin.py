import sys
from collections.abc import Callable, Iterable
from typing import Protocol

import pytest


class CliHelp(Protocol):
    def __call__(
        self,
        program: str,
        invoke: Callable[[], int],
        subcommands: Iterable[str] = (),
    ) -> None: ...


class FileRegression(Protocol):
    def check(self, contents: str) -> None: ...


@pytest.fixture
def cli_help(
    capsys: pytest.CaptureFixture[str],
    file_regression: FileRegression,
    monkeypatch: pytest.MonkeyPatch,
) -> CliHelp:
    def check(
        program: str,
        invoke: Callable[[], int],
        subcommands: Iterable[str] = (),
    ) -> None:
        file_regression.check(
            _help_text(program, invoke, capsys, monkeypatch, subcommands)
        )

    return check


def _help_text(
    program: str,
    invoke: Callable[[], int],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    subcommands: Iterable[str],
) -> str:
    monkeypatch.setenv('COLUMNS', '120')
    monkeypatch.setenv('NO_COLOR', '1')

    commands = [['--help'], *([name, '--help'] for name in sorted(subcommands))]
    sections = [
        _help_section(program, command, invoke, capsys, monkeypatch)
        for command in commands
    ]
    return '\n\n'.join(sections)


def _help_section(
    program: str,
    command: list[str],
    invoke: Callable[[], int],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    monkeypatch.setattr(sys, 'argv', [program, *command])
    try:
        result = invoke()
    except SystemExit as e:
        result = e.code

    if result != 0:
        raise AssertionError(f'{" ".join([program, *command])} exited with {result!r}')

    captured = capsys.readouterr()
    if captured.err:
        raise AssertionError(
            f'{" ".join([program, *command])} wrote help to standard error:\n'
            f'{captured.err}'
        )
    return f'$ {" ".join([program, *command])}\n{_normalize_help(captured.out)}'


def _normalize_help(text: str) -> str:
    return '\n'.join(
        line.replace('    • ', '    - ').replace('    � ', '    - ').rstrip()
        for line in text.splitlines()
    )
