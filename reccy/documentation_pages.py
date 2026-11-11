"""Validate generated documentation without fetching URLs or running a server."""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


def check_pages(
    index: Path, site_url: str = '', use_directory_urls: bool = True
) -> int:
    """Check reachable HTML pages for broken links and missing article content.

    Resolve links as a hosted site, including site_url's path prefix. External
    URLs, assets, and fragment targets are not checked. With directory URLs off,
    root-relative page links are rejected because they fail under file://.
    Heading-only indexes are allowed when they link to child pages.
    This does not evaluate CSS or scripts.
    Return the number of pages checked.
    """
    root = index.resolve().parent
    base = (site_url or 'https://reccy.invalid/').rstrip('/') + '/'
    origin = urlsplit(base)
    prefix = unquote(origin.path)
    pending = [index.resolve()]
    seen: set[Path] = set()
    while pending:
        page = pending.pop()
        if page in seen:
            continue
        parsed = DocumentationPage()
        parsed.feed(page.read_text())
        seen.add(page)
        targets: list[Path] = []
        current = urljoin(base, page.relative_to(root).as_posix())
        for link in parsed.links:
            source = urlsplit(link)
            if not source.path:
                continue
            url = urlsplit(urljoin(current, link))
            if (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
                continue
            path = unquote(url.path)
            if Path(path).suffix not in ('', '.html'):
                continue
            if (
                not use_directory_urls
                and not source.scheme
                and not source.netloc
                and source.path.startswith('/')
            ):
                raise ValueError(f'Root-relative documentation link in {page}: {link}')
            target = (root / path.removeprefix(prefix)).resolve()
            if not path.startswith(prefix) or not target.is_relative_to(root):
                raise ValueError(f'Broken documentation link in {page}: {link}')
            if target.is_dir():
                if not use_directory_urls:
                    raise ValueError(f'Directory documentation link in {page}: {link}')
                target /= 'index.html'
            if not target.is_file():
                raise ValueError(f'Broken documentation link in {page}: {link}')
            targets.append(target)
        children = page.parent if page.name == 'index.html' else page.with_suffix('')
        if not parsed.content and not any(
            t != page and t.is_relative_to(children) for t in targets
        ):
            raise ValueError(f'Blank documentation page: {page.relative_to(root)}')
        pending.extend(targets)
    return len(seen)


class DocumentationPage(HTMLParser):
    """Collect page links and article text outside headings, scripts, and styles."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.content: list[str] = []
        self._article = False
        self._heading = False
        self._hidden: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ('script', 'style'):
            self._hidden = tag
        if tag == 'a' and (href := dict(attrs).get('href')):
            self.links.append(href)
        if tag == 'article':
            self._article = True
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._heading = True

    def handle_endtag(self, tag: str) -> None:
        if tag == self._hidden:
            self._hidden = None
        if tag == 'article':
            self._article = False
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._heading = False

    def handle_data(self, data: str) -> None:
        if self._article and not self._heading and not self._hidden and data.strip():
            self.content.append(data.strip())
