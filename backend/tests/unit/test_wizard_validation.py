"""The API's allowed values must match the database's CHECK constraints.

`app/api/v1/wizard.py` spells its `Literal` members out because `Literal`
needs them at type-check time. That duplication is the risk this file closes:
if somebody adds an industry to the model tuple and not to the endpoint, the
API silently rejects a value the database accepts - and the failure looks like
a validation bug rather than a missing update.
"""

from __future__ import annotations

import typing

from app.api.v1 import wizard
from app.models.business import AGENT_OBJECTIVES, AGENT_TONES
from app.services.wizard import WizardService


def _members(literal: object) -> set[str]:
    return set(typing.get_args(literal))


class TestTheApiMatchesTheSchema:
    def test_tones(self) -> None:
        assert _members(wizard.Tone) == set(AGENT_TONES)

    def test_objectives(self) -> None:
        assert _members(wizard.Objective) == set(AGENT_OBJECTIVES)


class TestDefaultsMatchTheColumns:
    def test_profile_defaults(self) -> None:
        """What a caller sees before the first save must be what the row would
        hold after it, or the wizard shows values that change on save for no
        reason the user can see."""
        assert wizard.PROFILE_DEFAULTS.currency == "PEN"
        assert wizard.PROFILE_DEFAULTS.timezone == "America/Lima"
        assert wizard.PROFILE_DEFAULTS.updated_at is None

    def test_agent_defaults(self) -> None:
        assert wizard.AGENT_DEFAULTS.agent_name == "Leo"
        assert wizard.AGENT_DEFAULTS.tone == "cercano"
        assert wizard.AGENT_DEFAULTS.objective == "ambos"
        assert wizard.AGENT_DEFAULTS.is_active is False
        assert wizard.AGENT_DEFAULTS.escalation_rules == [
            "cliente_molesto",
            "pide_humano",
            "fuera_de_catalogo",
        ]


class TestStalenessDetection:
    """`_is_stale` decides whether the user gets the "somebody else saved"
    warning. It never blocks a write - that is what last-write-wins means."""

    def test_no_expectation_means_no_warning(self) -> None:
        assert WizardService._is_stale(object(), None) is False

    def test_no_stored_row_means_no_warning(self) -> None:
        from datetime import UTC, datetime

        assert WizardService._is_stale(None, datetime.now(UTC)) is False

    def test_an_older_expectation_warns(self) -> None:
        from datetime import UTC, datetime, timedelta

        class Row:
            updated_at = datetime.now(UTC)

        assert WizardService._is_stale(Row(), Row.updated_at - timedelta(minutes=5)) is True

    def test_the_same_timestamp_does_not_warn(self) -> None:
        """A client echoing back exactly what it was given is not in conflict.

        ISO 8601 carries microseconds and pydantic parses them back, so the
        comparison is exact and needs no tolerance window - which would only
        suppress the warning for saves close together in time, when a
        collision is most likely.
        """
        from datetime import UTC, datetime

        class Row:
            updated_at = datetime.now(UTC)

        assert WizardService._is_stale(Row(), Row.updated_at) is False
