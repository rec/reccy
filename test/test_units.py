from typing import Annotated

import pytest
import tyro
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from reccy.configuration import units
from reccy.configuration.tyro import unit_spec


@pytest.mark.parametrize('value', ['0:60', '1:00:60'])
def test_clock_seconds_reject_next_minute(value: str) -> None:
    with pytest.raises(ValidationError, match='Invalid seconds'):
        TypeAdapter(units.Seconds).validate_python(value)


@pytest.mark.parametrize(
    ('annotation', 'value', 'expected'),
    [
        (units.Seconds, '10ms', 0.01),
        (units.Seconds, '2 min', 120.0),
        (units.Seconds, '1:30', 90.0),
        (units.Seconds, '0:59', 59.0),
        (units.Seconds, '0:59.5', 59.5),
        (units.Seconds, '1:00:59.5', 3659.5),
        (units.Seconds, '0.5', 0.5),
        (units.Seconds, 0.5, 0.5),
        (units.Milliseconds, '0.0005s', 0.5),
        (units.WholeMilliseconds, '0.029s', 29),
        (units.Hertz, '2.4kHz', 2400.0),
        (units.WholeHertz, '48kHz', 48_000),
        (units.BitsPerSecond, '1.5Mbps', 1_500_000.0),
        (units.WholeBitsPerSecond, '160kbps', 160_000),
        (units.WholeKilobitsPerSecond, '1.2Mbps', 1200),
        (units.FramesPerSecond, '29.5fps', 29.5),
        (units.WholeFramesPerSecond, '30frame/s', 30),
        (units.Decibels, '-60dB', -60.0),
        (units.Pixels, '1kpx', 1000),
        (units.MusicalCents, '100 cents', 100.0),
        (units.Bytes, '2MB', 2_000_000),
        (units.Bytes, '2MiB', 2_097_152),
        (units.Bytes, '1KB', 1000),
        (units.Megabytes, '1GB', 1000),
    ],
)
def test_units_normalize_to_numbers(
    annotation: object, value: object, expected: int | float
) -> None:
    result = TypeAdapter(annotation).validate_python(value)
    assert result == expected
    assert isinstance(result, type(expected))
    if isinstance(value, str):
        assert result.provenance == units.UnitProvenance(
            authored=value,
            normalized=expected,
            canonical_unit={
                units.Seconds: 'second',
                units.Milliseconds: 'millisecond',
                units.WholeMilliseconds: 'millisecond',
                units.Hertz: 'hertz',
                units.WholeHertz: 'hertz',
                units.BitsPerSecond: 'bit_per_second',
                units.WholeBitsPerSecond: 'bit_per_second',
                units.WholeKilobitsPerSecond: 'kilobit_per_second',
                units.FramesPerSecond: 'frame_per_second',
                units.WholeFramesPerSecond: 'frame_per_second',
                units.Decibels: 'decibel',
                units.Pixels: 'pixel',
                units.MusicalCents: 'musical_cent',
                units.Bytes: 'byte',
                units.Megabytes: 'megabyte',
            }[annotation],
        )
    else:
        assert type(result) is type(expected)


@pytest.mark.parametrize(
    ('annotation', 'value'),
    [
        (units.Seconds, '3Hz'),
        (units.Hertz, '3ms'),
        (units.WholeHertz, '440.5Hz'),
        (units.WholeFramesPerSecond, '29.5fps'),
        (units.WholeBitsPerSecond, '1.5bps'),
        (units.WholeKilobitsPerSecond, '1bps'),
        (units.Pixels, '0.5px'),
        (units.Decibels, '3s'),
        (units.MusicalCents, '3Hz'),
        (units.Bytes, '2s'),
        (units.Bytes, '90degree'),
        (units.Bytes, '2radian'),
        (units.Seconds, '3m'),
        (units.WholeMilliseconds, '0.5ms'),
        (units.Bytes, '0.5byte'),
        (units.Megabytes, '1MiB'),
        (units.Seconds, 'junk'),
        (units.Seconds, 'ms'),
        (units.Seconds, '1 s/'),
        (units.Seconds, 'nan'),
        (units.Seconds, float('inf')),
        (units.Hertz, float('nan')),
        (units.Seconds, True),
    ],
)
def test_invalid_or_inexact_units_are_rejected(
    annotation: object, value: object
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(annotation).validate_python(value)


def test_unit_spec_preserves_authored_value() -> None:
    spec = unit_spec(units.Seconds, 'TIME')
    result = spec.instance_from_str(['250ms'])
    assert result == 0.25
    assert spec.str_from_instance(result) == ['250ms']
    assert spec.str_from_instance(0.25) == ['0.25']


class CliConfig(BaseModel, frozen=True):
    interval: Annotated[units.Seconds, unit_spec(units.Seconds, 'SECONDS')]


def test_tyro_parses_unit_spec() -> None:
    value = tyro.cli(CliConfig, args=['--interval', '250ms'])
    assert value.interval == 0.25
    assert value.interval.provenance.authored == '250ms'


class Nested(BaseModel, frozen=True):
    delay: units.Seconds


class Config(BaseModel, frozen=True):
    interval: units.Milliseconds

    nested: Nested
    history: list[units.Seconds]


def test_unit_provenance_is_collected_by_field_path() -> None:
    value = Config(interval='250ms', nested=Nested(delay='1 min'), history=['1s', 2.0])
    assert units.collect_unit_provenance(value) == {
        '/interval': units.UnitProvenance(
            authored='250ms', normalized=250.0, canonical_unit='millisecond'
        ),
        '/nested/delay': units.UnitProvenance(
            authored='1 min', normalized=60.0, canonical_unit='second'
        ),
        '/history/0': units.UnitProvenance(
            authored='1s', normalized=1.0, canonical_unit='second'
        ),
    }


def test_unit_dumps_separate_runtime_and_authored_values() -> None:
    value = Config(interval='250ms', nested=Nested(delay='1 min'), history=['1s', 2.0])
    assert units.runtime_dump(value) == {
        'interval': 250.0,
        'nested': {'delay': 60.0},
        'history': [1.0, 2.0],
    }
    authored = {
        'interval': '250ms',
        'nested': {'delay': '1 min'},
        'history': ['1s', 2.0],
    }
    assert units.authored_dump(value) == authored
    assert units.revalidation_dump(value) == authored


def test_revalidation_preserves_provenance() -> None:
    value = Config(interval='250ms', nested=Nested(delay='1 min'), history=['1s', 2.0])
    rebuilt = Config.model_validate(units.revalidation_dump(value))
    assert units.collect_unit_provenance(rebuilt) == units.collect_unit_provenance(
        value
    )


def test_copy_preserves_and_arithmetic_drops_provenance() -> None:
    value = TypeAdapter(units.Seconds).validate_python('250ms')
    copied = TypeAdapter(units.Seconds).validate_python(value)
    assert copied.provenance == value.provenance
    assert type(value + 1) is float


@pytest.mark.parametrize(
    ('unit', 'authored', 'expected'),
    [
        ('second', '1/3 ms', '1/3000'),
        ('second', '0:00.1', '1/10'),
        ('hertz', '2.4kHz', '2400'),
        ('beat', '1/3 beat', '1/3'),
        ('beats_per_minute', '2 beat/s', '120'),
        ('tick', '9007199254740993 tick', '9007199254740993'),
        ('musical_cent', '1 semitone', '100'),
    ],
)
def test_exact_units_preserve_rational_magnitudes(
    unit: str, authored: str, expected: str
) -> None:
    from fractions import Fraction

    assert units.magnitude(authored, unit, exact=True) == Fraction(expected)


def test_unit_validator_keeps_strict_integer_constraints() -> None:
    annotation = Annotated[int, Field(strict=True), units.unit_validator('frame')]
    adapter = TypeAdapter(annotation)
    assert adapter.validate_python('2 frame') == 2
    for v in ['1.5 frame', 2.0, True]:
        with pytest.raises(ValidationError):
            adapter.validate_python(v)
