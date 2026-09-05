from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from reccy.services import spec


def test_load_reads_service_specification() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'service.toml'
        path.write_text(
            'name = "example"\n'
            'display_name = "Example"\n'
            'description = "Example service"\n'
            'launchd_label = "com.example.service"\n'
            'daemon_env_var = "EXAMPLE_DAEMON"\n'
            'windows_pipe = "\\\\\\\\.\\\\pipe\\\\example"\n'
        )

        result = spec.load(path)

    assert result.name == 'example'
    assert result.launchd_label == 'com.example.service'
