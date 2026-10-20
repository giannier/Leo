"""Signed, expiring links to files held in Postgres.

S3 hands out presigned URLs; a file in a table has nothing to sign, so the API
serves it itself (`app/api/v1/files.py`) and this module makes the link that
authorises one read. A link is a capability: whoever holds it can read that one
file until it expires, which is exactly what `<img src>` in the panel and
WhatsApp downloading media need - neither can send a login.

    v1.<payload>.<mac>      payload = base64url("<expires_at>:<key>")

Properties worth keeping if this is ever changed:

- **The signing key is derived, not shared.** It is an HMAC of `SECRET_KEY` under
  a purpose label, so the key that signs sessions never signs links and a link
  can never be passed off as anything else. No new secret to look after.
- **Verification costs no query.** A forged, damaged or expired link is rejected
  by arithmetic, before the database is involved, so the public endpoint cannot
  be made to work by guessing.
- **A link never lives longer than promised.** The expiry is rounded *down* to a
  bucket, so the same file gets the same URL for a few minutes (the browser
  keeps the image instead of fetching it again on every render) without any link
  outliving the lifetime its caller asked for - a receipt asked for 10 minutes
  never lasts 11.
- **Versioned.** A change of scheme is a `v2` prefix; old links just stop
  verifying, which for links that last minutes to a day is no migration at all.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from dataclasses import dataclass

VERSION = "v1"
PURPOSE = b"leo/storage-link/v1"

# Longer than any caller asks today (campaigns: 24 h). A cap, so a bug cannot
# mint a link that outlives the data it points at by months.
MAX_LIFETIME_SECONDS = 7 * 24 * 3600
# How coarsely expiry is rounded. Small enough that a 10-minute link still lasts
# at least 7.5 minutes, large enough that a page re-rendered twice in a row gets
# the same URL.
MAX_BUCKET_SECONDS = 300


@dataclass(frozen=True)
class VerifiedLink:
    key: str
    expires_at: int


def derive_secret(secret_key: str) -> bytes:
    """The key links are signed with, derived from the application's `SECRET_KEY`."""
    return hmac.new(secret_key.encode("utf-8"), PURPOSE, hashlib.sha256).digest()


def expiry_for(expires_in: int, now: float) -> int:
    """When a link asked to last `expires_in` seconds really stops working.

    Never later than `now + expires_in`; at most a quarter of the lifetime (up to
    five minutes) earlier, so equal requests close together share one URL.
    """
    if expires_in <= 0 or expires_in > MAX_LIFETIME_SECONDS:
        raise ValueError(f"a link must last between 1 second and {MAX_LIFETIME_SECONDS}")
    bucket = max(1, min(MAX_BUCKET_SECONDS, expires_in // 4))
    expires_at = (int(now) + expires_in) // bucket * bucket
    # A very short link must still be alive when it is handed out.
    return max(expires_at, int(now) + 1)


def sign(key: str, expires_in: int, *, secret: bytes, now: float | None = None) -> tuple[str, int]:
    """A token that authorises reading `key`, and the second it stops working."""
    current = time.time() if now is None else now
    expires_at = expiry_for(expires_in, current)
    payload = _b64(f"{expires_at}:{key}".encode())
    return f"{VERSION}.{payload}.{_mac(payload, secret)}", expires_at


def verify(token: str, *, secret: bytes, now: float | None = None) -> VerifiedLink | None:
    """The link behind `token`, or `None` for anything that is not a live, genuine one.

    One answer for every way of failing - wrong version, damaged, forged,
    expired - because telling them apart would tell a guesser which part was right.
    """
    current = time.time() if now is None else now
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        return None
    _, payload, mac = parts
    # `compare_digest` takes the same time wherever the two differ. Compared as
    # bytes: given `str` it raises on non-ASCII, and the token is attacker input.
    if not hmac.compare_digest(_mac(payload, secret).encode("ascii"), mac.encode("utf-8")):
        return None
    try:
        text = _unb64(payload).decode("utf-8")
        expires_text, key = text.split(":", 1)
        expires_at = int(expires_text)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if expires_at <= current:
        return None
    return VerifiedLink(key=key, expires_at=expires_at)


def _mac(payload: str, secret: bytes) -> str:
    digest = hmac.new(secret, f"{VERSION}.{payload}".encode(), hashlib.sha256).digest()
    return _b64(digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
