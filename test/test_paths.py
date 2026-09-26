from pathlib import Path

from reccy.paths import legal_filename, legal_path


def test_legal_filename_can_be_created(tmp_path: Path) -> None:
    name = ''.join(chr(i) for i in range(2, 0x150, 7))
    (tmp_path / legal_filename(name)).write_text('ok')


def test_legal_filename_replaces_problematic_characters() -> None:
    assert legal_filename(r'\/:*?"<>|') == '---------'
    assert legal_filename('.,;= ') == '.,;= '


def test_legal_path_replaces_each_filename_segment() -> None:
    assert legal_path(Path('/tmp/device:name/track?')) == Path(
        '/tmp/device-name/track-'
    )
