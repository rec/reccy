from pathlib import Path

import pytest

from reccy.documentation_pages import check_pages


def test_checks_reachable_pages_without_fetching_external_urls(tmp_path: Path) -> None:
    index = tmp_path / 'index.html'
    index.write_text(
        '<article><p>Usage guide.</p><a href="client.html">Client</a>'
        '<a href="https://example.invalid/">External</a>'
        '<a href="mailto:example@example.invalid">Email</a>'
        '<a href="missing.png">Image</a><a href="#section">Section</a></article>'
    )
    (tmp_path / 'client.html').write_text(
        '<article><p>Client API.</p><a href="index.html">Home</a></article>'
    )
    (tmp_path / 'unreachable.html').write_text('<article><h1>Unused</h1></article>')

    assert check_pages(index) == 2


@pytest.mark.parametrize('hidden', ['', '<script>run()</script>', '<style>p{}</style>'])
def test_rejects_heading_only_pages_with_invisible_content(
    tmp_path: Path, hidden: str
) -> None:
    index = tmp_path / 'index.html'
    index.write_text('<article><p>Guide.</p><a href="empty.html">API</a></article>')
    (tmp_path / 'empty.html').write_text(
        f'<nav><a href="index.html">Home</a></nav>'
        f'<article><h1>Index</h1>{hidden}</article>'
    )

    with pytest.raises(ValueError, match='Blank documentation page: empty.html'):
        check_pages(index)


@pytest.mark.parametrize('link', ['missing.html', '../outside.html', '/guide.html'])
def test_rejects_missing_pages_and_links_outside_the_site(
    tmp_path: Path, link: str
) -> None:
    index = tmp_path / 'index.html'
    index.write_text(f'<article><p>Guide.</p><a href="{link}">API</a></article>')
    (tmp_path / 'guide.html').write_text('<article><p>Guide.</p></article>')

    with pytest.raises(ValueError, match='Broken documentation link'):
        check_pages(index, site_url='https://rec.github.io/example/')


@pytest.mark.parametrize(
    'link',
    [
        'guide%20name.html',
        '/example/guide%20name.html',
        'https://rec.github.io/example/guide%20name.html',
    ],
)
def test_resolves_links_using_the_site_prefix(tmp_path: Path, link: str) -> None:
    index = tmp_path / 'index.html'
    index.write_text(f'<article><p>Guide.</p><a href="{link}">API</a></article>')
    (tmp_path / 'guide name.html').write_text(
        '<article><p>Guide.</p><a href="index.html">Home</a></article>'
    )

    assert check_pages(index, site_url='https://rec.github.io/example/') == 2


@pytest.mark.parametrize('use_directory_urls', [False, True])
def test_directory_links_follow_the_configured_url_style(
    tmp_path: Path, use_directory_urls: bool
) -> None:
    index = tmp_path / 'index.html'
    index.write_text('<article><p>Guide.</p><a href="guide/">API</a></article>')
    (tmp_path / 'guide').mkdir()
    (tmp_path / 'guide' / 'index.html').write_text(
        '<article><p>Guide.</p><a href="../">Home</a></article>'
    )

    if use_directory_urls:
        assert check_pages(index) == 2
    else:
        with pytest.raises(ValueError, match='Directory documentation link'):
            check_pages(index, use_directory_urls=False)


@pytest.mark.parametrize('name', ['index.html', 'package.html'])
def test_allows_heading_only_indexes_with_links_to_child_pages(
    tmp_path: Path, name: str
) -> None:
    index = tmp_path / 'index.html'
    package = tmp_path / 'reference' / name
    child = package.parent if name == 'index.html' else package.with_suffix('')
    child.mkdir(parents=True)
    index.write_text(
        f'<article><p>Guide.</p><a href="reference/{name}">API</a></article>'
    )
    link = (child / 'client.html').relative_to(package.parent).as_posix()
    package.write_text(
        f'<nav><a href="{link}">Client</a></nav><article><h1>Package</h1></article>'
    )
    (child / 'client.html').write_text('<article><p>Client API.</p></article>')

    assert check_pages(index) == 3
