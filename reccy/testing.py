from collections.abc import Callable, Iterable

import pytest


def cli_help(
    program: str,
    invoke: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    subcommands: Iterable[str] = (),
) -> str:
    """Return normalized help text for a CLI and its immediate subcommands."""
    monkeypatch.setenv('COLUMNS', '120')
    monkeypatch.setenv('NO_COLOR', '1')

    commands = [['--help'], *([name, '--help'] for name in sorted(subcommands))]
    sections = [_help_section(program, command, invoke, capsys) for command in commands]
    return '\n\n'.join(sections)


def _help_section(
    program: str,
    command: list[str],
    invoke: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
) -> str:
    try:
        result = invoke(command)
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
