import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

import pytest
from pydantic import BaseModel

from .configuration.sample_config import write_sample_config


class CliHelp(Protocol):
    def __call__(
        self,
        program: str,
        invoke: Callable[[], int | None],
        subcommands: Iterable[str] = (),
    ) -> None: ...


class FileRegression(Protocol):
    def check(
        self, contents: str, *, extension: str = '.txt', fullpath: Path | None = None
    ) -> None: ...


class SampleConfigRegression(Protocol):
    def __call__(self, model: type[BaseModel], *, path: Path | None = None) -> str: ...


@pytest.fixture
def cli_help(
    capsys: pytest.CaptureFixture[str],
    file_regression: FileRegression,
    monkeypatch: pytest.MonkeyPatch,
) -> CliHelp:
    def check(
        program: str,
        invoke: Callable[[], int | None],
        subcommands: Iterable[str] = (),
    ) -> None:
        file_regression.check(
            _help_text(program, invoke, capsys, monkeypatch, subcommands)
        )

    return check


@pytest.fixture
def sample_config_regression(
    tmp_path: Path, file_regression: FileRegression, request: pytest.FixtureRequest
) -> SampleConfigRegression:
    """Compare sample TOML at path, defaulting to the nearest project doc/ directory."""

    def check(model: type[BaseModel], *, path: Path | None = None) -> str:
        if path is None:
            for parent in request.path.parents:
                if (parent / '.git').is_dir() or (parent / 'doc').is_dir():
                    path = parent / 'doc' / 'sample-config.toml'
                    break
            else:
                raise ValueError(f'No .git/ or doc/ directory above {request.path}')
        generated = tmp_path / 'sample.toml'
        write_sample_config(model, generated)
        text = generated.read_text()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_regression.check(text, fullpath=path)
        return text

    return check


def _help_text(
    program: str,
    invoke: Callable[[], int | None],
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
    invoke: Callable[[], int | None],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    monkeypatch.setattr(sys, 'argv', [program, *command])
    capsys.readouterr()
    try:
        try:
            result = invoke()
        except SystemExit as e:
            result = e.code
    finally:
        captured = capsys.readouterr()

    if result not in (0, None):
        raise AssertionError(f'{" ".join([program, *command])} exited with {result!r}')

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
