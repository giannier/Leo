"""Questions the assistant could not answer: counted, then proposed as a fact to add (017, RF-019).

No model call: when `consultar_conocimiento` finds nothing, the question is kept (with the personal
data the rules can see already taken out, and only if the business turned learning on). A question
asked at least twice becomes a suggestion to add the answer; the answer itself is always written by
the reviewer, because the assistant must not invent what the business answers.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy.orm import Session

from app.learning.anonymizer import redact
from app.learning.suggestions import Proposal, SuggestionService, fingerprint
from app.repositories.learning import LearningSettingsRepository, UnansweredQuestionRepository

logger = logging.getLogger(__name__)

# Asked this many times it is worth adding the answer.
MIN_ASKED = 2
MIN_LENGTH = 6
MAX_LENGTH = 200


def record_unanswered(
    session: Session, tenant_id: uuid.UUID, text: str, conversation_id: uuid.UUID | None
) -> bool:
    """Count one more time that `text` was asked and not answered. Never raises: a note about a
    question must not cost the customer their answer."""
    settings = LearningSettingsRepository(session, tenant_id).current()
    if settings is None or not settings.activo:
        return False
    cleaned = " ".join(redact(text).split())[:MAX_LENGTH]
    # Mostly personal data, or nothing to learn from: fewer than two real words are left.
    words = re.findall(r"[^\W\d_]{3,}", re.sub(r"\[[^\]]+\]", " ", cleaned))
    if len(cleaned) < MIN_LENGTH or len(words) < 2:
        return False
    try:
        with session.begin_nested():
            UnansweredQuestionRepository(session, tenant_id).record(
                fingerprint("pregunta", cleaned), cleaned, conversation_id
            )
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("could not note an unanswered question", exc_info=True)
        return False
    return True


def suggest_from_questions(session: Session, tenant_id: uuid.UUID) -> int:
    """Turn the questions asked often enough into suggestions; returns how many were created."""
    settings = LearningSettingsRepository(session, tenant_id).current()
    if settings is None or not settings.activo:
        return 0
    service = SuggestionService(session, tenant_id)
    created = 0
    for question in UnansweredQuestionRepository(session, tenant_id).due(MIN_ASKED):
        suggestion = service.propose(
            Proposal(
                tipo="dato",
                fuente="pregunta_sin_respuesta",
                resumen=(
                    f"Te preguntaron «{question.pregunta}» {question.veces} veces "
                    "y no hubo respuesta"
                ),
                propuesta={"pregunta": question.pregunta},
                key=question.pregunta,
                conversaciones=list(question.conversaciones),
            )
        )
        question.estado = "sugerida"
        created += 1 if suggestion is not None else 0
    session.flush()
    return created
