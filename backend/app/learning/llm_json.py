"""Asking the model for a JSON answer, inside the weekly ceiling of the business (017).

Every call takes one from the ceiling before it is made, so a business that turned learning on
cannot spend more than it chose. A call whose answer is not JSON is still spent (the model
answered), and gives `None`: the caller treats it as «nothing learned», never as an error.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.learning.budget import LearningBudget
from app.providers.llm.base import ChatMessage, LLMError, LLMProvider

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class BudgetExhausted(Exception):
    """The weekly ceiling is reached: whatever needs a model call waits for the next Monday."""


def parse_json(text: str) -> Any | None:
    """The first JSON object or array in a model's answer, or None."""
    cleaned = _FENCE.sub("", text.strip())
    # Whichever opens first: an array of objects must not be read as its first object.
    openings = sorted(
        (cleaned.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if cleaned.find(o) != -1
    )
    for start, _opening, closing in openings:
        end = cleaned.rfind(closing)
        if end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except ValueError:
                continue
    return None


async def ask_text(
    llm: LLMProvider, budget: LearningBudget, system: str, user: str, *, max_tokens: int = 2000
) -> str | None:
    """One call: the answer as text. Raises `BudgetExhausted`; None if the model failed."""
    if not budget.spend():
        raise BudgetExhausted("Se llegó al tope de análisis de la semana.")
    try:
        result = await llm.chat(
            [ChatMessage("system", system), ChatMessage("user", user)],
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except LLMError:
        logger.warning("a learning call failed", exc_info=True)
        return None
    return result.content


async def ask_json(
    llm: LLMProvider, budget: LearningBudget, system: str, user: str, *, max_tokens: int = 1200
) -> Any | None:
    answer = await ask_text(llm, budget, system, user, max_tokens=max_tokens)
    return parse_json(answer) if answer else None
