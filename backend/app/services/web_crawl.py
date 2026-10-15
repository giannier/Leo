"""Reading the business's own website (Feature 006, TASK-603, HU-02).

Breadth-first from the address the owner typed, same site only (the host with or
without `www.`), HTML only, at most `knowledge_web_max_pages` pages, one at a
time, honouring `robots.txt`. What comes back is plain text: scripts, styles and
navigation are dropped, headings and paragraphs keep their breaks, and a line that
repeats on most pages (a menu, a footer) is kept once, on the first page.

Static HTML only (006 C-001): a site that builds its content with JavaScript reads
as empty, and the source fails with a message that says to paste the text or
upload a PDF instead. Nothing re-reads a site on its own (C-002): the owner presses
"Actualizar desde mi web".

The network rules (public addresses only, byte cap, timeout) are the fetcher's
(`app/providers/web/`); this module never opens a connection.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.providers.web.base import ROBOTS_AGENT, BlockedAddressError, WebFetcher, WebFetchError

logger = logging.getLogger(__name__)

HTML_TYPES = ("text/html", "application/xhtml+xml")
# Links that are certainly not pages.
SKIPPED_EXTENSIONS = re.compile(
    r"\.(pdf|jpe?g|png|gif|webp|svg|ico|css|js|json|xml|zip|rar|mp4|mp3|webm|docx?|xlsx?|pptx?)$",
    re.IGNORECASE,
)
# Elements whose text is never content.
SKIPPED_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "button", "form"}
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header", "footer", "aside", "li", "ul", "ol",
    "table", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "blockquote",
    "dd", "dt", "dl", "address", "figcaption", "pre",
}  # fmt: skip
# A line on at least this share of the pages (and at least 3 pages) is boilerplate.
REPEATED_SHARE = 0.5
MIN_LINE_CHARS = 3

EMPTY_SITE = (
    "No pudimos leer tu web: no encontramos texto en ella. Puede que su contenido se arme con "
    "JavaScript, que hoy no leemos. Pega el contenido como texto o súbelo en PDF."
)


class InvalidSiteUrlError(ValueError):
    """The address is not a website Leo will read."""


@dataclass
class CrawledPage:
    url: str
    title: str
    lines: list[str]


@dataclass
class CrawlResult:
    pages: list[CrawledPage] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    robots: bool = False
    error: str | None = None

    def text(self) -> str:
        """Every page as a section: its title, its address, its text."""
        sections = []
        for page in self.pages:
            if page.lines:
                sections.append(f"# {page.title}\n{page.url}\n\n" + "\n".join(page.lines))
        return "\n\n".join(sections)

    def report(self) -> dict[str, Any]:
        return {
            "web": {
                "paginas": [page.url for page in self.pages if page.lines],
                "omitidas": self.skipped[:50],
                "robots_txt": self.robots,
            }
        }


def normalise_start(url: str) -> str:
    """The address the owner typed, as an absolute http(s) URL, or `InvalidSiteUrlError`."""
    raw = url.strip()
    if not raw:
        raise InvalidSiteUrlError("escribe la dirección de tu web")
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise InvalidSiteUrlError("la dirección debe empezar con http:// o https://")
    host = (parts.hostname or "").lower()
    if "." not in host or host.endswith(".") or host.replace(".", "").isdigit():
        raise InvalidSiteUrlError("escribe el dominio de tu web, por ejemplo mitienda.pe")
    if parts.username or parts.password:
        raise InvalidSiteUrlError("la dirección no puede llevar usuario ni clave")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _site(host: str | None) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


class _TextExtractor(HTMLParser):
    """Visible text with block breaks, the title, and the links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._buffer: list[str] = []
        self._skipping = 0
        self._in_title = False
        self.noindex = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED_TAGS:
            self._skipping += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag == "meta":
            values = dict(attrs)
            if (values.get("name") or "").lower() == "robots" and "noindex" in (
                values.get("content") or ""
            ).lower():
                self.noindex = True
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS and self._skipping:
            self._skipping -= 1
        elif tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skipping:
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        line = " ".join("".join(self._buffer).split())
        self._buffer = []
        if len(line) >= MIN_LINE_CHARS:
            self.lines.append(line)


def _decode(body: bytes, charset: str | None) -> str:
    for encoding in (charset, "utf-8"):
        if encoding:
            try:
                return body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
    return body.decode("latin-1", errors="replace")


async def _robots(fetcher: WebFetcher, start: str) -> tuple[RobotFileParser | None, bool]:
    """The site's robots.txt, `(None, False)` when there is none.

    A missing file (4xx) allows everything. A file the server fails to give (5xx)
    allows nothing: when in doubt, the site said no. A site that cannot be reached
    at all raises: that is "your address does not work", not a robots rule.
    """
    parts = urlsplit(start)
    address = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    parser = RobotFileParser()
    page = await fetcher.fetch(address)
    if page.status >= 500:
        return _deny_all(parser), True
    if page.status >= 400 or not page.body:
        return None, False
    parser.parse(_decode(page.body, page.charset).splitlines())
    parser.modified()  # `can_fetch` answers False for a parser it thinks was never read
    return parser, True


def _deny_all(parser: RobotFileParser) -> RobotFileParser:
    parser.parse(["User-agent: *", "Disallow: /"])
    parser.modified()
    return parser


async def crawl(fetcher: WebFetcher, start_url: str, *, max_pages: int) -> CrawlResult:
    """Read up to `max_pages` pages of the site at `start_url`."""
    start = normalise_start(start_url)
    site = _site(urlsplit(start).hostname)
    result = CrawlResult()
    try:
        robots, result.robots = await _robots(fetcher, start)
    except BlockedAddressError as exc:
        result.error = f"No podemos leer esa dirección: {exc}."
        return result
    except WebFetchError as exc:
        result.error = f"No pudimos abrir tu web: {exc}. Revisa la dirección."
        return result

    queue = [start]
    seen = {start}
    while queue and len(result.pages) < max_pages:
        url = queue.pop(0)
        if robots is not None and not robots.can_fetch(ROBOTS_AGENT, url):
            result.skipped.append({"url": url, "motivo": "robots.txt"})
            continue
        try:
            page = await fetcher.fetch(url)
        except BlockedAddressError as exc:
            result.skipped.append({"url": url, "motivo": str(exc)})
            if url == start:
                result.error = f"No podemos leer esa dirección: {exc}."
                return result
            continue
        except WebFetchError as exc:
            result.skipped.append({"url": url, "motivo": str(exc)})
            continue
        if page.status >= 400:
            result.skipped.append({"url": url, "motivo": f"respondió {page.status}"})
            continue
        if page.content_type not in HTML_TYPES:
            result.skipped.append({"url": url, "motivo": "no es una página"})
            continue
        if _site(urlsplit(page.url).hostname) != site:
            result.skipped.append({"url": url, "motivo": "lleva a otro sitio"})
            continue

        parser = _TextExtractor()
        parser.feed(_decode(page.body, page.charset))
        parser.close()
        if parser.noindex:
            result.skipped.append({"url": url, "motivo": "la página pide no indexarse"})
        else:
            title = " ".join(parser.title.split()) or urlsplit(page.url).path or page.url
            result.pages.append(CrawledPage(url=page.url, title=title, lines=parser.lines))

        for href in parser.links:
            link, _ = urldefrag(urljoin(page.url, href))
            parts = urlsplit(link)
            if (
                parts.scheme in ("http", "https")
                and _site(parts.hostname) == site
                and not SKIPPED_EXTENSIONS.search(parts.path)
                and link not in seen
            ):
                seen.add(link)
                queue.append(link)

    _drop_repeated_lines(result.pages)
    if not result.pages and result.skipped and result.skipped[0]["url"] == start:
        reason = result.skipped[0]["motivo"]
        result.error = (
            "Tu web pide que no la lean robots como el nuestro (robots.txt)."
            if reason == "robots.txt"
            else f"No pudimos leer tu web: {reason}."
        )
    return result


def _drop_repeated_lines(pages: list[CrawledPage]) -> None:
    """Keep a line that is on most pages (a menu, a footer) only on the first one."""
    if len(pages) < 3:
        return
    counts = Counter(line for page in pages for line in set(page.lines))
    repeated = {line for line, n in counts.items() if n >= 3 and n / len(pages) >= REPEATED_SHARE}
    kept: set[str] = set()
    for page in pages:
        lines = []
        for line in page.lines:
            if line in repeated:
                if line in kept:
                    continue
                kept.add(line)
            lines.append(line)
        page.lines = lines
