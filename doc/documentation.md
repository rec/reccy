# Building documentation

Run from the project root:

```sh
uv run --extra docs python -m reccy.documentation
```

The generated site starts at `site/index.html`. Builds fail on warnings. Generated
files are ignored by Git. No server or application is started.

Zensical renders the Markdown guides in `doc/`. mkdocstrings generates an API
reference and navigation from the package's Python source files. Public classes,
functions and fields appear with their signatures and type hints, even when they
have no docstrings. Private names are excluded. Source analysis does not import
application modules, so it does not require their runtime or hardware setup.
Docstrings supply the explanations; generation does not infer behavior.

## Shared project layout

The shared API is `reccy.documentation.Documentation`. Projects use this layout:

```text
pyproject.toml
README.md
package_name/
doc/
    index.md
    other-guides.md
```

The package directory is the `[project].name` from `pyproject.toml`, with hyphens
replaced by underscores. `doc/index.md` is the site's Markdown landing page.
`README.md` remains the repository introduction.

For a sibling that already depends on reccy, add a documentation extra containing
`reccy[docs]`. The same build command then works from that project's root. Keep
the existing reccy source declaration when using an editable or Git dependency.
Project-specific guides stay in `doc/`; the shared API supplies the theme, search,
API navigation and rendering settings. No per-project Zensical configuration is
required.

Python callers can build another project explicitly:

```python
from pathlib import Path

from reccy.documentation import Documentation

index = Documentation(root=Path('/path/to/project')).build()
```

`configuration()` returns the Zensical configuration as a dictionary for callers
that need to inspect it. Documentation dependencies are optional and are not
installed for normal reccy consumers.

## Language scope

This API generates Python references. Markdown guides can describe Rust or C++,
but this setup does not extract their APIs. Rust API references still need
rustdoc; C++ API references need a suitable extractor such as Doxygen.
