"""The satisfaction survey: "del 1 al 5" after a sale or a closed conversation (Feature 013).

**When** (C-102): a conversation won (a confirmed order) or closed by a person, an hour later,
only inside Meta's 24-hour window, never to somebody who wrote BAJA, at most once every 30 days
per customer, and only if the business turned it on. Fixed text, no model.

**The answer** (C-103) is read without the model. A number ("5", "cinco", "⭐⭐⭐⭐⭐", "4 de 5")
or a clear word ("excelente") is the score: stored, thanked, and the assistant is not called
unless the message also asks something. Anything else closes the survey with the text as a
comment and goes to the assistant as usual (spec C-001, C-002). No answer in 48 hours: expired.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.analytics import CsatSurvey
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.crm import Lead
from app.models.metrics import DashboardSettings
from app.repositories.conversation import MessageRepository

logger = logging.getLogger(__name__)

QUESTION = "¿Qué tal te atendimos? Responde con un número del 1 al 5 (5 = excelente)."
THANKS = "¡Gracias por tu respuesta! Nos ayuda a mejorar."
DELAY = timedelta(hours=1)
LOOKBACK = timedelta(hours=24)
WINDOW = timedelta(hours=24)
WINDOW_MARGIN = timedelta(minutes=30)
EVERY = timedelta(days=30)
ANSWER_WITHIN = timedelta(hours=48)
LONG_MESSAGE_WORDS = 12

_WORDS = {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
# Only words whose meaning is clear; "regular" or "más o menos" stay without a score (C-001).
_OPINIONS = (
    (re.compile(r"\b(pesimo|horrible|terrible|muy mal[oa]?)\b"), 1),
    (re.compile(r"\b(excelente|perfecto|genial|buenisim[oa])\b|10 ?(de|/) ?10"), 5),
    (re.compile(r"\b(muy buen[oa]|muy bien)\b"), 5),
    (re.compile(r"\b(mal[oa]?)\b"), 2),
    (re.compile(r"\b(buen[oa]|bien)\b"), 4),
)
_DIGIT = re.compile(r"(?<![\d.,])([1-5])(?![\d.,])")


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


@dataclass(frozen=True)
class Reading:
    score: int | None
    comment: str | None
    # Also for the assistant: it asks something or says much more than a score.
    for_assistant: bool


def read_answer(text: str | None) -> Reading:
    raw = (text or "").strip()
    if not raw:
        return Reading(None, None, False)
    asks = "?" in raw or "¿" in raw or len(raw.split()) > LONG_MESSAGE_WORDS
    stars = raw.count("⭐") + raw.count("★")
    plain = _plain(raw)
    score: int | None = None
    leftover = raw
    if 1 <= stars <= 5:
        score = stars
        leftover = raw.replace("⭐", "").replace("★", "")
    elif match := _DIGIT.search(plain):
        score = int(match.group(1))
        leftover = re.sub(r"\b[1-5]\s*(/\s*5|de\s*5)?", "", raw, count=1)
    else:
        for word, value in _WORDS.items():
            if re.search(rf"\b{word}\b", plain):
                score = value
                leftover = re.sub(rf"\b{word}\b", "", raw, count=1, flags=re.IGNORECASE)
                break
        else:
            for pattern, value in _OPINIONS:
                if pattern.search(plain):
                    score = value
                    break
    comment = leftover.strip(" ,.-!:") or None
    if score is None:
        return Reading(None, raw, True)
    return Reading(score, comment, asks)


def last_operator(
    session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> uuid.UUID | None:
    """The person who wrote last in the conversation, if anybody did (C-105)."""
    payload = session.execute(
        select(Message.payload)
        .where(
            Message.tenant_id == tenant_id,
            Message.conversation_id == conversation_id,
            Message.author == "human_operator",
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    raw = (payload or {}).get("operador_id") if isinstance(payload, dict) else None
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Candidate:
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    trigger: str
    lead_id: uuid.UUID | None = None


def candidates(session: Session, *, now: datetime) -> list[Candidate]:
    """Conversations won or closed between 25 and 1 hours ago, in businesses with it on."""
    since, until = now - LOOKBACK - DELAY, now - DELAY
    enabled = select(DashboardSettings.tenant_id).where(DashboardSettings.csat_activo.is_(True))
    won = session.execute(
        select(Lead.tenant_id, Lead.conversation_id, Lead.id).where(
            Lead.tenant_id.in_(enabled),
            Lead.status == "won",
            Lead.conversation_id.is_not(None),
            Lead.closed_at >= since,
            Lead.closed_at < until,
        )
    ).all()
    closed = session.execute(
        select(Message.tenant_id, Message.conversation_id).where(
            Message.tenant_id.in_(enabled),
            Message.msg_type == "event",
            Message.payload["evento"].astext == "cerrada",
            Message.created_at >= since,
            Message.created_at < until,
        )
    ).all()
    seen: set[uuid.UUID] = set()
    found: list[Candidate] = []
    for tenant_id, conversation_id, lead_id in won:
        if conversation_id not in seen:
            seen.add(conversation_id)
            found.append(Candidate(tenant_id, conversation_id, "ganado", lead_id))
    for tenant_id, conversation_id in closed:
        if conversation_id not in seen:
            seen.add(conversation_id)
            found.append(Candidate(tenant_id, conversation_id, "cerrada"))
    return found


def why_not(session: Session, candidate: Candidate, *, now: datetime) -> str | None:
    conversation = session.get(Conversation, candidate.conversation_id)
    if conversation is None or conversation.tenant_id != candidate.tenant_id:
        return "no_existe"
    if conversation.channel != "whatsapp" or conversation.contact_id is None:
        return "simulador"
    if conversation.takeover_user_id is not None:
        return "tomada"
    contact = session.get(Contact, conversation.contact_id)
    if contact is None or contact.opted_out_at is not None:
        return "baja"
    if contact.last_inbound_at is None or now + WINDOW_MARGIN >= contact.last_inbound_at + WINDOW:
        return "ventana"
    recent = session.execute(
        select(CsatSurvey.id)
        .where(
            CsatSurvey.tenant_id == candidate.tenant_id,
            CsatSurvey.contact_id == contact.id,
            CsatSurvey.sent_at >= now - EVERY,
        )
        .limit(1)
    ).scalar_one_or_none()
    if recent is not None:
        return "reciente"
    return None


def prepare(session: Session, candidate: Candidate, *, now: datetime) -> Message | None:
    """Write the question `queued` and record the survey, or None if it does not go."""
    if why_not(session, candidate, now=now) is not None:
        return None
    conversation = session.get(Conversation, candidate.conversation_id)
    assert conversation is not None and conversation.contact_id is not None
    message = MessageRepository(session, candidate.tenant_id).append(
        conversation_id=conversation.id,
        direction="outbound",
        author="system",
        content=QUESTION,
        delivery_status="queued",
        payload={"motivo": "csat"},
    )
    session.add(
        CsatSurvey(
            tenant_id=candidate.tenant_id,
            conversation_id=conversation.id,
            contact_id=conversation.contact_id,
            lead_id=candidate.lead_id,
            operator_id=last_operator(session, candidate.tenant_id, conversation.id),
            message_id=message.id,
            trigger=candidate.trigger,
            sent_at=now,
        )
    )
    session.flush()
    return message


def pending_for(
    session: Session, tenant_id: uuid.UUID, contact_id: uuid.UUID, *, now: datetime
) -> CsatSurvey | None:
    return session.execute(
        select(CsatSurvey)
        .where(
            CsatSurvey.tenant_id == tenant_id,
            CsatSurvey.contact_id == contact_id,
            CsatSurvey.status == "sent",
            CsatSurvey.sent_at >= now - ANSWER_WITHIN,
        )
        .order_by(CsatSurvey.sent_at.desc())
        .limit(1)
    ).scalar_one_or_none()


@dataclass(frozen=True)
class Answered:
    thanks: Message | None
    # The assistant still answers the message (no score, or the customer also asked something).
    assistant: bool


def answer(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    contact: Contact,
    conversation: Conversation,
    text: str | None,
    at: datetime,
) -> Answered | None:
    """Apply a customer's message to their pending survey, if there is one."""
    survey = pending_for(session, tenant_id, contact.id, now=at)
    if survey is None:
        return None
    reading = read_answer(text)
    survey.status = "answered"
    survey.answered_at = at
    survey.score = reading.score
    survey.comment = reading.comment
    session.flush()
    if reading.score is None:
        return Answered(None, True)
    thanks = MessageRepository(session, tenant_id).append(
        conversation_id=conversation.id,
        direction="outbound",
        author="system",
        content=THANKS,
        delivery_status="queued",
        payload={"motivo": "csat_gracias"},
    )
    return Answered(thanks, reading.for_assistant)


def expire(session: Session, *, now: datetime) -> int:
    result = session.execute(
        update(CsatSurvey)
        .where(CsatSurvey.status == "sent", CsatSurvey.sent_at < now - ANSWER_WITHIN)
        .values(status="expired")
    )
    return int(getattr(result, "rowcount", 0) or 0)


def utcnow() -> datetime:
    return datetime.now(UTC)
