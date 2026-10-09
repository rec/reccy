import re
from math import isfinite
from typing import Protocol, TypeVar


class _Comparable(Protocol):
    def __lt__(self, other: object) -> bool: ...


_T = TypeVar('_T', bound=_Comparable)

_IDENTIFIER = re.compile(r'^[a-z][a-z0-9_-]*$')
_ENV_VAR = re.compile(r'^[A-Z][A-Z0-9_]*$')


def non_empty_string(value: str) -> str:
    if not value:
        raise ValueError('value must not be empty')
    return value


def identifier(value: str) -> str:
    non_empty_string(value)
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            'value must use lowercase letters, numbers, hyphens, or underscores'
        )
    return value


def environment_variable(value: str) -> str:
    non_empty_string(value)
    if not _ENV_VAR.fullmatch(value):
        raise ValueError(
            'environment variable must use uppercase letters, numbers, or underscores'
        )
    return value


def positive_number(value: float) -> float:
    """Require a positive value; positive infinity is permitted, NaN is not."""
    if not value > 0:
        raise ValueError('value must be positive')
    return value


def non_negative_number(value: float) -> float:
    """Require a nonnegative value; positive infinity is permitted, NaN is not."""
    if not value >= 0:
        raise ValueError('value must not be negative')
    return value


def sorted_values(values: list[_T]) -> list[_T]:
    previous = None
    for value in values:
        if previous is not None and value < previous:
            raise ValueError('values must be sorted')
        previous = value
    return values


def validate_json(value: object, *, label: str = 'value', strict: bool = True) -> None:
    """Require finite JSON data bounded to 10000 values and 64 nesting levels.

    strict requires exact built-in types. Otherwise string, boolean, list, and
    dictionary subclasses are accepted, while numeric types remain exact.
    """
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 10000 or depth > 64:
            raise ValueError(f'{label} exceeds 10000 values or 64 levels')
        if item is None or type(item) in {bool, int, str}:
            continue
        if not strict and isinstance(item, bool | str):
            continue
        if type(item) is float:
            if not isfinite(item):
                raise ValueError(f'{label} numbers must be finite')
            continue
        if type(item) is list or (not strict and isinstance(item, list)):
            pending.extend((child, depth + 1) for child in item)
            continue
        if type(item) is dict or (not strict and isinstance(item, dict)):
            if any(type(key) is not str for key in item):
                raise ValueError(f'{label} object keys must be strings')
            pending.extend((child, depth + 1) for child in item.values())
            continue
        raise ValueError(f'{label} must contain only JSON values')
