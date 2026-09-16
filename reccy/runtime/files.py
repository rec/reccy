"""Atomic publication for writers that require an output path."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile


@contextmanager
def atomic_output(path: Path, *, sync: bool = False) -> Iterator[Path]:
    """Publish a completed sibling temporary file; concurrent last replacement wins.

    Create missing parent directories. Writers must close their handles before
    leaving the context. sync fsyncs file contents, not the parent directory.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=path.parent, prefix=f'.{path.stem}-', suffix=path.suffix, delete=False
    ) as file:
        temporary = Path(file.name)
    try:
        yield temporary
        if sync:
            with temporary.open('r+b') as file:
                os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
