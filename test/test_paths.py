from pathlib import Path

from reccy.paths import legal_filename, legal_path, legal_url_path


def test_legal_filename_can_be_created(tmp_path: Path) -> None:
    name = ''.join(chr(i) for i in range(2, 0x150, 7))
    (tmp_path / legal_filename(name)).write_text('ok')


def test_legal_filename_replaces_problematic_characters() -> None:
    assert legal_filename(r'\/:*?"<>|') == '---------'
    assert legal_filename('.,;= ') == '.,;= '


def test_legal_filename_avoids_windows_reserved_names_and_controls() -> None:
    assert legal_filename('CON.txt') == '-CON.txt'
    assert legal_filename('lpt9') == '-lpt9'
    assert legal_filename('a\x00b') == 'a-b'
    assert legal_filename('') == '-'


def test_legal_path_replaces_each_filename_segment() -> None:
    assert legal_path(Path('/tmp/device:name/track?')) == Path(
        '/tmp/device-name/track-'
    )


def test_legal_url_path_replaces_url_illegal_characters_and_spaces() -> None:
    path = Path(
        '/tmp/device:name/track? /mix ^`{} name/one + two/one - two/'
        'one _ two/one / two/other space'
    )

    assert legal_url_path(path) == Path(
        '/tmp/device-name/track-/mix------name/one+two/one-two/one_two/'
        'one/two/other-space'
    )


def test_legal_url_path_keeps_names_readable_without_url_delimiters() -> None:
    assert legal_url_path(Path('mix#1%done[final]\t.wav')) == Path(
        'mix-1-done-final--.wav'
    )
