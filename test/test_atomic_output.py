from pathlib import Path

import pytest

from reccy.configuration.settings import write_text_atomically
from reccy.runtime import files


@pytest.mark.parametrize('contents', ['preset = "example"\n', b'\x00\xffimage'])
def test_atomic_output_supports_text_and_binary_writers(
    tmp_path: Path, contents: str | bytes
) -> None:
    destination = tmp_path / 'new' / 'output.toml'
    with files.atomic_output(destination) as temporary:
        assert temporary.parent == destination.parent
        assert temporary.suffix == destination.suffix
        assert not destination.exists()
        if isinstance(contents, str):
            temporary.write_text(contents)
        else:
            temporary.write_bytes(contents)
    assert destination.read_bytes() == (
        contents.encode() if isinstance(contents, str) else contents
    )
    assert list(destination.parent.iterdir()) == [destination]


def test_writer_failure_preserves_existing_output(tmp_path: Path) -> None:
    destination = tmp_path / 'output.bin'
    destination.write_bytes(b'original')
    with pytest.raises(ValueError, match='failed'):
        with files.atomic_output(destination) as temporary:
            temporary.write_bytes(b'partial')
            raise ValueError('writer failed')
    assert destination.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [destination]


def test_text_writer_syncs_completed_contents_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / 'output.txt'
    synced: list[int] = []

    def sync(descriptor: int) -> None:
        assert not destination.exists()
        assert next(tmp_path.iterdir()).read_text() == 'complete'
        synced.append(descriptor)

    monkeypatch.setattr(files.os, 'fsync', sync)
    write_text_atomically(destination, 'complete')
    assert len(synced) == 1
