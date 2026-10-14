"""Website reading port and adapters (Feature 006)."""

from app.providers.web.base import (
    ROBOTS_AGENT,
    USER_AGENT,
    BlockedAddressError,
    FetchedPage,
    WebFetcher,
    WebFetchError,
)
from app.providers.web.httpx_fetcher import HttpxWebFetcher
from app.providers.web.mock import MockWebFetcher

__all__ = [
    "ROBOTS_AGENT",
    "USER_AGENT",
    "BlockedAddressError",
    "FetchedPage",
    "HttpxWebFetcher",
    "MockWebFetcher",
    "WebFetchError",
    "WebFetcher",
]
