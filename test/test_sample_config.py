import tomllib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, Field, ValidationError, create_model

from reccy.configuration.sample_config import write_sample_config

if TYPE_CHECKING:
    from reccy.pytest_plugin import SampleConfigRegression

pytest_plugins = ['reccy.pytest_plugin']


class Connection(BaseModel, frozen=True):
    """Connection settings."""

    # The remote machine.
    host: str = Field(description='Host name or address.')  # No protocol prefix.
    port: int = 8080
    """TCP port."""


class Camera(BaseModel, frozen=True):
    exposure_seconds: float = 2.5
    """Seconds to expose each frame; increase for faint objects or narrow filters."""

    # Keep the sensor below ambient temperature to reduce thermal noise.
    cooling_enabled: bool = True

    resolution: list[int] = [1920, 1080]
    """Output width and height in pixels, after any sensor binning."""


class CaptureSequence(BaseModel, frozen=True):
    filter_name: str = 'hydrogen-alpha'
    """Installed filter-wheel label to select before starting this sequence."""

    frames: int = 120
    """Number of exposures to stack; rejected frames still count toward this limit."""

    dither_pixels: float = 3.0
    """Shift the telescope between frames by this many pixels to average hot pixels."""


class Coordinates(BaseModel, frozen=True):
    right_ascension_hours: float = 20.97
    """Target position eastward along the celestial equator, in hours from 0 to 24."""

    declination_degrees: float = 44.33
    """Target position north or south of the celestial equator, from -90 to 90."""


class ObservatoryConfig(BaseModel, frozen=True):
    observer: str = Field(description='Name recorded in the metadata of every frame.')

    # Settings for the imaging sensor; apply these before running any sequences.
    camera: Camera

    session_date: date = date(2026, 8, 12)
    """Local date used to group frames from a single observing night."""

    output_directory: Path = Path('observations/perseids')
    """Directory for raw frames, relative to the process working directory."""

    # Leave false for an unattended session so the display does not spoil dark vision.
    preview_enabled: bool = False

    sequences: list[CaptureSequence] = [CaptureSequence()]
    """Run these capture sequences in order, changing filters between sequences."""

    targets: dict[str, Coordinates] = {'north-america-nebula': Coordinates()}
    """Named sky positions available to the scheduler; coordinates use epoch J2000."""

    calibration_files: list[str]
    """Paths to dark and flat calibration frames; leave empty to store raw data only."""

    labels: dict[str, str]
    """Metadata attached to every frame, such as a project or instrument ID."""


def test_writes_recursive_sample_values_and_source_comments(
    sample_config_regression: 'SampleConfigRegression',
) -> None:
    text = sample_config_regression(ObservatoryConfig)
    data = tomllib.loads(text)

    assert data == {
        'observer': 'sample',
        'camera': {
            'exposure_seconds': 2.5,
            'cooling_enabled': True,
            'resolution': [1920, 1080],
        },
        'session_date': '2026-08-12',
        'output_directory': 'observations/perseids',
        'preview_enabled': False,
        'sequences': [
            {'filter_name': 'hydrogen-alpha', 'frames': 120, 'dither_pixels': 3.0}
        ],
        'targets': {
            'north-america-nebula': {
                'right_ascension_hours': 20.97,
                'declination_degrees': 44.33,
            }
        },
        'calibration_files': [],
        'labels': {},
    }
    assert ObservatoryConfig.model_validate(data).observer == 'sample'


def test_groups_undocumented_parameters(
    sample_config_regression: 'SampleConfigRegression',
) -> None:
    class Metadata(BaseModel, frozen=True):
        title: str = 'sample'
        description: str = 'sample'
        category: str = Field(
            default='sample',
            description='Twitch category name; YouTube or Kick numeric category ID.',
        )
        tags: list[str] = []
        language: str = 'sample'
        privacy: str = 'unlisted'
        scheduled_start: datetime = datetime(2026, 8, 12, 20, tzinfo=UTC)

    class Settings(BaseModel, frozen=True):
        metadata: Metadata = Metadata()

    sample_config_regression(
        Settings, path=Path(__file__).parents[1] / 'doc' / 'sample-metadata.toml'
    )


def test_only_parameter_documentation_is_included(tmp_path: Path) -> None:
    class Settings(BaseModel, frozen=True):
        """Class-level documentation must not appear in the sample."""

        # General implementation notes are not parameter documentation.

        # Connection to use for uploading completed frames.
        connection: Connection
        # Retry failed uploads this many times before leaving the frames on disk.
        retries: int = 3

    path = tmp_path / 'sample.toml'
    write_sample_config(Settings, path)
    text = path.read_text()
    assert 'Class-level documentation' not in text
    assert 'General implementation notes' not in text
    assert 'Connection settings.' not in text
    assert '# The remote machine.\n# No protocol prefix.\n' in text
    assert '# Host name or address.\nhost = "sample"' in text
    assert text.index('retries = 3') < text.index('# Connection to use')
    assert text.index('# Connection to use') < text.index('[connection]')


def test_preserves_inherited_comments_and_default_factory(tmp_path: Path) -> None:
    class Parent(BaseModel, frozen=True):
        # An inherited setting.
        name: str = 'original'

    class Child(Parent, frozen=True):
        numbers: list[int] = Field(default_factory=lambda: [1, 2])

    path = tmp_path / 'sample.toml'
    write_sample_config(Child, path)
    assert tomllib.loads(path.read_text()) == {'name': 'original', 'numbers': [1, 2]}
    assert '# An inherited setting.' in path.read_text()
    assert Child().numbers == [1, 2]


def test_required_primitives_use_sample_values(tmp_path: Path) -> None:
    class RequiredSettings(BaseModel, frozen=True):
        name: str
        count: int
        ratio: float
        enabled: bool

    path = tmp_path / 'sample.toml'
    write_sample_config(RequiredSettings, path)
    assert tomllib.loads(path.read_text()) == {
        'name': 'sample',
        'count': 0,
        'ratio': 0.0,
        'enabled': False,
    }


def test_none_and_unions_choose_first_non_none_member(tmp_path: Path) -> None:
    class OptionalSettings(BaseModel, frozen=True):
        choice: str | int
        connection: Connection | None = None
        count: int | None = None

    path = tmp_path / 'sample.toml'
    write_sample_config(OptionalSettings, path)
    assert tomllib.loads(path.read_text()) == {
        'choice': 'sample',
        'connection': {'host': 'sample', 'port': 8080},
        'count': 0,
    }


def test_fills_none_recursively_without_mutating_default_instances(
    tmp_path: Path,
) -> None:
    class Child(BaseModel, frozen=True):
        name: str | None = None

    default = Child()

    class Parent(BaseModel, frozen=True):
        child: Child = default
        children: list[Child] = [default]

    path = tmp_path / 'sample.toml'
    write_sample_config(Parent, path)
    assert tomllib.loads(path.read_text()) == {
        'child': {'name': 'sample'},
        'children': [{'name': 'sample'}],
    }
    assert default.name is None
    assert Parent().children[0].name is None


def test_uses_default_constructor_for_other_classes(tmp_path: Path) -> None:
    class ConstructorSettings(BaseModel, frozen=True):
        value: bytes

    path = tmp_path / 'sample.toml'
    write_sample_config(ConstructorSettings, path)
    assert tomllib.loads(path.read_text()) == {'value': ''}


@pytest.mark.parametrize('failure', ['constructor', 'validation', 'recursive', 'null'])
def test_generation_errors_preserve_existing_file(tmp_path: Path, failure: str) -> None:
    class NeedsArgument:
        def __init__(self, value: int) -> None:
            self.value = value

    class ConstructorSettings(BaseModel, frozen=True, arbitrary_types_allowed=True):
        value: NeedsArgument

    class ConstrainedSettings(BaseModel, frozen=True):
        count: int = Field(gt=0)

    class RecursiveSettings(BaseModel, frozen=True):
        child: 'RecursiveSettings'

    class NullSettings(BaseModel, frozen=True):
        value: None = None

    models = {
        'constructor': (ConstructorSettings, TypeError),
        'validation': (ConstrainedSettings, ValidationError),
        'recursive': (RecursiveSettings, ValueError),
        'null': (NullSettings, TypeError),
    }
    model, error = models[failure]
    path = tmp_path / 'sample.toml'
    path.write_text('original')
    with pytest.raises(error):
        write_sample_config(model, path)
    assert path.read_text() == 'original'


def test_dynamic_models_keep_pydantic_descriptions_without_source(
    tmp_path: Path,
) -> None:
    model = create_model('Dynamic', name=(str, Field(description='A display name.')))
    path = tmp_path / 'sample.toml'
    write_sample_config(model, path)
    assert '# A display name.' in path.read_text()
    assert tomllib.loads(path.read_text()) == {'name': 'sample'}
