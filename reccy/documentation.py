"""Consistent Markdown guides and Python API references for sibling projects."""

import json
import subprocess
import tomllib
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel

from .errors import ReccyError


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

    def build(self, site_url: str = '') -> Path:
        """Build a static site and return its index path; warnings fail the build.

        uv installs and caches the documentation tools in an isolated environment.
        The generated site is written to site/.
        """
        root = self.root.resolve()
        with NamedTemporaryFile(
            mode='w',
            prefix='reccy-docs-',
            suffix='.yml',
            dir=root,
            delete_on_close=False,
        ) as config:
            settings = self.configuration()
            settings['site_url'] = site_url
            json.dump(settings, config)
            config.close()
            subprocess.run(
                [
                    'uv',
                    'tool',
                    'run',
                    '--with',
                    'mkdocstrings-python>=2.0.9',
                    'zensical>=0.0.70',
                    'build',
                    '--strict',
                    '--config-file',
                    config.name,
                ],
                cwd=root,
                check=True,
            )
        return root / 'site' / 'index.html'

    def publish(self, init: bool = False) -> str:
        """Build and push to origin's gh-pages branch, then return the site URL.

        Pages must already use gh-pages at its root. With init=True, a missing
        Pages site is configured after the first successful documentation push.
        Other publishing configurations are left untouched and cause an error.
        """
        root = self.root.resolve()
        remote = subprocess.check_output(
            ['git', 'remote', 'get-url', 'origin'], cwd=root, text=True
        ).strip()
        repository = subprocess.check_output(
            [
                'gh',
                'repo',
                'view',
                remote,
                '--json',
                'nameWithOwner',
                '--jq',
                '.nameWithOwner',
            ],
            cwd=root,
            text=True,
        ).strip()
        endpoint = f'repos/{repository}/pages'
        result = subprocess.run(
            ['gh', 'api', endpoint], cwd=root, capture_output=True, text=True
        )
        missing = result.returncode != 0 and '(HTTP 404)' in result.stderr
        if missing:
            if not init:
                raise ReccyError(
                    'GitHub Pages is not configured; run reccy docs --init'
                )
            owner, name = repository.split('/')
            path = '' if name.lower() == f'{owner.lower()}.github.io' else f'{name}/'
            url = f'https://{owner}.github.io/{path}'
        else:
            url = _pages_url(result)

        if init:
            landing = root / 'doc' / 'index.md'
            if not landing.exists():
                landing.parent.mkdir(parents=True, exist_ok=True)
                settings = self.configuration()
                landing.write_text(
                    f'# {settings["site_name"]}\n\n'
                    f'{settings["site_description"]}\n\n'
                    'The API Reference is generated from the Python source code.\n'
                )
            ignored = subprocess.run(
                ['git', 'check-ignore', '--quiet', '--no-index', 'site/index.html'],
                cwd=root,
            )
            if ignored.returncode == 1:
                ignore = root / '.gitignore'
                text = ignore.read_text() if ignore.exists() else ''
                ignore.write_text(f'{text.rstrip()}\n/site/\n')
            elif ignored.returncode:
                ignored.check_returncode()

        index = self.build(site_url=url)
        branch = subprocess.check_output(
            ['git', 'ls-remote', '--heads', 'origin', 'refs/heads/gh-pages'],
            cwd=root,
            text=True,
        )
        if branch:
            subprocess.run(
                [
                    'git',
                    'fetch',
                    'origin',
                    'refs/heads/gh-pages:refs/remotes/origin/gh-pages',
                ],
                cwd=root,
                check=True,
            )
        subprocess.run(
            [
                'uv',
                'tool',
                'run',
                'ghp-import',
                '--no-jekyll',
                '--push',
                str(index.parent),
            ],
            cwd=root,
            check=True,
        )
        if missing:
            result = subprocess.run(
                [
                    'gh',
                    'api',
                    '--method',
                    'POST',
                    endpoint,
                    '-f',
                    'build_type=legacy',
                    '-f',
                    'source[branch]=gh-pages',
                    '-f',
                    'source[path]=/',
                ],
                cwd=root,
                text=True,
                capture_output=True,
            )
            if result.returncode and '(HTTP 409)' in result.stderr:
                result = subprocess.run(
                    ['gh', 'api', endpoint], cwd=root, text=True, capture_output=True
                )
            url = _pages_url(result)
        print(f'Documentation pushed: {url}')
        return url


def _pages_url(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode:
        raise ReccyError(result.stderr.strip())
    site = json.loads(result.stdout)
    if site.get('build_type') != 'legacy' or site.get('source') != {
        'branch': 'gh-pages',
        'path': '/',
    }:
        raise ReccyError('GitHub Pages must publish from gh-pages at /')
    return site['html_url']
