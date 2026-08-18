import logging
import sys
import time
from pathlib import Path

MAX_LOG_BYTES = 1024 * 1024
MAX_LOG_FILES = 3


class RotatingLogStream:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a')

    def write(self, text: str) -> int:
        if text:
            self._rotate(len(text.encode()))
        return self.file.write(text)

    def flush(self) -> None:
        self.file.flush()

    def _rotate(self, size: int) -> None:
        if self.file.tell() + size <= MAX_LOG_BYTES:
            return
        self.file.close()
        oldest = self.path.with_suffix(self.path.suffix + f'.{MAX_LOG_FILES - 1}')
        oldest.unlink(missing_ok=True)
        for i in range(MAX_LOG_FILES - 2, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f'.{i}')
            target = self.path.with_suffix(self.path.suffix + f'.{i + 1}')
            if source.exists():
                source.replace(target)
        if self.path.exists():
            self.path.replace(self.path.with_suffix(self.path.suffix + '.1'))
        self.file = self.path.open('a')


def configure(
    path: Path | None = None,
    *,
    service_name: str | None = None,
    verbose: bool = False,
) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if root.handlers:
        return
    stream = sys.stderr
    if path is not None:
        if service_name is None:
            raise ValueError('service_name is required when logging to a file')
        stream = RotatingLogStream(path)
        sys.stdout = stream
        sys.stderr = stream
    handler = logging.StreamHandler(stream)
    formatter = logging.Formatter(
        '%(asctime)sZ %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root.addHandler(handler)
    if path is not None:
        root.info('%s logging started', service_name)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
