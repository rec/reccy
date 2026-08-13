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
