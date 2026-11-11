import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from reccy.documentation import Documentation


@pytest.mark.parametrize(
    'published, dirty, force, rebuild',
    [
        ('current', '', False, False),
        ('older', '', False, True),
        ('', '', False, True),
        ('current', ' M source.py', False, True),
        ('current', '?? new.py', False, True),
        ('current', '', True, True),
    ],
)
def test_publication_skips_only_matching_clean_commits(
    published: str,
    dirty: str,
    force: bool,
    rebuild: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    landing = tmp_path / 'doc' / 'index.md'
    landing.parent.mkdir()
    landing.write_text('# Example\n')
    outputs = {
        'remote': 'git@github.com:rec/example.git',
        'ls-remote': 'published refs/heads/gh-pages',
        'status': dirty,
        'rev-parse': 'current',
        'log': published,
    }

    def output(command: list[str], **kwargs: object) -> str:
        return 'rec/example' if command[0] == 'gh' else outputs[command[1]]

    url = 'https://rec.github.io/example/'
    result = subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps(
            {
                'build_type': 'legacy',
                'source': {'branch': 'gh-pages', 'path': '/'},
                'html_url': url,
            }
        ),
        stderr='',
    )
    run = Mock(return_value=result)
    build = Mock(return_value=tmp_path / 'site' / 'index.html')
    monkeypatch.setattr(subprocess, 'check_output', output)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(Documentation, 'build', build)

    assert Documentation(root=tmp_path).publish(force=force) == url

    assert build.called is rebuild
    assert not any('--method' in c.args[0] for c in run.call_args_list)
    pushes = [c.args[0] for c in run.call_args_list if c.args[0][0] == 'uv']
    assert bool(pushes) is rebuild
    if rebuild:
        message = pushes[0][pushes[0].index('--message') + 1]
        if dirty:
            assert 'Source-Commit:' not in message
        else:
            assert 'Source-Commit: current' in message


@pytest.mark.parametrize('build_fails', [False, True])
def test_publication_enables_missing_pages_only_after_a_successful_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_fails: bool
) -> None:
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "example"\n')
    url = 'https://rec.github.io/example/'
    missing = subprocess.CompletedProcess([], 1, stdout='', stderr='(HTTP 404)')
    site = subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps(
            {
                'build_type': 'legacy',
                'source': {'branch': 'gh-pages', 'path': '/'},
                'html_url': url,
            }
        ),
        stderr='',
    )
    run = Mock(side_effect=[missing, site, site, site])
    build = Mock(return_value=tmp_path / 'site' / 'index.html')
    if build_fails:
        build.side_effect = subprocess.CalledProcessError(1, ['uv'])

    def output(command: list[str], **kwargs: object) -> str:
        return 'rec/example' if command[0] == 'gh' else ''

    monkeypatch.setattr(subprocess, 'check_output', output)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(Documentation, 'build', build)

    if build_fails:
        with pytest.raises(subprocess.CalledProcessError):
            Documentation(root=tmp_path).publish()
        assert run.call_count == 1
    else:
        assert Documentation(root=tmp_path).publish() == url
        commands = [c.args[0] for c in run.call_args_list]
        assert commands[1][0] == 'uv'
        assert '--push' in commands[1]
        assert commands[2][3] == 'POST'
    build.assert_called_once_with(site_url=url)
    assert (tmp_path / 'doc' / 'index.md').is_file()


@pytest.mark.parametrize('existing_landing', [False, True])
def test_local_build_initializes_files_without_git_and_preserves_existing_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_landing: bool
) -> None:
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname = "example"\ndescription = "Project description"\n'
    )
    landing = tmp_path / 'doc' / 'index.md'
    if existing_landing:
        landing.parent.mkdir()
        landing.write_text('Custom landing page.\n')
    ignore = tmp_path / '.gitignore'
    ignore.write_text('existing-rule')
    index = tmp_path / '.cache' / 'reccy-docs' / 'site' / 'index.html'
    index.parent.mkdir(parents=True)
    index.write_text('<article><p>Guide.</p></article>')
    run = Mock()
    monkeypatch.setattr(subprocess, 'run', run)

    documentation = Documentation(root=tmp_path)
    assert documentation.build() == index
    assert documentation.build() == index

    if existing_landing:
        assert landing.read_text() == 'Custom landing page.\n'
    else:
        assert landing.read_text().startswith('# example\n')
        assert 'Project description' in landing.read_text()
    assert ignore.read_text().startswith('existing-rule\n')
    assert ignore.read_text().count('/.cache/reccy-docs/\n') == 1
    assert all(c.args[0][0] == 'uv' for c in run.call_args_list)


def test_project_metadata_selects_the_package_not_the_checkout_name(
    tmp_path: Path,
) -> None:
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname = "example-app"\ndescription = "Example documentation"\n'
    )
    config = Documentation(root=tmp_path).configuration()

    assert config['site_name'] == 'example-app'
    assert config['site_description'] == 'Example documentation'
    assert config['docs_dir'] == 'doc'
    assert config['site_dir'] == '.cache/reccy-docs/site'
    assert config['plugins']['mkdocs-autoapi']['autoapi_dir'] == 'example_app'


def test_relative_root_resolves_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "example"\n')
    monkeypatch.chdir(tmp_path)

    config = Documentation().configuration()

    assert config['site_description'] == ''
    assert config['plugins']['mkdocstrings']['handlers']['python']['paths'] == [
        str(tmp_path)
    ]
    assert (
        config['plugins']['mkdocstrings']['handlers']['python']['options'][
            'allow_inspection'
        ]
        is False
    )


def test_project_documentation_settings_are_applied(
    tmp_path: Path,
) -> None:
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname = "example"\n'
        '[tool.reccy.documentation]\n'
        'use_directory_urls = false\n'
        'extensions = ["scripts/griffe_example.py"]\n'
    )

    config = Documentation(root=tmp_path).configuration()

    assert config['use_directory_urls'] is False
    assert config['plugins']['mkdocstrings']['handlers']['python']['options'][
        'extensions'
    ] == ['scripts/griffe_example.py']


@pytest.mark.parametrize('valid', [False, True])
def test_build_checks_generated_pages_before_returning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "example"\n')
    index = tmp_path / '.cache' / 'reccy-docs' / 'site' / 'index.html'
    index.parent.mkdir(parents=True)
    index.write_text(
        '<article><p>Guide.</p><a href="/example/client.html">Client</a></article>'
        if valid
        else '<article><h1>Empty</h1></article>'
    )
    (index.parent / 'client.html').write_text('<article><p>Client API.</p></article>')
    monkeypatch.setattr(subprocess, 'run', Mock())

    if valid:
        assert (
            Documentation(root=tmp_path).build('https://rec.github.io/example/')
            == index
        )
    else:
        with pytest.raises(ValueError, match='Blank documentation page'):
            Documentation(root=tmp_path).build()


def test_build_checks_the_configured_url_style(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname = "example"\n'
        '[tool.reccy.documentation]\nuse_directory_urls = false\n'
    )
    root = tmp_path / '.cache' / 'reccy-docs' / 'site'
    (root / 'guide').mkdir(parents=True)
    (root / 'index.html').write_text(
        '<article><p>Guide.</p><a href="guide/">Guide</a></article>'
    )
    (root / 'guide' / 'index.html').write_text('<article><p>Guide.</p></article>')
    monkeypatch.setattr(subprocess, 'run', Mock())

    with pytest.raises(ValueError, match='Directory documentation link'):
        Documentation(root=tmp_path).build()
