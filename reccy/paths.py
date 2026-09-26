"""Portable filename and path sanitization."""

from pathlib import Path

PROBLEMATIC_FILENAME_CHARACTERS = r'\\/:*?"<>|'
FILENAME_REPLACEMENTS = str.maketrans(
    PROBLEMATIC_FILENAME_CHARACTERS,
    '-' * len(PROBLEMATIC_FILENAME_CHARACTERS),
)


def legal_filename(value: str) -> str:
    return value.translate(FILENAME_REPLACEMENTS)


def legal_path(path: Path) -> Path:
    return Path(
        *(part if part == path.anchor else legal_filename(part) for part in path.parts)
    )
