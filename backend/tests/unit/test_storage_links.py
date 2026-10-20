"""Signed links to stored files: what makes one valid, and everything that makes it not.

The endpoint these links open is public, so this module is the whole of its
access control. The tests are written from the attacker's side: what do you have
to hold to read a file, and what stops working the moment any part of it is wrong.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.providers.storage import links

KEY = "tenants/6f1c2d3e-0000-4000-8000-000000000001/media/photo.png"
OTHER_KEY = "tenants/6f1c2d3e-0000-4000-8000-000000000002/media/photo.png"
SECRET = links.derive_secret("a-secret-key-that-is-long-enough-for-tests")
NOW = 1_800_000_000.0


def _forge(payload: str, secret: bytes = SECRET, version: str = "v1") -> str:
    """A token with a correct signature over an arbitrary payload, as the
    signing key's holder could make it."""
    encoded = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    mac = hmac.new(secret, f"{version}.{encoded}".encode(), hashlib.sha256).digest()
    return f"{version}.{encoded}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"


class TestAGenuineLink:
    def test_it_verifies_and_names_the_file(self) -> None:
        token, expires_at = links.sign(KEY, 600, secret=SECRET, now=NOW)

        verified = links.verify(token, secret=SECRET, now=NOW + 1)

        assert verified == links.VerifiedLink(key=KEY, expires_at=expires_at)

    def test_it_is_a_single_url_safe_string(self) -> None:
        token, _ = links.sign(KEY, 600, secret=SECRET, now=NOW)

        assert token.startswith("v1.")
        assert all(c.isalnum() or c in "._-" for c in token)

    def test_a_key_with_a_colon_survives(self) -> None:
        """The payload is `<expiry>:<key>` split once, so the key may hold anything."""
        key = "tenants/6f1c2d3e-0000-4000-8000-000000000001/media/a:b.png"
        token, _ = links.sign(key, 600, secret=SECRET, now=NOW)

        verified = links.verify(token, secret=SECRET, now=NOW)

        assert verified is not None and verified.key == key


class TestWhatStopsItWorking:
    def test_it_expires(self) -> None:
        token, expires_at = links.sign(KEY, 600, secret=SECRET, now=NOW)

        assert links.verify(token, secret=SECRET, now=expires_at - 1) is not None
        assert links.verify(token, secret=SECRET, now=expires_at) is None
        assert links.verify(token, secret=SECRET, now=expires_at + 10_000) is None

    def test_changing_the_file_invalidates_the_signature(self) -> None:
        """The attack that matters: take a link for your file, point it at theirs."""
        token, expires_at = links.sign(KEY, 600, secret=SECRET, now=NOW)
        _, _, mac = token.split(".")
        swapped = base64.urlsafe_b64encode(f"{expires_at}:{OTHER_KEY}".encode()).rstrip(b"=")

        assert links.verify(f"v1.{swapped.decode()}.{mac}", secret=SECRET, now=NOW) is None

    def test_extending_the_expiry_invalidates_the_signature(self) -> None:
        token, expires_at = links.sign(KEY, 600, secret=SECRET, now=NOW)
        _, _, mac = token.split(".")
        later = base64.urlsafe_b64encode(f"{expires_at + 86_400}:{KEY}".encode()).rstrip(b"=")

        assert links.verify(f"v1.{later.decode()}.{mac}", secret=SECRET, now=NOW) is None

    def test_a_link_signed_with_another_secret_is_refused(self) -> None:
        other = links.derive_secret("some-other-secret-key-of-ample-length-xyz")
        token, _ = links.sign(KEY, 600, secret=other, now=NOW)

        assert links.verify(token, secret=SECRET, now=NOW) is None

    def test_the_session_key_itself_cannot_sign_a_link(self) -> None:
        """Links are signed with a key *derived* from `SECRET_KEY`, so holding the
        raw key (what signs sessions) is not the same as holding the link key."""
        raw = b"a-secret-key-that-is-long-enough-for-tests"
        forged = _forge(f"{int(NOW) + 600}:{KEY}", secret=raw)

        assert links.verify(forged, secret=SECRET, now=NOW) is None

    def test_a_future_version_is_not_understood(self) -> None:
        assert (
            links.verify(_forge(f"{int(NOW) + 600}:{KEY}", version="v2"), secret=SECRET, now=NOW)
            is None
        )

    @pytest.mark.parametrize(
        "token",
        [
            "",
            ".",
            "..",
            "v1",
            "v1.",
            "v1..",
            "v1.a.b.c",
            "not-a-token",
            "v1.!!!.???",
            "v1.éé.éé",
            "v1." + "A" * 5000 + "." + "B" * 5000,
            "\x00",
        ],
        ids=[
            "empty",
            "dot",
            "two-dots",
            "no-parts",
            "one-dot",
            "empty-parts",
            "too-many-parts",
            "word",
            "not-base64",
            "non-ascii",
            "huge",
            "nul",
        ],
    )
    def test_garbage_is_refused_and_never_raises(self, token: str) -> None:
        """The endpoint is public, so nothing a stranger can send may be an exception."""
        assert links.verify(token, secret=SECRET, now=NOW) is None

    def test_a_genuine_signature_over_a_damaged_payload_is_refused(self) -> None:
        for payload in ["no-colon", "abc:key", ":key", "12.5:key", ""]:
            assert links.verify(_forge(payload), secret=SECRET, now=NOW) is None


class TestHowLongItLasts:
    def test_a_link_never_lives_longer_than_it_was_asked_to(self) -> None:
        for now in (NOW, NOW + 0.999, NOW + 149, NOW + 150, NOW + 299.5):
            for asked in (1, 2, 5, 60, 600, 3600, 86_400):
                _, expires_at = links.sign(KEY, asked, secret=SECRET, now=now)

                assert expires_at <= int(now) + asked, (now, asked)

    @given(
        now=st.floats(min_value=1_000_000_000, max_value=4_000_000_000),
        asked=st.integers(min_value=1, max_value=links.MAX_LIFETIME_SECONDS),
    )
    def test_for_any_moment_and_lifetime_it_is_alive_but_never_over_promised(
        self, now: float, asked: int
    ) -> None:
        _, expires_at = links.sign(KEY, asked, secret=SECRET, now=now)

        assert expires_at > now
        assert expires_at <= int(now) + asked

    def test_a_ten_minute_link_lasts_at_least_seven_and_a_half(self) -> None:
        """Rounding down trades a little lifetime for a stable URL; this is the bound."""
        shortest = min(
            links.sign(KEY, 600, secret=SECRET, now=NOW + offset)[1] - (NOW + offset)
            for offset in range(0, 300)
        )

        assert shortest > 450

    def test_equal_requests_close_together_share_one_url(self) -> None:
        """So the browser keeps the image instead of fetching it on every render."""
        first, _ = links.sign(KEY, 600, secret=SECRET, now=NOW)
        second, _ = links.sign(KEY, 600, secret=SECRET, now=NOW + 5)

        assert first == second

    def test_different_files_get_different_links(self) -> None:
        first, _ = links.sign(KEY, 600, secret=SECRET, now=NOW)
        second, _ = links.sign(OTHER_KEY, 600, secret=SECRET, now=NOW)

        assert first != second

    @pytest.mark.parametrize("asked", [0, -1, links.MAX_LIFETIME_SECONDS + 1])
    def test_a_lifetime_outside_the_cap_is_refused(self, asked: int) -> None:
        with pytest.raises(ValueError):
            links.sign(KEY, asked, secret=SECRET, now=NOW)

    def test_a_one_second_link_is_alive_when_handed_out(self) -> None:
        token, expires_at = links.sign(KEY, 1, secret=SECRET, now=NOW)

        assert expires_at > NOW
        assert links.verify(token, secret=SECRET, now=NOW) is not None


class TestTheDerivedKey:
    def test_it_is_deterministic_and_not_the_secret(self) -> None:
        assert links.derive_secret("abc") == links.derive_secret("abc")
        assert links.derive_secret("abc") != links.derive_secret("abd")
        assert links.derive_secret("abc") != b"abc"
        assert len(links.derive_secret("abc")) == 32
