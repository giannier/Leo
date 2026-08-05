"""Short-lived Redis locks and one-shot markers.

Two primitives, both `SET ... NX EX`, both used by the channel pipeline for
things a single database transaction cannot express because they span
processes and time.

**A lock**, so one conversation is answered by one worker at a time (RF-006).
Messages from different customers should be handled in parallel; two replies
to the *same* customer computed concurrently would read the same history and
answer the same question twice.

**A marker**, so a burst of messages produces one reply (RF-005). The first
message of a burst claims the marker and schedules the answer; the ones behind
it find the marker taken and schedule nothing.

Every key carries a TTL, so a worker that dies holding one blocks its
conversation for seconds rather than forever. That is the whole reason not to
use a database row for this: a lock that outlives the process holding it is
worse than no lock, and a row has no expiry.
"""

from __future__ import annotations

import uuid
from typing import Protocol

# Long enough for a slow model call plus the send, short enough that a worker
# killed mid-turn does not hold its conversation hostage. `AgentCore` gives up
# on the model well before this.
DEFAULT_LOCK_TTL_SECONDS = 90


class LockStore(Protocol):
    """The slice of Redis these primitives need.

    Narrow on purpose: it is what makes the pipeline testable with a fake
    rather than a real server, and it documents that nothing here needs
    anything cleverer than a conditional set.
    """

    async def set(
        self, name: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> object: ...

    async def get(self, name: str) -> object: ...

    async def delete(self, *names: str) -> int: ...


class ConversationLock:
    """A held lock, and the token that proves it is ours."""

    def __init__(self, store: LockStore, key: str, token: str) -> None:
        self._store = store
        self._key = key
        self._token = token

    async def release(self) -> bool:
        """Give the lock back, unless it has already expired to somebody else.

        The token check is the point. Without it a worker whose turn overran
        the TTL would delete the lock a *different* worker is now holding, and
        the conversation would be answered twice - which is the exact failure
        the lock exists to prevent, made worse by being rare.
        """
        held = await self._store.get(self._key)
        current = held.decode() if isinstance(held, bytes) else held
        if current != self._token:
            return False
        await self._store.delete(self._key)
        return True


async def acquire_lock(
    store: LockStore, key: str, *, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS
) -> ConversationLock | None:
    """Take `key`, or return None if somebody else holds it."""
    token = uuid.uuid4().hex
    taken = await store.set(key, token, ex=ttl_seconds, nx=True)
    if not taken:
        return None
    return ConversationLock(store, key, token)


async def claim_once(store: LockStore, key: str, *, ttl_seconds: int) -> bool:
    """Claim `key` for `ttl_seconds`. True only for the first caller.

    The debounce. `ttl_seconds` is the width of the window, and it is a fixed
    window from the first message rather than one that restarts on each - a
    restarting timer means somebody who keeps typing is never answered, and
    the feature's own acceptance criterion is a reply inside ten seconds.
    """
    claimed = await store.set(key, "1", ex=ttl_seconds, nx=True)
    return bool(claimed)
