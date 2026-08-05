"""The two Redis primitives the channel pipeline is built on (TASK-204).

Small enough to look obviously correct and important enough to pin anyway,
because both failures are silent and both are expensive:

* a lock that can be released by somebody who does not hold it answers one
  customer twice;
* a debounce marker that never expires answers them never.
"""

from __future__ import annotations

from app.core.locks import ConversationLock, acquire_lock, claim_once


class FakeStore:
    """Enough Redis for `SET NX EX`, `GET` and `DEL`.

    No expiry clock: every test here is about the conditional set, and a
    sleeping test that waits out a TTL proves less and takes longer. The one
    thing the TTL guarantees - that a dead worker's lock eventually frees - is
    Redis's own behaviour, not ours.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def set(
        self, name: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> object:
        if nx and name in self.values:
            return None
        self.values[name] = value
        self.ttls[name] = ex
        return True

    async def get(self, name: str) -> object:
        return self.values.get(name)

    async def delete(self, *names: str) -> int:
        removed = 0
        for name in names:
            if self.values.pop(name, None) is not None:
                self.ttls.pop(name, None)
                removed += 1
        return removed


class TestTheConversationLock:
    async def test_the_first_caller_gets_it(self) -> None:
        store = FakeStore()

        assert await acquire_lock(store, "channel:lock:abc") is not None

    async def test_the_second_caller_does_not(self) -> None:
        """RF-006. Two replies to the same customer computed at once would
        read the same history and answer the same question twice."""
        store = FakeStore()
        await acquire_lock(store, "channel:lock:abc")

        assert await acquire_lock(store, "channel:lock:abc") is None

    async def test_two_conversations_do_not_block_each_other(self) -> None:
        """Serialised per thread, parallel across them - the whole point."""
        store = FakeStore()

        assert await acquire_lock(store, "channel:lock:abc") is not None
        assert await acquire_lock(store, "channel:lock:def") is not None

    async def test_releasing_frees_it_for_the_next_worker(self) -> None:
        store = FakeStore()
        lock = await acquire_lock(store, "channel:lock:abc")
        assert lock is not None

        assert await lock.release() is True
        assert await acquire_lock(store, "channel:lock:abc") is not None

    async def test_it_carries_a_ttl_so_a_dead_worker_frees_it(self) -> None:
        """A lock that outlives the process holding it is worse than no lock:
        the conversation is silent until somebody notices."""
        store = FakeStore()
        await acquire_lock(store, "channel:lock:abc", ttl_seconds=42)

        assert store.ttls["channel:lock:abc"] == 42

    async def test_a_stale_holder_cannot_release_somebody_elses_lock(self) -> None:
        """The rare, expensive case: a turn overran the TTL, the lock expired,
        another worker took it, and the first one finishes and tidies up. The
        token is what stops it deleting a lock that is now in use - and the
        conversation being answered twice as a result."""
        store = FakeStore()
        stale = await acquire_lock(store, "channel:lock:abc")
        assert stale is not None
        await store.delete("channel:lock:abc")
        successor = await acquire_lock(store, "channel:lock:abc")
        assert successor is not None

        assert await stale.release() is False
        # The successor still holds it.
        assert await acquire_lock(store, "channel:lock:abc") is None

    async def test_a_bytes_token_is_compared_correctly(self) -> None:
        """A real Redis client answers `GET` with bytes unless it is told to
        decode. Comparing bytes to str is always False, which would silently
        turn every release into a no-op and leave locks to expire."""
        store = FakeStore()
        lock = await acquire_lock(store, "channel:lock:abc")
        assert lock is not None
        token = store.values["channel:lock:abc"]
        store.values["channel:lock:abc"] = token  # str
        raw = ConversationLock(store, "channel:lock:abc", token)

        assert await raw.release() is True


class TestTheDebounceMarker:
    async def test_the_first_message_of_a_burst_claims_it(self) -> None:
        store = FakeStore()

        assert await claim_once(store, "channel:burst:abc", ttl_seconds=8) is True

    async def test_the_rest_of_the_burst_does_not(self) -> None:
        """RF-005: five messages in eight seconds produce one answer."""
        store = FakeStore()
        await claim_once(store, "channel:burst:abc", ttl_seconds=8)

        for _ in range(4):
            assert await claim_once(store, "channel:burst:abc", ttl_seconds=8) is False

    async def test_the_window_is_the_ttl(self) -> None:
        store = FakeStore()

        await claim_once(store, "channel:burst:abc", ttl_seconds=8)

        assert store.ttls["channel:burst:abc"] == 8

    async def test_a_new_burst_can_claim_once_the_marker_is_gone(self) -> None:
        """The answering job clears the marker before it reads the transcript,
        so a message arriving from then on belongs to the next burst and can
        book its own reply."""
        store = FakeStore()
        await claim_once(store, "channel:burst:abc", ttl_seconds=8)
        await store.delete("channel:burst:abc")

        assert await claim_once(store, "channel:burst:abc", ttl_seconds=8) is True

    async def test_two_conversations_debounce_independently(self) -> None:
        store = FakeStore()

        assert await claim_once(store, "channel:burst:abc", ttl_seconds=8) is True
        assert await claim_once(store, "channel:burst:def", ttl_seconds=8) is True
