import re
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
    if value <= 0:
        raise ValueError('value must be positive')
    return value


def non_negative_number(value: float) -> float:
    if value < 0:
        raise ValueError('value must not be negative')
    return value


def sorted_values(values: list[_T]) -> list[_T]:
    previous = None
    for value in values:
        if previous is not None and value < previous:
            raise ValueError('values must be sorted')
        previous = value
    return values
