"""The admin router's literals must track the model enums (TASK-018).

`Literal` needs literal members to type-check, so the accepted statuses are
spelled out in the router rather than derived from the model. That duplication
is fine only while something fails when it drifts: adding a fifth request
status and forgetting the router would otherwise produce an endpoint that
silently rejects a status the database accepts.
"""

from __future__ import annotations

from typing import get_args

from app.api.v1.platform_admin import RequestStatus, TenantStatus
from app.models.tenant import TENANT_STATUSES
from app.models.whatsapp import REQUEST_STATUSES
from app.services.admin import ALLOWED_TRANSITIONS


def test_request_statuses_match_the_model() -> None:
    assert set(get_args(RequestStatus)) == set(REQUEST_STATUSES)


def test_tenant_statuses_match_the_model() -> None:
    assert set(get_args(TenantStatus)) == set(TENANT_STATUSES)


def test_every_request_status_has_a_transition_rule() -> None:
    """A status with no entry falls through `.get(..., ())` and becomes
    silently terminal - which is the right default, but only if it was
    chosen rather than forgotten."""
    assert set(ALLOWED_TRANSITIONS) == set(REQUEST_STATUSES)


def test_no_transition_leads_out_of_a_terminal_status() -> None:
    """A connected number is live and a rejected request has been answered.
    Reopening either would let one number serve two businesses."""
    assert ALLOWED_TRANSITIONS["connected"] == ()
    assert ALLOWED_TRANSITIONS["rejected"] == ()


def test_every_destination_is_itself_a_real_status() -> None:
    destinations = {status for targets in ALLOWED_TRANSITIONS.values() for status in targets}
    assert destinations <= set(REQUEST_STATUSES)
