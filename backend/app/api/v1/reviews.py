"""The weekly review and the questions to try (feature 019, phase C).

    GET /assistant/review                  this week's sample of real conversations, and the results
    PUT /assistant/review/{conversation}   rate one: bien, regular or mal, why, and a note
    GET /assistant/test-questions          questions made from the business data, for the simulator

For the reviewers of the business (the owner, the admins and whoever has the permission): what
they read are real customers' conversations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ReviewerDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.services import reviews, test_questions
from app.services.crm_report import contact_name

router = APIRouter(prefix="/assistant", tags=["quality"])

AUTH = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)


class Line(BaseModel):
    de: str  # "cliente", "asistente" or "equipo"
    texto: str
    en: datetime


class Mark(BaseModel):
    rating: int
    motivos: list[str]
    nota: str | None


class ReviewItem(BaseModel):
    conversation_id: uuid.UUID
    cliente: str
    canal: str
    mensajes: list[Line]
    revision: Mark | None


class Progress(BaseModel):
    revisadas: int
    total: int


class Summary(BaseModel):
    total: int
    bien: int
    regular: int
    mal: int
    porcentaje_bien: int | None
    fallas: dict[str, int]


class ReviewPage(BaseModel):
    semana: str
    progreso: Progress
    items: list[ReviewItem]
    motivos: list[str]
    resumen: Summary


class ReviewIn(BaseModel):
    rating: int = Field(ge=-1, le=1)
    motivos: list[str] = Field(default_factory=list, max_length=6)
    nota: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class TestQuestionOut(BaseModel):
    texto: str
    sobre: str


class TestQuestions(BaseModel):
    items: list[TestQuestionOut]


def _who(item: reviews.Reviewable) -> str:
    contact = item.contact
    if contact is None:
        return "Cliente"
    named = contact_name(contact)
    if named:
        return named
    if contact.phone_e164:
        return f"{contact.phone_e164[:5]}…{contact.phone_e164[-3:]}"
    return "Visitante del chat web"


def _lines(item: reviews.Reviewable) -> list[Line]:
    out = []
    for m in item.excerpt:
        who = (
            "cliente"
            if m.direction == "inbound"
            else ("equipo" if m.author == "human_operator" else "asistente")
        )
        out.append(Line(de=who, texto=m.content or "", en=m.created_at))
    return out


@router.get("/review", responses=error_responses(*AUTH))
def sample(principal: ReviewerDep, session: SessionDep) -> ReviewPage:
    now = datetime.now(UTC)
    chosen = reviews.sample(session, principal.tenant_id, now)
    items = [
        ReviewItem(
            conversation_id=r.conversation.id,
            cliente=_who(r),
            canal=r.conversation.channel,
            mensajes=_lines(r),
            revision=(
                Mark(rating=r.review.rating, motivos=list(r.review.motivos), nota=r.review.nota)
                if r.review
                else None
            ),
        )
        for r in chosen
    ]
    return ReviewPage(
        semana=reviews.week_label(now),
        progreso=Progress(revisadas=sum(1 for r in chosen if r.review), total=len(chosen)),
        items=items,
        motivos=list(reviews.MOTIVOS),
        resumen=Summary.model_validate(reviews.summary(session, principal.tenant_id, now)),
    )


@router.put(
    "/review/{conversation_id}",
    responses=error_responses(*AUTH, (404, "`not_found`"), (422, "`invalid_rating`")),
)
def rate(
    conversation_id: uuid.UUID, body: ReviewIn, principal: ReviewerDep, session: SessionDep
) -> Mark:
    try:
        row = reviews.rate(
            session,
            principal.tenant_id,
            conversation_id,
            rating=body.rating,
            motivos=body.motivos,
            nota=body.nota,
            user_id=principal.user_id,
        )
    except reviews.ReviewError as exc:
        raise ApiError(
            status_code=404 if exc.code == "not_found" else 422, error=exc.code, message=str(exc)
        ) from exc
    session.commit()
    return Mark(rating=row.rating, motivos=list(row.motivos), nota=row.nota)


@router.get("/test-questions", responses=error_responses(*AUTH))
def questions(principal: ReviewerDep, session: SessionDep) -> TestQuestions:
    return TestQuestions(
        items=[
            TestQuestionOut(texto=q.texto, sobre=q.sobre)
            for q in test_questions.build(session, principal.tenant_id)
        ]
    )
