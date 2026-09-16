"""Validated, non-mutating edits to model fields."""

from collections.abc import Sequence
from copy import deepcopy
from typing import cast

from pydantic import BaseModel

from .units import revalidation_dump


def validated_update[Model: BaseModel](
    model: Model, path: Sequence[str], value: object
) -> Model:
    """Edit a nonempty sequence of canonical field names and revalidate the model.

    Only nested BaseModel fields are traversed, not dictionaries or lists.
    Aliases are not path components. Application mutability policy stays external.
    """
    if isinstance(path, str) or not path:
        raise ValueError('path must be a nonempty sequence of field names')
    data = deepcopy(revalidation_dump(model))
    current: BaseModel = model
    section = data
    for index, name in enumerate(path):
        if not isinstance(name, str) or name not in type(current).model_fields:
            raise ValueError(f'Unknown configuration field: {name!r}')
        if name not in section:
            raise ValueError(f'Configuration field is excluded from the dump: {name}')
        if index == len(path) - 1:
            section[name] = deepcopy(value)
            break
        child = getattr(current, name)
        child_data = section[name]
        if not isinstance(child, BaseModel) or not isinstance(child_data, dict):
            raise ValueError(f'Cannot traverse non-model field: {name}')
        current = child
        section = cast(dict[str, object], child_data)
    return type(model).model_validate(data, by_alias=False, by_name=True)
