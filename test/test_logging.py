import logging
from io import StringIO

from reccy import logging as reccy_logging


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
    monkeypatch.setenv(reccy_logging.LOG_PATH_ENVIRONMENT_VARIABLE, str(path))
    root.handlers = []
    try:
        reccy_logging.configure()
        print('standard output')
        reccy_logging.get_logger('test').error('failed')

        assert 'standard output' in path.read_text()
        assert 'ERROR test: failed' in path.read_text()
    finally:
        reccy_logging.sys.stdout = original_stdout
        reccy_logging.sys.stderr = original_stderr
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_rotating_log_stream_limits_retained_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(reccy_logging, 'MAX_LOG_BYTES', 3)
    stream = reccy_logging.RotatingLogStream(tmp_path / 'service.log')

    for _ in range(4):
        stream.write('abc')
        stream.write('\n')

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        'service.log',
        'service.log.1',
        'service.log.2',
    ]
