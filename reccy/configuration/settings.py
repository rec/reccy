from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..errors import ReccyError

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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp')
    with temporary.open('w') as file:
        file.write(value.model_dump_json(indent=indent) + '\n')
        file.flush()
        if sync:
            os.fsync(file.fileno())
    temporary.replace(path)
