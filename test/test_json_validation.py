import pytest

from reccy.configuration.validators import validate_json


class JsonList(list[object]):
    pass


def test_json_container_subclasses_require_non_strict_validation() -> None:
    value = {'items': JsonList([None, True, 2, 0.5, 'text'])}
    validate_json(value, strict=False)
    with pytest.raises(ValueError, match='JSON values'):
        validate_json(value)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), {1: 'text'}, (1, 2)])
@pytest.mark.parametrize('strict', [True, False])
def test_json_validation_rejects_non_json_values(value: object, strict: bool) -> None:
    with pytest.raises(ValueError):
        validate_json(value, strict=strict)


def test_json_validation_bounds_size_and_depth() -> None:
    validate_json([0] * 9999)
    with pytest.raises(ValueError, match='10000 values'):
        validate_json([0] * 10000)
    value: object = None
    for _ in range(64):
        value = [value]
    validate_json(value)
    with pytest.raises(ValueError, match='64 levels'):
        validate_json([value])
