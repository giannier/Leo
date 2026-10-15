"""In-memory `WebFetcher` for tests: a dict of URL -> page."""

from __future__ import annotations

from app.providers.web.base import FetchedPage, WebFetchError


class MockWebFetcher:
    def __init__(self, pages: dict[str, tuple[int, str, str]] | None = None) -> None:
        # url -> (status, content type, body)
        self.pages = dict(pages or {})
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.calls.append(url)
        if url not in self.pages:
            return FetchedPage(url=url, status=404, content_type="text/html", body=b"")
        status, content_type, body = self.pages[url]
        if status < 0:
            raise WebFetchError(body)
        return FetchedPage(url=url, status=status, content_type=content_type, body=body.encode())
