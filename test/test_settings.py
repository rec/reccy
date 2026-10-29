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
    with pytest.raises(ValueError, match=r'\.json or \.toml'):
        settings.load(path, Preferences)
    with pytest.raises(ValueError, match=r'\.json or \.toml'):
        settings.save(path, Preferences())

    if exists:
        assert path.read_text() == 'original'
    else:
        assert not path.exists()


@pytest.mark.parametrize('suffix', ['.json', '.toml'])
def test_missing_settings_are_optional_but_read_requires_a_file(
    tmp_path: Path, suffix: str
) -> None:
    path = tmp_path / f'settings{suffix}'

    assert settings.load(path, Preferences) is None
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

    with pytest.raises(TypeError):
        settings.save(path, Nullable())

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
