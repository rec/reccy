"""Consistent Markdown guides and Python API references for sibling projects."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from tempfile import NamedTemporaryFile

import tyro
from pydantic import BaseModel


class Documentation(BaseModel, frozen=True):
    """Build documentation for a project with a same-named package and doc/."""

    root: Path = Path('.')
    """Project directory containing pyproject.toml and doc/index.md."""

    def configuration(self) -> dict[str, object]:
        """Return shared Zensical settings derived from project metadata.

        Python modules are parsed without importing them. Public members without
        docstrings are included so the reference also exposes undocumented APIs.
        """
        root = self.root.resolve()
        with (root / 'pyproject.toml').open('rb') as file:
            project = tomllib.load(file)['project']
        package = project['name'].replace('-', '_')
        return {
            'site_name': project['name'],
            'site_description': project.get('description', ''),
            'docs_dir': 'doc',
            'site_dir': 'site',
            'theme': {
                'name': 'material',
                'features': ['navigation.indexes', 'content.code.copy'],
            },
            'plugins': {
                'search': {},
                'api-autonav': {'modules': [package]},
                'mkdocstrings': {
                    'handlers': {
                        'python': {
                            'paths': [str(root)],
                            'options': {
                                'allow_inspection': False,
                                'show_if_no_docstring': True,
                                'show_signature_annotations': True,
                                'members_order': 'source',
                                'filters': ['!^_'],
                            },
                        },
                    },
                },
            },
        }

    def build(self) -> Path:
        """Build a static site and return its index path; warnings fail the build.

        Install reccy's ``docs`` extra in the calling project's environment.
        Only the temporary configuration and generated site are written.
        """
        root = self.root.resolve()
        with NamedTemporaryFile(
            mode='w',
            prefix='reccy-docs-',
            suffix='.yml',
            dir=root,
            delete_on_close=False,
        ) as config:
            json.dump(self.configuration(), config)
            config.close()
            subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'zensical',
                    'build',
                    '--strict',
                    '--config-file',
                    config.name,
                ],
                cwd=root,
                check=True,
            )
        return root / 'site' / 'index.html'


if __name__ == '__main__':
    tyro.cli(Documentation).build()
