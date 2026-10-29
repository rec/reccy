from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TypeVar, cast

import tomlkit
from pydantic import BaseModel

from ..errors import ReccyError
from ..runtime.files import atomic_output

Settings = TypeVar('Settings', bound=BaseModel)


def load(path: Path, model: type[Settings]) -> Settings:
    try:
        return model.model_validate(read(path))
    except Exception as error:
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


def save(path: Path, value: BaseModel, *, write_empty_dicts: bool = False) -> None:
    """Save a model, omitting empty dictionary entries unless requested."""
    try:
        match path.suffix:
            case '.json':
                write_json_model(
                    path, value, indent=2, write_empty_dicts=write_empty_dicts
                )
            case '.toml':
                data = value.model_dump(mode='json')
                if not write_empty_dicts:
                    data = cast(dict[str, object], _without_empty_dicts(data))
                write_text_atomically(path, tomlkit.dumps(data))
            case _:
                raise ValueError(f'Settings path must end in .json or .toml: {path}')
    except Exception as error:
        raise ReccyError(f'Could not save settings to {path}: {error}') from error


def write_json_model(
    path: Path,
    value: BaseModel,
    *,
    indent: int | None = None,
    sync: bool = True,
    write_empty_dicts: bool = False,
) -> None:
    """Write JSON, optionally retaining empty dictionary entries."""
    content = value.model_dump_json(indent=indent)
    if not write_empty_dicts:
        content = json.dumps(
            _without_empty_dicts(json.loads(content)),
            indent=indent,
            ensure_ascii=False,
            separators=(',', ':') if indent is None else None,
        )
    write_text_atomically(path, content + '\n', sync=sync)


def write_text_atomically(path: Path, content: str, *, sync: bool = True) -> None:
    """Publish one complete write; concurrent writers use last-replacement-wins."""
    with atomic_output(path, sync=sync) as temporary:
        temporary.write_text(content)


def _without_empty_dicts(value: object) -> object:
    if isinstance(value, dict):
        return {k: r for k, v in value.items() if (r := _without_empty_dicts(v)) != {}}
    if isinstance(value, list):
        return [_without_empty_dicts(v) for v in value]
    return value
