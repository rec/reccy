import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from reccy.documentation import Documentation


@pytest.mark.parametrize('init', [False, True])
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
    init: bool,
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

    assert Documentation(root=tmp_path).publish(init=init, force=force) == url

    assert build.called is rebuild
    pushes = [c.args[0] for c in run.call_args_list if c.args[0][0] == 'uv']
    assert bool(pushes) is rebuild
    if rebuild:
        message = pushes[0][pushes[0].index('--message') + 1]
        if dirty:
            assert 'Source-Commit:' not in message
        else:
            assert 'Source-Commit: current' in message


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
    assert config['site_dir'] == 'site'
    assert config['plugins']['api-autonav']['modules'] == ['example_app']


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
