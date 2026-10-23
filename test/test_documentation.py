from pathlib import Path

import pytest

from reccy.documentation import Documentation


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
