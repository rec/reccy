from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TypeVar

import tomlkit
from pydantic import BaseModel, ValidationError

from ..errors import ReccyError
from ..runtime.files import atomic_output

Settings = TypeVar('Settings', bound=BaseModel)


def load(path: Path, model: type[Settings]) -> Settings | None:
    try:
        return model.model_validate(read(path))
    except FileNotFoundError:
        return None
    except (
        OSError,
        ValidationError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
    ) as error:
        raise ReccyError(f'Could not load settings from {path}: {error}') from error


def read(path: Path) -> object:
    """Read JSON or TOML data; reject unsupported or missing suffixes."""
    match path.suffix:
        case '.json':
            return json.loads(path.read_text())
        case '.toml':
            with path.open('rb') as file:
                return tomllib.load(file)
        case _:
            raise ValueError(f'Settings path must end in .json or .toml: {path}')


def save(path: Path, value: BaseModel) -> None:
    try:
        match path.suffix:
            case '.json':
                write_json_model(path, value, indent=2)
            case '.toml':
                write_text_atomically(
                    path, tomlkit.dumps(value.model_dump(mode='json'))
                )
            case _:
                raise ValueError(f'Settings path must end in .json or .toml: {path}')
    except OSError as error:
        raise ReccyError(f'Could not save settings to {path}: {error}') from error


def write_json_model(
    path: Path, value: BaseModel, *, indent: int | None = None, sync: bool = True
) -> None:
    write_text_atomically(path, value.model_dump_json(indent=indent) + '\n', sync=sync)


def write_text_atomically(path: Path, content: str, *, sync: bool = True) -> None:
    """Publish one complete write; concurrent writers use last-replacement-wins."""
    with atomic_output(path, sync=sync) as temporary:
        temporary.write_text(content)
