"""One website for the business, asked once (feature 019).

The web used to live in three places that did not know each other: the profile's "página web o red
social" (read by nobody), the contact section (what the assistant gives to customers) and the
knowledge source that reads the site. The profile and the contact section now mirror each other, and
the assistant falls back to the profile's, so writing it once is enough.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.business import BusinessProfileRepository
from app.repositories.sales_config import BusinessInfoRepository

_DOMAIN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?$", re.I
)
_SOCIAL = (
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "wa.me",
    "linktr.ee",
    "twitter.com",
    "x.com",
)


def normalize_website(raw: str | None) -> str | None:
    """`mitienda.pe`, `www.mitienda.pe/` and `https://mitienda.pe` as one URL; None if not a site.

    A social profile ("instagram.com/mitienda") stays out: the contact section has fields for it.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text or " " in text:
        return None
    bare = re.sub(r"^https?://", "", text, flags=re.I)
    if not _DOMAIN.match(bare):
        return None
    host = bare.split("/", 1)[0].lower().removeprefix("www.")
    if any(host == s or host.endswith("." + s) for s in _SOCIAL):
        return None
    scheme = "http://" if text.lower().startswith("http://") else "https://"
    return scheme + bare


def mirror_profile_website_into_contact(
    session: Session, tenant_id: uuid.UUID, website: str | None
) -> bool:
    """Put the profile's website in the contact section if that has none. True if it changed."""
    url = normalize_website(website)
    if url is None:
        return False
    info = BusinessInfoRepository(session, tenant_id)
    row = info.section("contacto")
    data: dict[str, Any] = dict(row.data) if row else {}
    if data.get("web"):
        return False
    data["web"] = url
    data.setdefault("redes", {})
    info.upsert("contacto", data)
    return True


def mirror_contact_website_into_profile(
    session: Session, tenant_id: uuid.UUID, website: str | None
) -> bool:
    """Put the contact section's web in the profile if that has none. True if it changed."""
    url = normalize_website(website)
    if url is None:
        return False
    profiles = BusinessProfileRepository(session, tenant_id)
    current = profiles.current()
    if current is not None and normalize_website(current.website):
        return False
    profiles.upsert(website=url)
    return True
