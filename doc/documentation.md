# Publishing documentation

Run from the project root:

```sh
uv run reccy docs
```

This builds the site at `site/index.html` and pushes it to the repository's
`gh-pages` branch. GitHub Pages then deploys that branch. The command requires
`uv`, `git`, and an authenticated `gh`, with permission to push to `origin`.
Builds fail on warnings, and failed builds are not pushed. Successful completion
confirms the push was accepted; GitHub's deployment happens afterward.

When the working tree is clean and the current commit has already been published,
the command skips generation and publication. The source commit is recorded in
the `gh-pages` commit message. Publications with uncommitted changes do not record
a source commit, so they cannot cause a later clean checkout to be skipped.

To rebuild regardless, including after documentation-tool updates, run:

```sh
uv run reccy docs --force
```

`-f` is the short form of `--force`, and can also be combined with `--init`.

GitHub Pages must already be configured to publish the root of `gh-pages`.
Otherwise the command fails. For a repository without a Pages site, run:

```sh
uv run reccy docs --init
```

Initialization creates a missing `doc/index.md`, adds `site/` to `.gitignore` if
needed, builds and pushes the documentation, and enables GitHub Pages. Commit any
new landing page or ignore-file changes before releasing. Existing Pages settings
that use a different branch, directory, or workflow cause an error and are left
untouched, including when `--init` is supplied.

Generated HTML is committed only on `gh-pages`, keeping it out of source commits.
Publishing never switches the current branch and does not force-push.
No documentation builds are added to routine CI. `reccy release` publishes docs
before continuing with a release and stops if documentation publication fails.

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

Installing reccy, directly or as another project's dependency, installs the
`reccy` executable in that Python environment. Existing consumers must update
their reccy installation to receive the entry point. For a project already
tracking reccy's Git `main` branch, refresh its locked revision with:

```sh
uv sync --upgrade-package reccy
```

`uv add --dev reccy` can reuse an older revision already in `uv.lock`, including
one installed transitively. A `main` source declaration does not automatically
advance that locked revision. If `uv run reccy` reports that the executable does
not exist, refresh the dependency before retrying.

The same commands then work
from each project's root using `uv run reccy`, or just `reccy` with its environment
activated. No documentation extra is needed: `uv` fetches and caches the tools in
isolated environments only when documentation is built or published.

Project-specific guides stay in `doc/`; the shared API supplies the theme, search,
API navigation and rendering settings. No per-project Zensical configuration is
required. `--init` supplies the landing page when starting from scratch.

Python callers can build locally without publishing:

```python
from pathlib import Path

from reccy.documentation import Documentation

index = Documentation(root=Path('/path/to/project')).build()
```

`configuration()` returns the Zensical configuration as a dictionary.
`publish(init=False, force=False)` publishes documentation as needed, returning
the Pages URL. Documentation tools are not installed into normal consumer environments.

## Language scope

This API generates Python references. Markdown guides can describe Rust or C++,
but this setup does not extract their APIs. Rust API references still need
rustdoc; C++ API references need a suitable extractor such as Doxygen.
