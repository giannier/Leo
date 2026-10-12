"""How many outbound messages one turn may send (Feature 004, C-11).

Every outbound message costs money and several in a row look bad on a phone, so a
turn has a budget of three, and one is always kept for the text of the reply.
The tools that send something (a resource, the options, the payment methods)
spend from what is left after that reservation; when it runs out they answer
`no_disponible` and the model offers the thing in its next message.

The budget is mutable on purpose: one instance travels with the turn, the tools
spend from it and the formatter reads what is left to decide how many bubbles the
text may use.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MAX_MESSAGES = 3
DEFAULT_TEXT_RESERVE = 1


@dataclass
class TurnBudget:
    """Outbound messages for one turn: `max` in total, `reserva_texto` of them for the text."""

    max: int = DEFAULT_MAX_MESSAGES
    reserva_texto: int = DEFAULT_TEXT_RESERVE
    _spent: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max < 1:
            raise ValueError("a turn needs a budget of at least one message")
        if not 0 <= self.reserva_texto <= self.max:
            raise ValueError("the text reserve must be between 0 and the budget")

    @property
    def spent(self) -> int:
        """Messages sent by tools so far this turn."""
        return self._spent

    @property
    def remaining_for_tools(self) -> int:
        """How many more messages a tool may send without touching the text reserve."""
        return max(0, self.max - self.reserva_texto - self._spent)

    def can_consume(self) -> bool:
        return self.remaining_for_tools > 0

    def consume(self) -> bool:
        """Spend one message on a tool. False (and nothing spent) if none is left."""
        if not self.can_consume():
            return False
        self._spent += 1
        return True

    def remaining_text_bubbles(self) -> int:
        """How many bubbles the final text may use: everything the tools left over.

        With nothing spent that is the whole budget; once two things went out it
        is one. The formatter then caps it by the detail level (at most two).
        """
        return self.max - self._spent
