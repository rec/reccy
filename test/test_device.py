import pytest

from reccy.device import AudioMidiDeviceSpec, device_key


def test_audio_midi_device_spec_rejects_empty_identifiers() -> None:
    with pytest.raises(ValueError, match='name'):
        AudioMidiDeviceSpec(name='')

    with pytest.raises(ValueError, match='audio_device_names'):
        AudioMidiDeviceSpec(name='Flow 8', audio_device_names=[''])


def test_device_key_prefers_stable_identity() -> None:
    assert device_key({'name': 'USB Audio', 'uid': 'first'}) == 'uid:first'
    assert device_key({'name': 'USB Audio'}) == 'USB Audio'
