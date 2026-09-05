from reccy.protocol.jsonl import Compress, Decompress


def test_compresses_sparse_records_and_retains_none_state() -> None:
    records = [
        {'type': 'meter', 'channel': 1, 'level': 0.5},
        {'type': 'meter', 'channel': 1},
        {'type': 'meter', 'channel': 1, 'level': None},
        {'type': 'meter', 'channel': 2},
    ]

    compressed = list(Compress('type')(records))

    assert compressed == [
        {'type': 'meter', 'channel': 1, 'level': 0.5},
        {'type': 'meter', 'level': None},
        {'type': 'meter'},
        {'type': 'meter', 'channel': 2},
    ]
    assert list(Decompress('type')(compressed)) == [
        {'type': 'meter', 'channel': 1, 'level': 0.5},
        {'type': 'meter', 'channel': 1, 'level': None},
        {'type': 'meter', 'channel': 1, 'level': None},
        {'type': 'meter', 'channel': 2, 'level': None},
    ]


def test_tracks_state_independently_for_each_type() -> None:
    records = [
        {'type': 'meter', 'level': 0.5},
        {'type': 'status', 'online': True},
        {'type': 'meter', 'level': 0.5},
    ]

    assert list(Compress('type')(records)) == [
        {'type': 'meter', 'level': 0.5},
        {'type': 'status', 'online': True},
        {'type': 'meter'},
    ]
