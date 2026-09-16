from typing import Annotated

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    RootModel,
    TypeAdapter,
    field_serializer,
    model_serializer,
)

from reccy.configuration import units


def test_provenance_paths_distinguish_keys_and_nested_values() -> None:
    second = TypeAdapter(units.Seconds).validate_python('1s')
    result = units.collect_unit_provenance(
        {'a.b': second, 'a': {'b': second}, '~/': second, '': second}
    )
    assert set(result) == {'/a.b', '/a/b', '/~0~1', '/'}
    with pytest.raises(TypeError, match='string dictionary keys'):
        units.collect_unit_provenance({1: second, '1': second})


def test_unit_dumps_use_field_names_despite_serialization_aliases() -> None:
    class Config(BaseModel):
        duration: units.Seconds = Field(alias='time', serialization_alias='seconds')
        model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    value = Config(time='1 min')
    assert units.authored_dump(value) == {'duration': '1 min'}
    assert units.runtime_dump(value) == {'duration': 60.0}
    assert Config.model_validate(units.revalidation_dump(value)) == value


class Filtered(BaseModel):
    values: list[units.Seconds]

    @field_serializer('values')
    def filter_values(self, values: list[float]) -> list[float]:
        return values[1:]


class Reshaped(BaseModel):
    @model_serializer
    def serialize(self) -> str:
        return 'reshaped'


class AnnotatedSerializer(BaseModel):
    value: Annotated[units.Seconds, PlainSerializer(str)]


@pytest.mark.parametrize(
    'value',
    [
        Filtered(values=['1s', '2s']),
        Reshaped(),
        AnnotatedSerializer(value='1s'),
        RootModel[units.Seconds]('1s'),
    ],
)
def test_unit_dumps_reject_unsupported_serialization(value: BaseModel) -> None:
    class Nested(BaseModel):
        values: list[BaseModel]

    for v in [value, Nested(values=[value])]:
        for f in [units.runtime_dump, units.authored_dump, units.revalidation_dump]:
            with pytest.raises(TypeError, match='do not support'):
                f(v)
