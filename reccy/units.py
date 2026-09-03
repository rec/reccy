"""Parse configuration units once; validated fields contain ordinary numbers."""

import re
from decimal import Decimal
from functools import cache, partial
from importlib.resources import files
from typing import Annotated

from pint import UnitRegistry
from pint.errors import PintError
from pydantic import BeforeValidator, Field


def magnitude(value: object, unit: str) -> object:
    if isinstance(value, bool):
        raise ValueError('A quantity cannot be a boolean')
    if not isinstance(value, str):
        return value
    if unit == 'second' and ':' in value:
        return _clock_seconds(value)
    match = QUANTITY.fullmatch(value.strip())
    if match is None:
        raise ValueError(f'Expected a number optionally followed by a {unit} unit')
    number, supplied_unit = match.groups()
    if not supplied_unit:
        return Decimal(number)
    try:
        return _registry().Quantity(Decimal(number), supplied_unit).to(unit).magnitude
    except PintError as error:
        raise ValueError(f'Expected {unit}: {error}') from None


@cache
def _registry() -> UnitRegistry:
    # Define information before Pint caches its dimensionless default.
    registry = UnitRegistry(None, non_int_type=Decimal, on_redefinition='ignore')
    registry.load_definitions(str(files('pint').joinpath('default_en.txt')))
    registry.define('bit = [information]')
    registry.define('kilobyte = 1000 * byte = kB = KB')
    return registry


def _clock_seconds(value: str) -> float:
    parts = value.split(':')
    if not 1 <= len(parts) <= 3:
        raise ValueError('A time can only have three parts')
    seconds = float(parts.pop())
    if seconds < 0 or parts and seconds > 59:
        raise ValueError('Invalid seconds in time')
    minutes = int(parts.pop()) if parts else 0
    if minutes < 0 or parts and minutes > 59:
        raise ValueError('Invalid minutes in time')
    hours = int(parts.pop()) if parts else 0
    if hours < 0:
        raise ValueError('Invalid hours in time')
    return seconds + 60 * minutes + 3600 * hours


QUANTITY = re.compile(
    r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'
    r'\s*([A-Za-z\u00b5\u03bc]+)?'
)

Seconds = Annotated[
    float,
    Field(allow_inf_nan=False),
    BeforeValidator(partial(magnitude, unit='second')),
]

Milliseconds = Annotated[
    float,
    Field(allow_inf_nan=False),
    BeforeValidator(partial(magnitude, unit='millisecond')),
]

WholeMilliseconds = Annotated[
    int, BeforeValidator(partial(magnitude, unit='millisecond'))
]

Hertz = Annotated[
    float, Field(allow_inf_nan=False), BeforeValidator(partial(magnitude, unit='hertz'))
]

Bytes = Annotated[int, BeforeValidator(partial(magnitude, unit='byte'))]
Megabytes = Annotated[int, BeforeValidator(partial(magnitude, unit='megabyte'))]
