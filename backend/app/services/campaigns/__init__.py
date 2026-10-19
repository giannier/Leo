"""Campaigns, templates, opt-ins and template follow-ups (Feature 011)."""

from __future__ import annotations


class CampaignError(ValueError):
    """A request Feature 011 refuses, with a code for the API and a message in Spanish."""

    def __init__(self, code: str, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
