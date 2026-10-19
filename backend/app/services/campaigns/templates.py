"""The business's WhatsApp templates (Feature 011, HU-01).

Written with named variables (`{{nombre}}`, `{{negocio}}`, `{{producto}}`, C-105). On
submit they become `{{1}}`, `{{2}}`... with automatic examples, Meta reviews them, and the
verdict arrives by webhook (`apply_review`). A rejected template can be edited and sent
again; an approved one is final (Meta does not allow editing an approved body freely, and a
campaign may be using it), so the owner duplicates it instead.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import CredentialDecryptionError
from app.models.business import BusinessProfile, Product
from app.models.campaigns import TEMPLATE_CATEGORIES, TEMPLATE_VARIABLES, MessageTemplate
from app.models.media import MediaAsset
from app.models.whatsapp import WhatsAppNumber
from app.providers.storage.base import StorageProvider
from app.providers.whatsapp.base import (
    TemplateSpec,
    TemplateStatusUpdate,
    WhatsAppError,
    WhatsAppProvider,
)
from app.repositories.campaigns import CampaignRepository, MessageTemplateRepository
from app.services.campaigns import CampaignError
from app.services.campaigns.library import library_entry
from app.services.campaigns.sender import (
    PLACEHOLDER,
    business_name,
    connected_number,
    header_link,
)
from app.services.connection import read_credentials

logger = logging.getLogger(__name__)

MAX_LABEL = 60
MAX_FOOTER = 60
# Meta asks marketing messages to say how to stop them; added when the owner did not.
MARKETING_FOOTER = "Escribe BAJA si no quieres recibir más mensajes"
EDITABLE = ("draft", "rejected")
# What Meta's reasons mean, in words the owner can act on.
REJECTION_HINTS = {
    "INVALID_FORMAT": "El formato no es válido: revisa las variables y que no empiece ni "
    "termine con una.",
    "ABUSIVE_CONTENT": "Meta lo consideró contenido no permitido. Reescríbelo con un tono "
    "más informativo.",
    "INCORRECT_CATEGORY": "La categoría no corresponde: si invita a comprar, es Marketing.",
    "TAG_CONTENT_MISMATCH": "La categoría no corresponde al contenido.",
    "SCAM": "Meta lo consideró engañoso. Evita promesas exageradas o enlaces dudosos.",
}
EXAMPLES = {"nombre": "Ana", "producto": "tu producto favorito"}


def meta_name(label: str, taken: set[str]) -> str:
    """Lowercase, digits and underscores, unique within the business."""
    plain = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:50] or "plantilla"
    name, suffix = base, 2
    while name in taken:
        name = f"{base}_{suffix}"
        suffix += 1
    return name


def variables_of(body: str) -> list[str]:
    """The named variables in order of first appearance; refuses unknown ones (C-105)."""
    found: list[str] = []
    for match in PLACEHOLDER.finditer(body):
        name = match.group(1).lower()
        if name not in TEMPLATE_VARIABLES:
            raise CampaignError(
                "variable_desconocida",
                f"La variable {{{{{match.group(1)}}}}} no existe. Usa {{{{nombre}}}}, "
                "{{negocio}} o {{producto}}.",
            )
        if name not in found:
            found.append(name)
    return found


def validate_body(body: str) -> str:
    text = body.strip()
    if not text:
        raise CampaignError("texto_vacio", "Escribe el mensaje.")
    if len(text) > 1024:
        raise CampaignError("texto_largo", "El mensaje puede tener hasta 1024 caracteres.")
    # Meta rejects a body that starts or ends with a variable.
    if PLACEHOLDER.match(text) or re.search(r"\{\{\s*[A-Za-z_]+\s*\}\}$", text):
        raise CampaignError(
            "variable_en_borde",
            "El mensaje no puede empezar ni terminar con una variable; agrega texto antes o "
            "después.",
        )
    return text


def positional(body: str, variables: list[str]) -> str:
    """`{{nombre}}` → `{{1}}`, in the order of `variables`."""
    return PLACEHOLDER.sub(
        lambda m: "{{" + str(variables.index(m.group(1).lower()) + 1) + "}}", body
    )


def rejection_hint(reason: str | None) -> str | None:
    if not reason:
        return None
    return REJECTION_HINTS.get(reason.upper(), f"Meta lo rechazó ({reason}).")


class TemplateService:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self.templates = MessageTemplateRepository(session, tenant_id)

    # ---------------------------------------------------------------- editing

    def _clean(self, values: dict[str, Any]) -> dict[str, Any]:
        label = str(values.get("label") or "").strip()
        if not label or len(label) > MAX_LABEL:
            raise CampaignError("nombre_invalido", "Ponle un nombre de hasta 60 caracteres.")
        category = values.get("category")
        if category not in TEMPLATE_CATEGORIES:
            raise CampaignError("categoria_invalida", "Elige Utilidad o Marketing.")
        body = validate_body(str(values.get("body") or ""))
        footer = (str(values.get("footer") or "").strip() or None) if "footer" in values else None
        if footer and len(footer) > MAX_FOOTER:
            raise CampaignError("pie_largo", "El pie puede tener hasta 60 caracteres.")
        if category == "marketing" and not footer:
            footer = MARKETING_FOOTER
        asset_id = values.get("header_media_asset_id")
        if asset_id is not None:
            asset = self._session.get(MediaAsset, asset_id)
            if asset is None or asset.tenant_id != self._tenant_id or asset.tipo != "imagen":
                raise CampaignError("imagen_invalida", "Elige una imagen de tu biblioteca.")
        return {
            "label": label,
            "category": category,
            "body": body,
            "footer": footer,
            "header_media_asset_id": asset_id,
            "variables": variables_of(body),
        }

    def create(self, values: dict[str, Any], *, library_key: str | None = None) -> MessageTemplate:
        clean = self._clean(values)
        return self.templates.add(
            name=meta_name(clean["label"], self.templates.names()),
            library_key=library_key,
            **clean,
        )

    def clone(self, key: str) -> MessageTemplate:
        entry = library_entry(key)
        if entry is None:
            raise CampaignError("no_existe", "Esa plantilla no está en la biblioteca.", status=404)
        return self.create(
            {
                "label": entry.label,
                "category": entry.category,
                "body": entry.body,
                "footer": entry.footer,
            },
            library_key=entry.key,
        )

    def duplicate(self, template: MessageTemplate) -> MessageTemplate:
        return self.create(
            {
                "label": f"{template.label} (copia)"[:MAX_LABEL],
                "category": template.category,
                "body": template.body,
                "footer": template.footer,
                "header_media_asset_id": template.header_media_asset_id,
            },
            library_key=template.library_key,
        )

    def update(self, template: MessageTemplate, values: dict[str, Any]) -> MessageTemplate:
        if template.status not in EDITABLE:
            raise CampaignError(
                "no_editable",
                "Una plantilla enviada a Meta no se edita: duplícala y cambia la copia.",
                status=409,
            )
        clean = self._clean(values)
        for field, value in clean.items():
            setattr(template, field, value)
        # A rejected template edited is a new draft; Meta reviews it again on submit.
        template.status = "draft"
        template.rejection_reason = None
        self._session.flush()
        return template

    def delete(self, template: MessageTemplate) -> None:
        if CampaignRepository(self._session, self._tenant_id).using_template(template.id):
            raise CampaignError(
                "en_uso", "Una campaña usa esta plantilla; no se puede borrar.", status=409
            )
        self._session.delete(template)
        self._session.flush()

    # ----------------------------------------------------------------- review

    def spec(self, template: MessageTemplate, storage: StorageProvider | None) -> TemplateSpec:
        examples = {**EXAMPLES, "negocio": business_name(self._session, self._tenant_id)}
        first = self._session.execute(
            select(Product.name)
            .where(
                Product.tenant_id == self._tenant_id,
                Product.is_active.is_(True),
                Product.deleted_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()
        if first:
            examples["producto"] = first
        return TemplateSpec(
            name=template.name,
            language=template.language,
            category=template.category,  # type: ignore[arg-type]
            body=positional(template.body, list(template.variables)),
            examples=tuple(examples[name] for name in template.variables),
            footer=template.footer,
            header_image_link=header_link(self._session, self._tenant_id, template, storage),
        )

    async def submit(
        self,
        template: MessageTemplate,
        *,
        provider: WhatsAppProvider,
        storage: StorageProvider | None,
    ) -> MessageTemplate:
        if template.status not in EDITABLE:
            raise CampaignError("ya_enviada", "Esta plantilla ya está en Meta.", status=409)
        _number, credentials = connected_number(self._session, self._tenant_id)
        try:
            result = await provider.submit_template(credentials, self.spec(template, storage))
        except WhatsAppError as exc:
            logger.warning(
                "a template was refused on submit",
                extra={"tenant_id": str(self._tenant_id), "reason": str(exc)[:200]},
            )
            raise CampaignError(
                "meta_rechazo",
                "WhatsApp no aceptó la plantilla. Revisa el texto e inténtalo de nuevo."
                if not exc.retryable
                else "WhatsApp no respondió. Inténtalo en unos minutos.",
                status=502,
            ) from exc
        now = datetime.now(UTC)
        template.status = result.status
        template.provider_template_id = result.provider_template_id
        template.rejection_reason = result.reason
        template.submitted_at = now
        if result.status != "pending":
            template.reviewed_at = now
        self._session.flush()
        return template


def apply_review(session: Session, update: TemplateStatusUpdate) -> list[MessageTemplate]:
    """Record Meta's verdict. Platform-wide: the business is found by its WABA id.

    A template belongs to the WhatsApp account, so two numbers of one account (the businesses
    of a holding) that each have a template with this name both get the verdict.
    """
    updated: list[MessageTemplate] = []
    seen: set[uuid.UUID] = set()
    for number in session.execute(
        select(WhatsAppNumber).where(WhatsAppNumber.status == "connected")
    ).scalars():
        if number.tenant_id in seen:
            continue
        try:
            credentials = read_credentials(number)
        except CredentialDecryptionError:
            # One number with stale credentials must not stop the others' reviews.
            logger.warning(
                "a number's credentials could not be read", extra={"number": str(number.id)}
            )
            continue
        if credentials is None or credentials.values.get("waba_id") != update.waba_id:
            continue
        seen.add(number.tenant_id)
        template = MessageTemplateRepository(session, number.tenant_id).by_name(
            update.name, update.language
        )
        if template is None:
            continue
        template.status = update.status
        template.rejection_reason = update.reason if update.status == "rejected" else None
        template.provider_template_id = update.provider_template_id or template.provider_template_id
        template.reviewed_at = datetime.now(UTC)
        updated.append(template)
    session.flush()
    if not updated:
        logger.info(
            "a template review for a template we do not know", extra={"template": update.name}
        )
    return updated


def industry_of(session: Session, tenant_id: uuid.UUID) -> str | None:
    profile = session.get(BusinessProfile, tenant_id)
    return profile.industry if profile is not None else None
