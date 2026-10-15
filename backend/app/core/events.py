"""Real-time events of the inbox (Feature 007, TASK-702).

Whatever changes a conversation publishes a small event on the tenant's Redis
channel; the WebSocket endpoint forwards it to the panels of that business, which
refetch what they show. The event carries ids and a kind, never content: a panel
that receives it reads the data through the API, under its own permissions.

Publishing is **best effort and after the commit**: a lost event costs a refresh
(the panel re-syncs on reconnect and polls as a fallback), and an event about a
change that then rolled back would make every panel show something that is not
there. A Redis that is down never fails the request that changed something.

The API's handlers are synchronous, so this uses a synchronous client of its own.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from functools import cache
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

Publisher = Callable[[str, str], Any]


def channel(tenant_id: uuid.UUID) -> str:
    return f"inbox:{tenant_id}"


@cache
def _client() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_timeout=1, socket_connect_timeout=1)


def _redis_publish(name: str, data: str) -> Any:
    return _client().publish(name, data)


_publisher: Publisher = _redis_publish


def set_publisher(publisher: Publisher | None) -> None:
    """Replace the transport (tests record instead of publishing). None restores Redis."""
    global _publisher
    _publisher = publisher or _redis_publish


def publish(tenant_id: uuid.UUID, kind: str, **data: Any) -> None:
    """Tell the tenant's panels that something changed. Never raises."""
    event = {
        "type": kind,
        **{k: str(v) if isinstance(v, uuid.UUID) else v for k, v in data.items()},
    }
    try:
        _publisher(channel(tenant_id), json.dumps(event, default=str))
    except (RedisError, OSError):
        logger.warning(
            "inbox event not published", extra={"tenant_id": str(tenant_id), "type": kind}
        )
