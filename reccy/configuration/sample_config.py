"""Generate commented TOML examples from Pydantic configuration types."""

import ast
import inspect
import tokenize
from io import StringIO
from pathlib import Path
from textwrap import dedent
from types import UnionType
from typing import Annotated, Union, cast, get_args, get_origin

import tomlkit
from pydantic import BaseModel
from tomlkit.items import AoT, Item, Table

from .settings import write_text_atomically


def write_sample_config(model: type[BaseModel], path: Path) -> None:
    """Write a complete sample with parameter comments and descriptions.

    Defaults are retained, except None (which TOML cannot represent). Required
    primitives use 'sample', 0, or False; unions use their first non-None member
    and containers start empty. Nested models are filled recursively. Other
    classes must support construction without arguments and TOML serialization.
    Invalid samples and recursive required models raise before writing the file.
    """
    value = _sample_model(model, ())
    table = _model_table(value)
    write_text_atomically(path, tomlkit.dumps(table))


def _sample_model(
    model: type[BaseModel],
    parents: tuple[type[BaseModel], ...],
    existing: BaseModel | None = None,
) -> BaseModel:
    if existing is None and model in parents:
        raise ValueError(f'Recursive required configuration: {model.__qualname__}')
    data: dict[str, object] = {}
    for n, f in model.model_fields.items():
        value = (
            getattr(existing, n)
            if existing is not None
            else f.get_default(call_default_factory=True, validated_data=data)
        )
        if (existing is None and f.is_required()) or value is None:
            value = _sample_value(f.annotation, (*parents, model), n)
        else:
            value = _fill_defaults(value, (*parents, model))
        data[n] = value
    return model.model_validate(data, by_alias=False, by_name=True)


def _fill_defaults(value: object, parents: tuple[type[BaseModel], ...]) -> object:
    if isinstance(value, BaseModel):
        return _sample_model(type(value), parents, value)
    if isinstance(value, list):
        return [_fill_defaults(v, parents) for v in value]
    if isinstance(value, dict):
        return {k: _fill_defaults(v, parents) for k, v in value.items()}
    return value


def _sample_value(
    annotation: object, parents: tuple[type[BaseModel], ...], name: str
) -> object:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _sample_value(get_args(annotation)[0], parents, name)
    if origin in (Union, UnionType):
        for a in get_args(annotation):
            if a is not type(None):
                return _sample_value(a, parents, name)
    if annotation is str:
        return 'sample'
    if annotation is bool:
        return False
    if annotation in (int, float):
        return 0
    if origin in (list, dict, tuple, set, frozenset):
        return origin()
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return _sample_model(annotation, parents)
        try:
            return annotation()
        except TypeError as error:
            raise TypeError(f'Cannot create sample for {name}: {annotation}') from error
    raise TypeError(f'Cannot create sample for {name}: {annotation}')


def _model_table(value: BaseModel, *, nested: bool = False) -> Table:
    model = type(value)
    table = tomlkit.table()
    comments = _source_comments(model)
    serialized = value.model_dump(mode='json', by_alias=True)
    fields: list[tuple[str, Item, str]] = []
    for n, f in model.model_fields.items():
        key = f.serialization_alias or f.alias or n
        if key not in serialized:
            continue
        text = comments.get(n, '')
        if f.description and f.description not in text:
            text = '\n'.join(filter(None, (text, f.description)))
        fields.append((key, _toml_value(getattr(value, n), serialized[key]), text))
    # TOML places scalar parameters before nested tables. Emit their comments
    # in the same order so each description stays with its parameter.
    previous_documented = False
    for index, (key, item, text) in enumerate(
        sorted(fields, key=lambda f: isinstance(f[1], (Table, AoT)))
    ):
        if (nested and not index) or (
            index and (text or previous_documented or isinstance(item, (Table, AoT)))
        ):
            table.add(tomlkit.nl())
        _add_comments(table, text)
        table.add(key, item)
        if isinstance(item, Table):
            item.trivia.indent = ''
        elif isinstance(item, AoT) and item:
            item[0].trivia.indent = ''
        previous_documented = bool(text)
    return table


def _toml_value(value: object, serialized: object) -> Item:
    if isinstance(value, BaseModel):
        return _model_table(value, nested=True)
    if isinstance(value, dict) and isinstance(serialized, dict):
        table = tomlkit.table()
        original = cast(dict[str, object], value)
        for k, v in cast(dict[str, object], serialized).items():
            table.add(k, _toml_value(original[k], v))
        return table
    if isinstance(value, (list, tuple, set, frozenset)) and isinstance(
        serialized, list
    ):
        if serialized and all(isinstance(v, dict) for v in serialized):
            tables = tomlkit.aot()
            for v, s in zip(value, serialized, strict=True):
                item = _toml_value(v, s)
                assert isinstance(item, Table)
                tables.append(item)
            return tables
        array = tomlkit.array()
        for v, s in zip(value, serialized, strict=True):
            array.append(_toml_value(v, s))
        return array
    return tomlkit.item(serialized)


def _add_comments(table: Table, text: str) -> None:
    for line in text.splitlines():
        table.add(tomlkit.comment(line))


def _source_comments(model: type[BaseModel]) -> dict[str, str]:
    comments: dict[str, str] = {}
    for cls in reversed(model.__mro__):
        if cls is BaseModel or not issubclass(cls, BaseModel):
            continue
        try:
            source = dedent(inspect.getsource(cls))
        except (OSError, TypeError):
            continue
        node = ast.parse(source).body[0]
        assert isinstance(node, ast.ClassDef)
        lines = source.splitlines()
        tokens = list(tokenize.generate_tokens(StringIO(source).readline))
        for index, field in enumerate(node.body):
            if not isinstance(field, ast.AnnAssign) or not isinstance(
                field.target, ast.Name
            ):
                continue
            start = field.lineno
            while start > node.lineno + 1 and lines[start - 2].lstrip().startswith('#'):
                start -= 1
            end = field.end_lineno or field.lineno
            text = [
                t.string.removeprefix('#').lstrip()
                for t in tokens
                if t.type == tokenize.COMMENT and start <= t.start[0] <= end
            ]
            if index + 1 < len(node.body):
                following = node.body[index + 1]
                if (
                    isinstance(following, ast.Expr)
                    and isinstance(following.value, ast.Constant)
                    and isinstance(following.value.value, str)
                ):
                    text.append(inspect.cleandoc(following.value.value))
            comments[field.target.id] = '\n'.join(text)
    return comments
