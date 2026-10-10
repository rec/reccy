"""Parse configuration units into numbers with optional authored provenance."""

from decimal import Decimal
from fractions import Fraction
from functools import cache, partial
from importlib.resources import files
from tokenize import TokenError
from typing import Annotated, Literal, cast, get_args

from pint import Quantity, UnitRegistry
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


def unit_validator(unit: str, *, exact: bool = False) -> WrapValidator:
    """Parse authored units before validating canonical numeric constraints.

    exact preserves rational magnitudes for Fraction fields. Integer fields
    accept only integral conversions, including when strict=True.
    """
    return WrapValidator(
        partial(_unit_value, unit=unit, exact=exact),
        json_schema_input_type=int | float | str,
    )


def magnitude(value: object, unit: str, *, exact: bool = False) -> object:
    if isinstance(value, bool):
        raise ValueError('A quantity cannot be a boolean')
    if not isinstance(value, str):
        return value
    if unit == 'second' and ':' in value:
        return _clock_seconds(value, exact=exact)
    try:
        return Fraction(value)
    except ZeroDivisionError:
        raise ValueError('Invalid quantity number') from None
    except ValueError:
        pass
    try:
        result = _quantity(value).to(unit).magnitude
    except PintError as error:
        raise ValueError(f'Expected {unit}: {error}') from None
    if exact and not isinstance(result, (int, Fraction)):
        raise ValueError('Quantity conversion must retain an exact rational magnitude')
    return result


def quantity_unit(value: str) -> str | None:
    """Return Pint's base unit expression, or None for a dimensionless value."""
    parsed = _quantity(value).to_base_units()
    return str(parsed.units) if parsed.units != _registry().dimensionless else None


def _unit_value(
    value: object, handler: ValidatorFunctionWrapHandler, unit: str, exact: bool = False
) -> object:
    if isinstance(value, bool):
        raise ValueError('A quantity cannot be a boolean')
    if isinstance(value, (_UnitFloat, _UnitInt)):
        value = value.provenance.authored
    if not isinstance(value, str):
        return handler(value)
    converted = magnitude(value, unit, exact=exact)
    if isinstance(converted, (Decimal, Fraction)) and converted == int(converted):
        converted = int(converted)
    normalized = handler(converted)
    if isinstance(normalized, Fraction):
        return normalized
    provenance = UnitProvenance(
        authored=value, normalized=normalized, canonical_unit=unit
    )
    if isinstance(normalized, int):
        return _UnitInt(normalized, provenance)
    return _UnitFloat(normalized, provenance)


def _quantity(value: str) -> Quantity:
    if not value.strip():
        raise ValueError('Quantity must not be empty')
    try:
        return _registry().parse_expression(value)
    except (PintError, ValueError, ZeroDivisionError, SyntaxError, TokenError) as error:
        raise ValueError(f'Invalid quantity: {error}') from None


@cache
def _registry() -> UnitRegistry:
    registry = UnitRegistry(None, non_int_type=Fraction)
    # These are authored coordinates, not implicit angle, power, or pitch ratios.
    # Replace definitions before loading so Pint's caches see only one contract.
    definitions = (
        files('pint')
        .joinpath('default_en.txt')
        .read_text()
        .replace(
            '@import constants_en.txt',
            files('pint').joinpath('constants_en.txt').read_text(),
        )
        .splitlines()
    )
    registry.load_definitions(
        [UNIT_DEFINITIONS.get(x.split('=', 1)[0].strip(), x) for x in definitions]
    )
    registry.define('bit_per_second = bit / second = bps')
    registry.define('frame = [frame]')
    registry.define('tick = [tick]')
    registry.define('beat = [musical_beat]')
    registry.define('beats_per_minute = beat / minute = bpm')
    registry.define('musical_cent = [pitch_interval] = cent = cents')
    registry.define('semitone = 100 * musical_cent')
    registry.define('frame_per_second = frame / second = fps')
    registry.define('pixel = [pixel] = px')
    registry.define('kilobyte = 1000 * byte = kB = KB')
    return registry


def _clock_seconds(value: str, *, exact: bool = False) -> float | Fraction:
    parts = value.split(':')
    if not 1 <= len(parts) <= 3:
        raise ValueError('A time can only have three parts')
    seconds = Fraction(parts.pop()) if exact else float(parts.pop())
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
            if not isinstance(key, str):
                raise TypeError('Unit provenance requires string dictionary keys')
            _collect_unit_provenance(item, _child_path(path, key), result)
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
    child = child.replace('~', '~0').replace('/', '~1')
    return f'{parent}/{child}'


class _UnitFloat(float):
    provenance: UnitProvenance

    def __new__(cls, value: float, provenance: UnitProvenance) -> '_UnitFloat':
        result = super().__new__(cls, value)
        result.provenance = provenance
        return result

    def __getnewargs_ex__(
        self,
    ) -> tuple[tuple[float, UnitProvenance], dict[str, object]]:
        return (float(self), self.provenance), {}


class _UnitInt(int):
    provenance: UnitProvenance

    def __new__(cls, value: int, provenance: UnitProvenance) -> '_UnitInt':
        result = super().__new__(cls, value)
        result.provenance = provenance
        return result

    def __getnewargs_ex__(self) -> tuple[tuple[int, UnitProvenance], dict[str, object]]:
        return (int(self), self.provenance), {}


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

BitsPerSecond = Annotated[
    float,
    Field(allow_inf_nan=False),
    WrapValidator(partial(_unit_value, unit='bit_per_second')),
]

WholeBitsPerSecond = Annotated[
    int, WrapValidator(partial(_unit_value, unit='bit_per_second'))
]

WholeKilobitsPerSecond = Annotated[
    int, WrapValidator(partial(_unit_value, unit='kilobit_per_second'))
]

FramesPerSecond = Annotated[
    float,
    Field(allow_inf_nan=False),
    WrapValidator(partial(_unit_value, unit='frame_per_second')),
]

WholeFramesPerSecond = Annotated[
    int, WrapValidator(partial(_unit_value, unit='frame_per_second'))
]

Decibels = Annotated[
    float,
    Field(allow_inf_nan=False),
    WrapValidator(partial(_unit_value, unit='decibel')),
]

Pixels = Annotated[int, WrapValidator(partial(_unit_value, unit='pixel'))]

MusicalCents = Annotated[
    float,
    Field(allow_inf_nan=False),
    WrapValidator(partial(_unit_value, unit='musical_cent')),
]

Bytes = Annotated[int, WrapValidator(partial(_unit_value, unit='byte'))]
Megabytes = Annotated[int, WrapValidator(partial(_unit_value, unit='megabyte'))]

UNIT_DEFINITIONS = {
    'bit': 'bit = [information]',
    'radian': 'radian = [angle] = rad',
    'decibel': 'decibel = [log_gain] = dB',
    'octave': 'octave = 1200 * musical_cent = oct',
}
