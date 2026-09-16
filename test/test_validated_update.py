from typing import Self

import pytest
from pydantic import BaseModel, Field, ValidationError, model_validator

from reccy.configuration.units import Seconds, authored_dump
from reccy.configuration.update import validated_update


class Timing(BaseModel, frozen=True):
    duration: Seconds = Field(alias='time')
    limit: float = 10

    @model_validator(mode='after')
    def check_limit(self) -> Self:
        if self.duration > self.limit:
            raise ValueError('duration exceeds limit')
        return self


class Config(BaseModel, frozen=True):
    timing: Timing
    name: str = 'original'


def test_nested_update_revalidates_without_mutating_original() -> None:
    original = Config(timing=Timing(time='1s'))
    changed = validated_update(original, ['timing', 'duration'], '250ms')
    assert changed.timing.duration == 0.25
    assert authored_dump(changed)['timing'] == {'duration': '250ms', 'limit': 10.0}
    assert original.timing.duration == 1.0
    assert changed is not original
    with pytest.raises(ValidationError, match='exceeds limit'):
        validated_update(original, ['timing', 'duration'], '20s')
    assert original.timing.duration == 1.0


def test_single_field_update_preserves_other_authored_values() -> None:
    original = Config(timing=Timing(time='1000ms'))
    changed = validated_update(original, ['name'], 'new')
    assert changed.name == 'new'
    assert original.name == 'original'
    assert authored_dump(changed)['timing'] == {'duration': '1000ms', 'limit': 10.0}


@pytest.mark.parametrize(
    'path',
    [
        [],
        ['unknown'],
        ['timing', 'unknown'],
        ['timing', 'time'],
        ['name', 'field'],
        'timing.duration',
    ],
)
def test_update_rejects_invalid_paths(path: list[str] | str) -> None:
    with pytest.raises(ValueError):
        validated_update(Config(timing=Timing(time='1s')), path, 1)
