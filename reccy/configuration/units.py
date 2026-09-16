"""Parse configuration units into numbers with optional authored provenance."""

import re
from decimal import Decimal
from functools import cache, partial
from importlib.resources import files
from typing import Annotated, Literal, cast, get_args

from pint import UnitRegistry
from pint.errors import PintError
from pydantic import BaseModel, Field, ValidatorFunctionWrapHandler, WrapValidator
from pydantic.functional_serializers import PlainSerializer, WrapSerializer


class UnitProvenance(BaseModel, frozen=True):
    authored: str
    normalized: int | float
    canonical_unit: str


def collect_unit_provenance(value: object) -> dict[str, UnitProvenance]:
    result: dict[str, UnitProvenance] = {}
    _collect_unit_provenance(value, '', result)
    return result


def runtime_dump(
    value: BaseModel, *, mode: Literal['json', 'python'] = 'python'
) -> dict[str, object]:
    _validate_dump_source(value)
    dumped = value.model_dump(mode=mode, by_alias=False)
    result = _replace_unit_values(value, dumped, authored=False)
    assert isinstance(result, dict)
    return cast(dict[str, object], result)


def authored_dump(
    value: BaseModel, *, mode: Literal['json', 'python'] = 'python'
) -> dict[str, object]:
    _validate_dump_source(value)
    dumped = value.model_dump(mode=mode, by_alias=False)
    result = _replace_unit_values(value, dumped, authored=True)
    assert isinstance(result, dict)
    return cast(dict[str, object], result)


def revalidation_dump(value: BaseModel) -> dict[str, object]:
    """Return authored Python values for validation, preserving unit provenance."""
    return authored_dump(value)


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


def _unit_value(
    value: object, handler: ValidatorFunctionWrapHandler, unit: str
) -> object:
    if isinstance(value, bool):
        raise ValueError('A quantity cannot be a boolean')
    if isinstance(value, (_UnitFloat, _UnitInt)):
        value = value.provenance.authored
    if not isinstance(value, str):
        return handler(value)
    normalized = handler(magnitude(value, unit))
    provenance = UnitProvenance(
        authored=value, normalized=normalized, canonical_unit=unit
    )
    if isinstance(normalized, int):
        return _UnitInt(normalized, provenance)
    return _UnitFloat(normalized, provenance)


@cache
def _registry() -> UnitRegistry:
    # Define information before Pint caches its dimensionless default.
    registry = UnitRegistry(None, non_int_type=Decimal, on_redefinition='ignore')
    registry.load_definitions(str(files('pint').joinpath('default_en.txt')))
    registry.define('bit = [information]')
    registry.define('kilobyte = 1000 * byte = kB = KB')
    registry.define('musical_cent = octave / 1200 = cent = cents')
    return registry


def _clock_seconds(value: str) -> float:
    parts = value.split(':')
    if not 1 <= len(parts) <= 3:
        raise ValueError('A time can only have three parts')
    seconds = float(parts.pop())
    if seconds < 0 or parts and seconds >= 60:
        raise ValueError('Invalid seconds in time')
    minutes = int(parts.pop()) if parts else 0
    if minutes < 0 or parts and minutes > 59:
        raise ValueError('Invalid minutes in time')
    hours = int(parts.pop()) if parts else 0
    if hours < 0:
        raise ValueError('Invalid hours in time')
    return seconds + 60 * minutes + 3600 * hours


def _collect_unit_provenance(
    value: object, path: str, result: dict[str, UnitProvenance]
) -> None:
    if isinstance(value, (_UnitFloat, _UnitInt)):
        result[path] = value.provenance
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _collect_unit_provenance(
                getattr(value, name), _child_path(path, name), result
            )
    elif isinstance(value, dict):
        for key, item in value.items():
            _collect_unit_provenance(item, _child_path(path, str(key)), result)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_unit_provenance(item, _child_path(path, str(index)), result)


def _replace_unit_values(source: object, dumped: object, *, authored: bool) -> object:
    if isinstance(source, (_UnitFloat, _UnitInt)):
        if authored:
            return source.provenance.authored
        return source.provenance.normalized
    if isinstance(source, BaseModel) and isinstance(dumped, dict):
        return {
            key: _replace_unit_values(getattr(source, key), item, authored=authored)
            if isinstance(key, str) and key in type(source).model_fields
            else item
            for key, item in dumped.items()
        }
    if isinstance(source, dict) and isinstance(dumped, dict):
        result = dict(dumped)
        for key, item in source.items():
            if key in result:
                result[key] = _replace_unit_values(item, result[key], authored=authored)
        return result
    if isinstance(source, list) and isinstance(dumped, list):
        return [
            _replace_unit_values(item, dumped[index], authored=authored)
            for index, item in enumerate(source)
        ]
    return dumped


def _validate_dump_source(value: object) -> None:
    if isinstance(value, BaseModel):
        model = type(value)
        decorators = model.__pydantic_decorators__
        if model.__pydantic_root_model__:
            raise TypeError('Unit-aware dumps do not support RootModel')
        if decorators.field_serializers or decorators.model_serializers:
            raise TypeError('Unit-aware dumps do not support custom serializers')
        for name, field in model.model_fields.items():
            _validate_dump_annotation(field.annotation)
            for metadata in field.metadata:
                _validate_dump_annotation(metadata)
            _validate_dump_source(getattr(value, name))
    elif isinstance(value, dict):
        for item in value.values():
            _validate_dump_source(item)
    elif isinstance(value, list):
        for item in value:
            _validate_dump_source(item)


def _validate_dump_annotation(annotation: object) -> None:
    if isinstance(annotation, (PlainSerializer, WrapSerializer)):
        raise TypeError('Unit-aware dumps do not support custom serializers')
    for argument in get_args(annotation):
        _validate_dump_annotation(argument)


def _child_path(parent: str, child: str) -> str:
    return f'{parent}.{child}' if parent else child


class _UnitFloat(float):
    provenance: UnitProvenance

    def __new__(cls, value: float, provenance: UnitProvenance) -> '_UnitFloat':
        result = super().__new__(cls, value)
        result.provenance = provenance
        return result


class _UnitInt(int):
    provenance: UnitProvenance

    def __new__(cls, value: int, provenance: UnitProvenance) -> '_UnitInt':
        result = super().__new__(cls, value)
        result.provenance = provenance
        return result


QUANTITY = re.compile(
    r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'
    r'\s*([A-Za-z\u00b5\u03bc]+)?'
)

Seconds = Annotated[
    float,
    Field(allow_inf_nan=False),
    WrapValidator(partial(_unit_value, unit='second')),
]

Milliseconds = Annotated[
    float,
    Field(allow_inf_nan=False),
    WrapValidator(partial(_unit_value, unit='millisecond')),
]

WholeMilliseconds = Annotated[
    int, WrapValidator(partial(_unit_value, unit='millisecond'))
]

Hertz = Annotated[
    float, Field(allow_inf_nan=False), WrapValidator(partial(_unit_value, unit='hertz'))
]

WholeHertz = Annotated[int, WrapValidator(partial(_unit_value, unit='hertz'))]

MusicalCents = Annotated[
    float,
    Field(allow_inf_nan=False),
    WrapValidator(partial(_unit_value, unit='musical_cent')),
]

Bytes = Annotated[int, WrapValidator(partial(_unit_value, unit='byte'))]
Megabytes = Annotated[int, WrapValidator(partial(_unit_value, unit='megabyte'))]
