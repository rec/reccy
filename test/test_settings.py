import json
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import BaseModel

from reccy.configuration import settings
from reccy.configuration.settings import write_text_atomically
from reccy.errors import ReccyError


class Preferences(BaseModel, frozen=True):
    enabled: bool = True
    names: list[str] = ['first', 'second']
    nested: dict[str, int] = {'count': 3}


@pytest.mark.parametrize('writer', ['save-json', 'save-toml', 'write-json'])
@pytest.mark.parametrize('write_empty_dicts', [False, True])
def test_empty_dictionary_filtering_preserves_list_positions_and_input(
    tmp_path: Path, writer: str, write_empty_dicts: bool
) -> None:
    class Dictionaries(BaseModel, frozen=True):
        empty: dict[str, object] = {}
        nested: dict[str, object] = {'child': {'empty': {}}}
        retained: dict[str, object] = {'empty': {}, 'value': 7}
        rows: list[dict[str, object]] = [{}, {'empty': {}}, {'value': 1}, {}]
        lists: list[list[dict[str, object]]] = [[{}], []]
        empty_list: list[str] = []
        zero: int = 0
        false: bool = False
        text: str = ''

    value = Dictionaries()
    original = value.model_copy(deep=True)
    path = tmp_path / ('settings.toml' if writer == 'save-toml' else 'settings.json')

    if writer == 'write-json':
        settings.write_json_model(path, value, write_empty_dicts=write_empty_dicts)
    else:
        settings.save(path, value, write_empty_dicts=write_empty_dicts)

    actual = settings.read(path)
    assert isinstance(actual, dict)
    assert value == original
    assert ('empty' in actual) is write_empty_dicts
    assert ('nested' in actual) is write_empty_dicts
    assert actual['retained'] == (
        {'empty': {}, 'value': 7} if write_empty_dicts else {'value': 7}
    )
    assert actual['rows'] == (
        [{}, {'empty': {}}, {'value': 1}, {}]
        if write_empty_dicts
        else [{}, {}, {'value': 1}, {}]
    )
    assert actual['lists'] == [[{}], []]
    assert actual['empty_list'] == []
    assert actual['zero'] == 0
    assert actual['false'] is False
    assert actual['text'] == ''


@pytest.mark.parametrize('writer', ['save-json', 'save-toml', 'write-json'])
def test_writers_omit_empty_dictionary_entries_by_default(
    tmp_path: Path, writer: str
) -> None:
    class Empty(BaseModel, frozen=True):
        value: dict[str, object] = {}

    path = tmp_path / ('settings.toml' if writer == 'save-toml' else 'settings.json')
    if writer == 'write-json':
        settings.write_json_model(path, Empty())
    else:
        settings.save(path, Empty())

    assert settings.read(path) == {}


@pytest.mark.parametrize('suffix', ['.json', '.toml'])
def test_settings_round_trip_in_the_selected_format(
    tmp_path: Path, suffix: str
) -> None:
    path = tmp_path / 'nested' / f'settings{suffix}'
    value = Preferences()

    settings.save(path, value)

    assert settings.load(path, Preferences) == value
    assert settings.read(path) == value.model_dump()
    decode = json.loads if suffix == '.json' else tomllib.loads
    assert decode(path.read_text()) == value.model_dump()
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize('suffix', ['', '.yaml', '.JSON', '.TOML'])
@pytest.mark.parametrize('exists', [False, True])
def test_settings_reject_unsupported_suffixes_before_io(
    tmp_path: Path, suffix: str, exists: bool
) -> None:
    path = tmp_path / f'settings{suffix}'
    if exists:
        path.write_text('original')

    with pytest.raises(ValueError, match=r'\.json or \.toml'):
        settings.read(path)
    with pytest.raises(ReccyError, match=r'\.json or \.toml') as loaded:
        settings.load(path, Preferences)
    with pytest.raises(ReccyError, match=r'\.json or \.toml') as saved:
        settings.save(path, Preferences())

    assert isinstance(loaded.value.__cause__, ValueError)
    assert isinstance(saved.value.__cause__, ValueError)

    if exists:
        assert path.read_text() == 'original'
    else:
        assert not path.exists()


@pytest.mark.parametrize('suffix', ['.json', '.toml'])
def test_missing_settings_raise_with_the_original_cause(
    tmp_path: Path, suffix: str
) -> None:
    path = tmp_path / f'settings{suffix}'

    with pytest.raises(ReccyError, match='Could not load settings') as raised:
        settings.load(path, Preferences)
    assert isinstance(raised.value.__cause__, FileNotFoundError)
    with pytest.raises(FileNotFoundError):
        settings.read(path)


@pytest.mark.parametrize(
    'suffix, content',
    [
        ('.json', '{'),
        ('.toml', '['),
        ('.json', '{"enabled": 123}'),
        ('.toml', 'enabled = 123'),
    ],
)
def test_invalid_settings_report_the_source_path(
    tmp_path: Path, suffix: str, content: str
) -> None:
    path = tmp_path / f'settings{suffix}'
    path.write_text(content)

    with pytest.raises(ReccyError, match='Could not load settings') as raised:
        settings.load(path, Preferences)

    assert str(path) in str(raised.value)


def test_toml_rejects_null_without_replacing_existing_settings(tmp_path: Path) -> None:
    class Nullable(BaseModel, frozen=True):
        value: str | None = None

    path = tmp_path / 'settings.toml'
    path.write_text('original')

    with pytest.raises(ReccyError, match='Could not save settings') as raised:
        settings.save(path, Nullable())

    assert isinstance(raised.value.__cause__, TypeError)
    assert path.read_text() == 'original'


def test_explicit_json_writer_keeps_supporting_other_suffixes(tmp_path: Path) -> None:
    path = tmp_path / 'settings.owner'

    settings.write_json_model(path, Preferences())

    assert json.loads(path.read_text()) == Preferences().model_dump()


def test_concurrent_writes_publish_complete_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / 'settings.json'
    barrier = Barrier(2)
    replace = Path.replace

    def synchronized_replace(self: Path, target: Path) -> Path:
        barrier.wait(timeout=2)
        return replace(self, target)

    monkeypatch.setattr(Path, 'replace', synchronized_replace)
    values = ['a' * 10000, 'b' * 10000]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write_text_atomically, destination, v) for v in values]
        for f in futures:
            f.result()
    assert destination.read_text() in values
    assert list(tmp_path.iterdir()) == [destination]


def test_failed_replacement_preserves_destination_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / 'settings.json'
    destination.write_text('original')

    def failed_replace(self: Path, target: Path) -> Path:
        raise OSError('replacement failed')

    monkeypatch.setattr(Path, 'replace', failed_replace)
    with pytest.raises(OSError, match='replacement failed'):
        write_text_atomically(destination, 'new')
    assert destination.read_text() == 'original'
    assert list(tmp_path.iterdir()) == [destination]
