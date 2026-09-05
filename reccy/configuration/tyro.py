from collections.abc import Callable, Mapping
from typing import TypeVar

import tyro
from pydantic import TypeAdapter
from tyro.constructors import PrimitiveConstructorSpec

_T = TypeVar('_T')


def unit_spec(annotation: object, metavar: str) -> PrimitiveConstructorSpec:
    adapter = TypeAdapter(annotation)
    return PrimitiveConstructorSpec(
        nargs=1,
        metavar=metavar,
        instance_from_str=lambda a: adapter.validate_python(a[0]),
        is_instance=lambda v: isinstance(v, (float, int)),
        str_from_instance=lambda v: [
            v.provenance.authored if hasattr(v, 'provenance') else str(v)
        ],
    )


def tyro_option(
    alias: str | None = None,
    name: str | None = None,
    metavar: str | None = None,
    constructor: type | Callable[..., object] | None = None,
    help_behavior_hint: str | Callable[[str], str] | None = None,
) -> object:
    aliases = [alias] if alias is not None else None
    return tyro.conf.arg(
        prefix_name=False,
        aliases=aliases,
        constructor=constructor,
        help_behavior_hint=help_behavior_hint,
        name=name,
        metavar=metavar,
    )


def prefix_spec(values: Mapping[str, _T], metavar: str) -> PrimitiveConstructorSpec[_T]:
    def parse(args: list[str]) -> _T:
        try:
            return values[args[0]]
        except KeyError:
            raise ValueError(f'Cannot understand {metavar}="{args[0]}"') from None

    return PrimitiveConstructorSpec(
        nargs=1,
        metavar=metavar,
        instance_from_str=parse,
        is_instance=lambda value: value in values.values(),
        str_from_instance=lambda value: [str(value)],
    )
