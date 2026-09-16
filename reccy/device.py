from pydantic import BaseModel, Field, field_validator

DeviceDict = dict[str, float | int | str]
STABLE_DEVICE_ID_FIELDS = ('uid', 'unique_id', 'persistent_id', 'guid', 'identifier')


class AudioMidiDeviceSpec(BaseModel, frozen=True):
    """Candidate device names; consumers define selection and matching policy."""

    name: str
    audio_device_names: list[str] = Field(default_factory=list)
    midi_input_names: list[str] = Field(default_factory=list)

    @field_validator('name')
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('must not be empty')
        return value

    @field_validator('audio_device_names', 'midi_input_names')
    @classmethod
    def validate_names(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError('must not contain empty values')
        return values


def device_key(info: DeviceDict) -> str:
    """Prefer a supplied persistent ID; the display-name fallback is not unique."""
    for field in STABLE_DEVICE_ID_FIELDS:
        if value := str(info.get(field, '')).strip():
            return f'{field}:{value}'
    return str(info['name'])
