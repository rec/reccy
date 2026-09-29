import logging
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import pytest

from reccy.runtime import logging as reccy_logging


def test_configure_adds_utc_stderr_handler(monkeypatch) -> None:
    root = logging.getLogger()
    original_handlers = root.handlers
    original_level = root.level
    stream = StringIO()
    monkeypatch.setattr(reccy_logging.sys, 'stderr', stream)
    root.handlers = []
    try:
        reccy_logging.configure()
        reccy_logging.get_logger('test').error('failed')

        assert 'ERROR test: failed' in stream.getvalue()
        assert stream.getvalue().endswith('Z ERROR test: failed\n')
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_configure_redirects_output_to_rotating_log(monkeypatch, tmp_path) -> None:
    root = logging.getLogger()
    original_handlers = root.handlers
    original_level = root.level
    original_stdout = reccy_logging.sys.stdout
    original_stderr = reccy_logging.sys.stderr
    path = tmp_path / 'service.log'
    root.handlers = []
    try:
        reccy_logging.configure(path, service_name='test')
        print('standard output')
        reccy_logging.get_logger('test').error('failed')

        assert 'INFO root: test logging started' in path.read_text()
        assert 'standard output' in path.read_text()
        assert 'ERROR test: failed' in path.read_text()
    finally:
        reccy_logging.sys.stdout = original_stdout
        reccy_logging.sys.stderr = original_stderr
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_rotating_log_stream_limits_retained_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(reccy_logging, 'MAX_LOG_BYTES', 3)
    with reccy_logging.RotatingLogStream(tmp_path / 'service.log') as stream:
        for _ in range(4):
            stream.write('abc')
            stream.write('\n')

    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix != '.lock') == [
        'service.log',
        'service.log.1',
        'service.log.2',
    ]


def test_log_path_allows_only_one_writer(tmp_path: Path) -> None:
    path = tmp_path / 'service.log'
    with reccy_logging.RotatingLogStream(path) as first:
        first.write('first\n')
        with pytest.raises(BlockingIOError, match='claimed'):
            reccy_logging.RotatingLogStream(path)
    with reccy_logging.RotatingLogStream(path) as second:
        second.write('second\n')
    assert path.read_text() == 'first\nsecond\n'


def test_failed_rotation_keeps_log_stream_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(reccy_logging, 'MAX_LOG_BYTES', 3)
    path = tmp_path / 'service.log'
    replace = Path.replace
    failed = False

    def fail_once(self: Path, target: Path) -> Path:
        nonlocal failed
        if self == path and not failed:
            failed = True
            raise OSError('rename failed')
        return replace(self, target)

    monkeypatch.setattr(Path, 'replace', fail_once)
    with reccy_logging.RotatingLogStream(path) as stream:
        stream.write('abc')
        with pytest.raises(OSError, match='rename failed'):
            stream.write('d')
        stream.write('e')
    assert path.read_text() == 'e'
    assert path.with_suffix('.log.1').read_text() == 'abc'


def test_explicit_file_logging_replaces_existing_handlers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = logging.getLogger()
    monkeypatch.setattr(root, 'handlers', [logging.StreamHandler(StringIO())])
    monkeypatch.setattr(root, 'level', root.level)
    monkeypatch.setattr(reccy_logging.sys, 'stdout', StringIO())
    monkeypatch.setattr(reccy_logging.sys, 'stderr', StringIO())
    path = tmp_path / 'output.log'
    reccy_logging.configure(path, service_name='test')
    stream = reccy_logging.sys.stdout
    try:
        reccy_logging.configure(path, service_name='test')
        assert reccy_logging.sys.stdout is stream
        assert len(root.handlers) == 1
        assert not stream.isatty()
        assert stream.encoding
        print('redirected')
        stream.flush()
        assert 'redirected' in path.read_text()
    finally:
        stream.close()


def test_switching_log_paths_releases_previous_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = logging.getLogger()
    monkeypatch.setattr(root, 'handlers', [])
    monkeypatch.setattr(root, 'level', root.level)
    monkeypatch.setattr(reccy_logging.sys, 'stdout', StringIO())
    monkeypatch.setattr(reccy_logging.sys, 'stderr', StringIO())
    first_path = tmp_path / 'first.log'
    second_path = tmp_path / 'second.log'
    reccy_logging.configure(first_path, service_name='test')
    first = reccy_logging.sys.stderr
    try:
        reccy_logging.configure(second_path, service_name='test')
        assert first.closed
        with reccy_logging.RotatingLogStream(first_path):
            pass
    finally:
        reccy_logging.sys.stderr.close()


def test_concurrent_rotation_preserves_complete_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(reccy_logging, 'MAX_LOG_BYTES', 100)
    monkeypatch.setattr(reccy_logging, 'MAX_LOG_FILES', 100)
    with reccy_logging.RotatingLogStream(tmp_path / 'output.log') as stream:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(stream.write, [f'{i:04d}\n' for i in range(400)]))
    lines = [s for p in tmp_path.iterdir() for s in p.read_text().splitlines()]
    assert sorted(lines) == [f'{i:04d}' for i in range(400)]
