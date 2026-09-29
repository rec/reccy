from reccy.protocol.jsonl import Compress, Decompress


def test_initial_null_is_equivalent_to_absent() -> None:
    compressed = list(Compress('type')([{'type': 'meter', 'level': None}]))
    assert compressed == [{'type': 'meter'}]
    assert list(Decompress('type')(compressed)) == [{'type': 'meter'}]


def test_codec_state_persists_across_calls() -> None:
    compress = Compress('type')
    decompress = Decompress('type')
    record = {'type': 'meter', 'level': 0.5}
    assert list(decompress(compress([record]))) == [record]
    delta = list(compress([record]))
    assert delta == [{'type': 'meter'}]
    assert list(decompress(delta)) == [record]
    assert list(Decompress('type')(delta)) == [{'type': 'meter'}]
    cleared = {'type': 'meter', 'level': None}
    assert list(decompress(compress([{'type': 'meter'}]))) == [cleared]
    assert list(decompress(compress([cleared]))) == [cleared]


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


def test_mutating_input_or_output_does_not_change_codec_state() -> None:
    compressor = Compress('type')
    input_record = {'type': 'meter', 'values': [1]}
    output_record = list(compressor([input_record]))[0]
    input_record['values'].append(2)
    output_record['values'].append(3)
    assert list(compressor([{'type': 'meter', 'values': [1]}])) == [{'type': 'meter'}]

    decompressor = Decompress('type')
    delta = {'type': 'meter', 'values': [1]}
    result = list(decompressor([delta]))[0]
    delta['values'].append(2)
    result['values'].append(3)
    assert list(decompressor([{'type': 'meter'}])) == [{'type': 'meter', 'values': [1]}]


def test_distant_key_reappearance_keeps_its_delta_baseline() -> None:
    compressor = Compress('type')
    decompressor = Decompress('type')
    original = {'type': 'first', 'value': [1]}
    assert list(decompressor(compressor([original]))) == [original]
    for i in range(100):
        assert list(decompressor(compressor([{'type': f'other-{i}', 'value': i}])))
    delta = list(compressor([original]))
    assert delta == [{'type': 'first'}]
    assert list(decompressor(delta)) == [original]
