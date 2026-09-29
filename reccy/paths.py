"""Portable filename and path sanitization."""

import re
from pathlib import Path

PROBLEMATIC_FILENAME_CHARACTERS = r'\\/:*?"<>|'
FILENAME_REPLACEMENTS = str.maketrans(
    PROBLEMATIC_FILENAME_CHARACTERS,
    '-' * len(PROBLEMATIC_FILENAME_CHARACTERS),
)
URL_PATH_REPLACEMENTS = str.maketrans(' #%[]^`{}', '-' * len(' #%[]^`{}'))


def legal_filename(value: str) -> str:
    value = ''.join(
        '-' if ord(c) < 32 or ord(c) == 127 else c
        for c in value.translate(FILENAME_REPLACEMENTS)
    )
    value = value.rstrip(' ')
    trailing_dots = len(value) - len(value.rstrip('.'))
    value = value.rstrip('.') + '-' * trailing_dots
    if not value:
        return '-'
    if re.fullmatch(
        r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])',
        value.split('.')[0].rstrip(' '),
        re.I,
    ):
        return '-' + value
    return value


def legal_path(path: Path) -> Path:
    return Path(
        *(part if part == path.anchor else legal_filename(part) for part in path.parts)
    )


def legal_url_path(path: Path) -> Path:
    value = re.sub(r'(?<=[-+_/]) | (?=[-+_/])', '', str(legal_path(path)))
    return Path(
        ''.join(
            '-' if ord(c) < 32 or ord(c) == 127 else c
            for c in value.translate(URL_PATH_REPLACEMENTS)
        )
    )
