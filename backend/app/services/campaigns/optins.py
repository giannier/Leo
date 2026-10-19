"""Who agreed to hear about promotions, and the proof (Feature 011, HU-02).

**By button (C-102).** After the business confirms a payment, the customer is asked once,
with two buttons, whether they want promotions. The tap is the evidence; Leo answers with a
fixed text and the assistant is not called (no model cost).

**By import (C-001).** The owner uploads a list and declares, with a checkbox and a stored
text, that those people agreed. At most 200 a week. Somebody who said no, or wrote BAJA, is
never turned back into a yes by an import.

**BAJA revokes (C-103).** Writing BAJA (or the owner marking it in the inbox) revokes the
permission; writing again later does not give it back.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.business import AgentConfig
from app.models.campaigns import WEEKLY_IMPORT_LIMIT, ContactOptin
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.orders.customer_data import InvalidField, validate_field
from app.providers.whatsapp.base import Button
from app.repositories.campaigns import (
    ContactOptinRepository,
    ContactTagRepository,
    OptinImportRepository,
)
from app.repositories.channel import ContactRepository, session_window_is_open
from app.repositories.conversation import MessageRepository
from app.services.campaigns import CampaignError
from app.services.campaigns.sender import business_name
from app.services.csv_import import decode, neutralise_formula, sniff_delimiter
from app.services.turn_outbound import buttons_payload

BUTTON_YES = "optin_si"
BUTTON_NO = "optin_no"
ANSWER_YES = (
    "¡Listo! Te avisaremos de promociones y novedades. Si ya no quieres recibirlas, escribe BAJA."
)
ANSWER_NO = "Entendido, no te enviaremos promociones. ¡Gracias por tu compra!"
DECLARATION = (
    "Declaro que las personas de esta lista aceptaron recibir mensajes de mi negocio por "
    "WhatsApp y que puedo demostrarlo. Entiendo que enviar mensajes a quien no lo aceptó va "
    "contra las reglas de WhatsApp y la Ley 29733, y puede bloquear mi número."
)
MAX_IMPORT_BYTES = 512 * 1024
_PHONE_HEADERS = ("telefono", "teléfono", "celular", "whatsapp", "numero", "número", "phone")
_NAME_HEADERS = ("nombre", "nombres", "name", "cliente")


def question(session: Session, tenant_id: uuid.UUID) -> str:
    return (
        f"¿Quieres que {business_name(session, tenant_id)} te avise de promociones y "
        "novedades por aquí? Puedes darte de baja cuando quieras escribiendo BAJA."
    )


def is_eligible(optin: ContactOptin | None, contact: Contact) -> bool:
    return optin is not None and optin.status == "granted" and contact.opted_out_at is None


class OptinService:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self.optins = ContactOptinRepository(session, tenant_id)

    # ----------------------------------------------------------------- button

    def prepare_question(
        self, conversation: Conversation, contact: Contact, *, now: datetime
    ) -> Message | None:
        """The question, written `queued` in the caller's transaction, or None.

        Asked once per contact, only on the real channel, only with the window open.
        """
        config = self._session.get(AgentConfig, self._tenant_id)
        if config is not None and not config.optin_preguntar:
            return None
        if conversation.channel != "whatsapp" or contact.opted_out_at is not None:
            return None
        if self.optins.for_contact(contact.id) is not None:
            return None
        if not session_window_is_open(contact, now=now):
            return None
        text = question(self._session, self._tenant_id)
        buttons = [
            Button(id=BUTTON_YES, title="Sí, avísame"),
            Button(id=BUTTON_NO, title="No, gracias"),
        ]
        message = MessageRepository(self._session, self._tenant_id).append(
            conversation_id=conversation.id,
            direction="outbound",
            author="system",
            content=text,
            msg_type="interactive",
            delivery_status="queued",
            payload={**buttons_payload(text, buttons), "motivo": "optin"},
        )
        self.optins.add(
            contact_id=contact.id,
            source="conversation",
            status="asked",
            asked_at=now,
            evidence={"pregunta": text, "mensaje_pregunta": str(message.id)},
        )
        return message

    def record_answer(
        self, contact: Contact, *, granted: bool, message_id: uuid.UUID, at: datetime
    ) -> str:
        """Store the tap and return the fixed answer to send."""
        optin = self.optins.for_contact(contact.id)
        evidence = {"respuesta": "si" if granted else "no", "mensaje_respuesta": str(message_id)}
        if optin is None:
            self.optins.add(
                contact_id=contact.id,
                source="conversation",
                status="granted" if granted else "declined",
                answered_at=at,
                evidence=evidence,
            )
        else:
            optin.status = "granted" if granted else "declined"
            optin.source = "conversation"
            optin.answered_at = at
            optin.revoked_at = None
            optin.evidence = {**(optin.evidence or {}), **evidence}
            self._session.flush()
        return ANSWER_YES if granted else ANSWER_NO

    def revoke(self, contact: Contact, *, at: datetime) -> None:
        optin = self.optins.for_contact(contact.id)
        if optin is not None and optin.status == "granted":
            optin.status = "revoked"
            optin.revoked_at = at
            self._session.flush()

    # ----------------------------------------------------------------- import

    def import_csv(
        self,
        raw: bytes,
        *,
        filename: str | None,
        accepted: bool,
        user_id: uuid.UUID | None,
        tags: list[str],
        now: datetime | None = None,
    ) -> ImportResult:
        if not accepted:
            raise CampaignError(
                "sin_declaracion", "Para importar debes aceptar la declaración de consentimiento."
            )
        if len(raw) > MAX_IMPORT_BYTES:
            raise CampaignError("archivo_grande", "El archivo puede pesar hasta 512 KB.")
        at = now or datetime.now(UTC)
        rows = parse_contacts(raw, pais=self._country())
        imports = OptinImportRepository(self._session, self._tenant_id)
        used = imports.imported_since(at - timedelta(days=7))
        room = WEEKLY_IMPORT_LIMIT - used
        if len(rows.valid) > room:
            raise CampaignError(
                "limite_semanal",
                f"Puedes importar hasta {WEEKLY_IMPORT_LIMIT} contactos por semana; te quedan "
                f"{max(room, 0)}. Sube una lista más corta.",
            )
        record = imports.add(
            user_id=user_id,
            declaration=DECLARATION,
            filename=filename,
            imported=0,
        )
        contacts = ContactRepository(self._session, self._tenant_id)
        tag_repo = ContactTagRepository(self._session, self._tenant_id)
        result = ImportResult(invalid=list(rows.invalid))
        for phone, name in rows.valid:
            contact = contacts.by_phone(phone) or contacts.upsert(phone, profile_name=name)
            if name and not (contact.custom_data or {}).get("nombres"):
                contacts.merge_custom_data(contact, {"nombres": name})
            optin = self.optins.for_contact(contact.id)
            if contact.opted_out_at is not None or (
                optin is not None and optin.status in ("declined", "revoked")
            ):
                result.refused.append(phone)
                continue
            if optin is not None and optin.status == "granted":
                result.already.append(phone)
            elif optin is None:
                self.optins.add(
                    contact_id=contact.id,
                    source="import",
                    status="granted",
                    answered_at=at,
                    evidence={"importacion": str(record.id), "declaracion": DECLARATION},
                )
                result.imported.append(phone)
            else:
                optin.status = "granted"
                optin.source = "import"
                optin.answered_at = at
                optin.evidence = {"importacion": str(record.id), "declaracion": DECLARATION}
                result.imported.append(phone)
            tag_repo.add_tags(contact.id, tags)
        record.imported = len(result.imported)
        record.skipped = len(result.refused) + len(result.invalid) + len(result.already)
        self._session.flush()
        return result

    def _country(self) -> str:
        tenant = self._session.get(Tenant, self._tenant_id)
        return tenant.pais if tenant is not None else "PE"


@dataclass
class ImportResult:
    imported: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    invalid: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedContacts:
    valid: list[tuple[str, str | None]]
    invalid: list[dict[str, Any]]


def parse_contacts(raw: bytes, *, pais: str) -> ParsedContacts:
    """A CSV with a phone column and, optionally, a name column."""
    text = decode(raw)
    lines = text.splitlines()
    if not lines:
        raise CampaignError("archivo_vacio", "El archivo está vacío.")
    reader = csv.reader(io.StringIO(text), delimiter=sniff_delimiter(text[:4096]))
    header = [cell.strip().lower() for cell in next(reader, [])]
    phone_col = next((i for i, h in enumerate(header) if h in _PHONE_HEADERS), None)
    name_col = next((i for i, h in enumerate(header) if h in _NAME_HEADERS), None)
    if phone_col is None:
        raise CampaignError(
            "sin_columna_telefono",
            "El archivo necesita una columna 'telefono' (y opcional 'nombre').",
        )
    valid: list[tuple[str, str | None]] = []
    invalid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        raw_phone = row[phone_col].strip() if phone_col < len(row) else ""
        try:
            phone = validate_field("telefono", raw_phone, pais=pais)
        except InvalidField:
            invalid.append(
                {"fila": number, "telefono": raw_phone[:30], "error": "Teléfono no válido"}
            )
            continue
        if phone in seen:
            continue
        seen.add(phone)
        name = row[name_col].strip() if name_col is not None and name_col < len(row) else ""
        valid.append((phone, neutralise_formula(name)[:60] or None))
    return ParsedContacts(valid, invalid)
