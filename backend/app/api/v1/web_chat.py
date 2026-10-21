"""The business's web chat, in the panel (feature 019, phase F).

    GET  /tenant/web-chat           the address, the switch, the greeting, the colour, the daily cap
    PUT  /tenant/web-chat           change them (managers)
    POST /tenant/web-chat/rotate    a new address; the old one stops working (managers)

The address the owner shares is `{frontend}/chat/{key}`; the snippet for a website loads the
widget from `{frontend}/widget.js`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ConfigurationManagerDep, SessionDep
from app.api.errors import error_responses
from app.core.config import get_settings
from app.models.web_chat import WebChat
from app.services.webchat import WebChatService

router = APIRouter(prefix="/tenant/web-chat", tags=["web chat"])

AUTH = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)


class WebChatOut(BaseModel):
    enabled: bool
    public_key: str
    url: str
    snippet: str
    saludo: str | None
    color: str
    limite_diario: int


class WebChatIn(BaseModel):
    enabled: bool | None = None
    saludo: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    limite_diario: int | None = Field(default=None, ge=10, le=5000)


def _out(row: WebChat) -> WebChatOut:
    base = get_settings().frontend_base_url.rstrip("/")
    return WebChatOut(
        enabled=row.enabled,
        public_key=row.public_key,
        url=f"{base}/chat/{row.public_key}",
        snippet=(
            f'<script src="{base}/widget.js" data-chat="{row.public_key}" '
            f'data-color="{row.color}" async></script>'
        ),
        saludo=row.saludo,
        color=row.color,
        limite_diario=row.limite_diario,
    )


@router.get("", responses=error_responses(*AUTH))
def read(principal: ConfigurationManagerDep, session: SessionDep) -> WebChatOut:
    row = WebChatService(session, principal.tenant_id).current()
    session.commit()
    return _out(row)


@router.put("", responses=error_responses(*AUTH))
def save(body: WebChatIn, principal: ConfigurationManagerDep, session: SessionDep) -> WebChatOut:
    row = WebChatService(session, principal.tenant_id).current()
    changes = body.model_dump(exclude_unset=True)
    if "saludo" in changes:
        changes["saludo"] = changes["saludo"] or None
    for field, value in changes.items():
        if value is not None or field == "saludo":
            setattr(row, field, value)
    session.commit()
    return _out(row)


@router.post("/rotate", responses=error_responses(*AUTH))
def rotate(principal: ConfigurationManagerDep, session: SessionDep) -> WebChatOut:
    row = WebChatService(session, principal.tenant_id).rotate_key()
    session.commit()
    return _out(row)
