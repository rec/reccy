# Releasing a project

From a clean checkout on `main`, run:

```sh
uv run reccy release patch
```

The version part can be `patch`, `minor`, or `major`; omitting it selects `patch`.
The command uses the release procedure originally provided by
`dotfiles/bin/release.py`:

1. Require a clean working tree on `main`.
2. Build and push documentation using the same operation as `reccy docs`.
3. Preview the new version and reject existing local or remote tags.
4. Run pytest, Ruff, ty, pyupgrade, and the whitespace check. Stop if verification
   changes tracked or untracked source files.
5. Build the application with PyInstaller when its installation entry point
   exists, including the existing platform-specific packaging and signing steps.
6. Bump the version, commit it, push it, create and push its `v` tag, and create
   a GitHub release with generated notes.
7. Clear the existing `dist/` contents, as the original release script does.

For ordinary Python packages, versioning uses `uv version`, `pyproject.toml`, and
`uv.lock`. Projects with a dynamic version must use maturin; their Cargo version
and workspace lock are updated instead.

The existing optional PyPI publication behavior is preserved:

```sh
uv run reccy release minor --publish
```

This also runs `uv build` and `uv publish` after creating the GitHub release and
clearing `dist/`. Package-registry credentials must already be configured.

Documentation publication is required. If Pages is missing, run
`reccy docs --init` separately and commit any initialization files before retrying
the release. Any documentation build, push, or Pages-setup error stops the command
before version changes, release commits, tags, or GitHub releases. GitHub's Pages
deployment runs asynchronously after the documentation push.
