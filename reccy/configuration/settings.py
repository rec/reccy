from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..errors import ReccyError
from ..runtime.files import atomic_output

Settings = TypeVar('Settings', bound=BaseModel)


def load(path: Path, model: type[Settings]) -> Settings | None:
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text())
    except (OSError, ValidationError) as error:
        raise ReccyError(f'Could not load settings from {path}: {error}') from error


def save(path: Path, value: BaseModel) -> None:
    try:
        write_json_model(path, value, indent=2)
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
