# Regression testing sample configurations

Install the test dependencies in the project that owns your Pydantic settings:

```bash
uv add --dev reccy pytest-regressions
```

Enable reccy's fixtures in your top-level `conftest.py`:

```python
pytest_plugins = ['reccy.pytest_plugin']
```

Pass your configuration type to `sample_config_regression` once per test:

```python
from reccy.pytest_plugin import SampleConfigRegression

from my_app.settings import Settings


def test_sample_config(sample_config_regression: SampleConfigRegression) -> None:
    sample_config_regression(Settings)
```

Run `uv run pytest test/test_settings.py::test_sample_config`. Starting from the
test file's directory, the fixture searches upward for the first directory
containing a `.git/` or `doc/` directory. It creates `doc/` if necessary and uses
`doc/sample-config.toml` as the reference. No project marker is an error.

The first run creates the reference and fails so you can review it. Commit the
TOML file and rerun the test. Later runs compare the complete generated TOML,
including parameter comments and spacing, against that file.

To choose another location, pass `path`:

```python
from pathlib import Path

sample_config_regression(
    Settings,
    path=Path(__file__).parents[1] / 'examples' / 'config.toml',
)
```

The chosen path is always the reference location; pytest-regressions' per-test
reference path is never used. Relative paths are relative to the working
directory. Use distinct explicit paths when testing several configuration types
in the same project.

For an intentional change, run the same test with `--force-regen`, review the TOML
diff, and commit the updated reference. Regeneration reports a failure; rerun
without the flag to verify the new reference. See the
[pytest-regressions guide](https://pytest-regressions.readthedocs.io/en/latest/overview.html).

The fixture generates into a temporary directory and returns the TOML text for
optional parsed-value assertions. Your project owns the reference files. Field
docstrings and source comments require the original Python source to be available;
class-level documentation is excluded. Generation errors fail the test before
the regression comparison.
