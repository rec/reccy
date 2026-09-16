from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from reccy.configuration.settings import write_text_atomically


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
