import pytest
from pydantic import TypeAdapter, ValidationError

from reccy import config, units


@pytest.mark.parametrize(
    ('annotation', 'value', 'expected'),
    [
        (units.Seconds, '10ms', 0.01),
        (units.Seconds, '2 min', 120.0),
        (units.Seconds, '1:30', 90.0),
        (units.Seconds, '0.5', 0.5),
        (units.Seconds, 0.5, 0.5),
        (units.Milliseconds, '0.0005s', 0.5),
        (units.WholeMilliseconds, '0.029s', 29),
        (units.Hertz, '2.4kHz', 2400.0),
        (units.Bytes, '2MB', 2_000_000),
        (units.Bytes, '2MiB', 2_097_152),
        (units.Bytes, '1KB', 1000),
        (units.Megabytes, '1GB', 1000),
    ],
)
def test_units_normalize_to_plain_numbers(
    annotation: object, value: object, expected: int | float
) -> None:
    result = TypeAdapter(annotation).validate_python(value)
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    ('annotation', 'value'),
    [
        (units.Seconds, '3Hz'),
        (units.Hertz, '3ms'),
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


def test_unit_spec_parses_and_serializes_plain_numbers() -> None:
    spec = config.unit_spec(units.Seconds, 'TIME')
    assert spec.instance_from_str(['250ms']) == 0.25
    assert spec.str_from_instance(0.25) == ['0.25']
