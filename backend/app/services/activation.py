"""The activation checklist (TASK-013, Clarification 009).

**Computed, never stored.** A stored checklist is a second copy of the truth,
and the moment somebody deletes their last product without going through the
wizard the two disagree - with the stored one saying the tenant is ready. So
this reads the entities and answers; there is no table and nothing to keep in
sync.

**Pure.** No writes, no clock beyond what it is handed, no side effects. That
is what lets the same function serve the endpoint the customer polls and the
guard that refuses to activate, with no chance of the two disagreeing about
what "ready" means - the failure that makes a wizard say "all set" next to a
button that refuses to work.

Two thresholds, not one:

* `sandbox_ready` - items 1 to 5. Enough to talk to your own agent in a
  private sandbox, which is where the customer decides the product is worth
  paying for. Deliberately cheap to reach.
* `activation_ready` - everything, plus a connected WhatsApp number. This one
  puts the agent in front of real customers, so it is allowed to be strict.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.user import User
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.payment import PaymentMethodRepository
from app.repositories.product import ProductRepository
from app.repositories.tenant import OWNER_ROLE, TenantUserRepository
from app.repositories.user import UserRepository
from app.repositories.whatsapp import WhatsAppNumberRepository

ChecklistKey = Literal[
    "email_verified",
    "business_profile_complete",
    "agent_config_complete",
    "has_active_product",
    "has_payment_method",
    "phone_verified",
]

# Items 1-5. Item 6 (phone) gates the *connection request*, not the sandbox
# (Clarification 005), so a customer can try their agent before proving a
# phone number they may not even have chosen yet.
SANDBOX_ITEMS: tuple[ChecklistKey, ...] = (
    "email_verified",
    "business_profile_complete",
    "agent_config_complete",
    "has_active_product",
    "has_payment_method",
)

# Objectives that make a payment method necessary. An agent that only answers
# questions has nothing to charge for, so demanding payment instructions would
# block a perfectly valid setup on a field it will never use.
SELLING_OBJECTIVES = ("vender", "ambos")


@dataclass(frozen=True)
class ChecklistItem:
    key: ChecklistKey
    done: bool
    # False for an item that does not apply to this tenant's configuration.
    # It is still reported, so the panel can show it greyed rather than making
    # the list change length as the customer edits their agent.
    required: bool = True


@dataclass(frozen=True)
class ActivationChecklist:
    items: tuple[ChecklistItem, ...]
    sandbox_ready: bool
    activation_ready: bool
    # Why activation is refused, when it is. `None` when it is not.
    blocker: Literal["checklist_incomplete", "number_not_connected"] | None

    def item(self, key: ChecklistKey) -> ChecklistItem:
        return next(entry for entry in self.items if entry.key == key)


def _profile_complete(profile: BusinessProfile | None) -> bool:
    """A business the agent can introduce.

    Name and industry only. `description` reads like the most important field
    and is deliberately not required: it is the one customers leave for later,
    and blocking the sandbox on prose would stop them reaching the moment that
    sells the product.
    """
    if profile is None:
        return False
    return bool(profile.business_name and profile.industry)


def _agent_complete(config: AgentConfig | None) -> bool:
    """A configured personality, not merely a default one.

    Every column here has a default, so a row always looks complete. The
    signal is that the customer chose a name - the first thing the wizard asks
    and the one field with no sensible automatic answer.
    """
    if config is None:
        return False
    return bool(config.agent_name and config.tone and config.objective)


def build_checklist(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user: User,
) -> ActivationChecklist:
    """Read the tenant's state and report what is missing.

    `user` is the caller, because item 1 and item 6 are properties of a person
    rather than of the business: a tenant is ready when *this* member has
    verified their address, and the owner's phone is what a connection request
    is filed against.
    """
    profile = BusinessProfileRepository(session, tenant_id).current()
    config = AgentConfigRepository(session, tenant_id).current()
    products = ProductRepository(session, tenant_id)
    numbers = WhatsAppNumberRepository(session, tenant_id)

    payments = PaymentMethodRepository(session, tenant_id)

    objective = config.objective if config is not None else "ambos"
    payment_required = objective in SELLING_OBJECTIVES

    has_product = products.count(Product.deleted_at.is_(None), Product.is_active.is_(True)) > 0
    has_payment = payments.count_active() > 0

    items = (
        ChecklistItem("email_verified", user.email_verified_at is not None),
        ChecklistItem("business_profile_complete", _profile_complete(profile)),
        ChecklistItem("agent_config_complete", _agent_complete(config)),
        ChecklistItem("has_active_product", has_product),
        ChecklistItem(
            "has_payment_method",
            # An item that does not apply is reported done, so it neither
            # blocks nor shows as an outstanding task the customer cannot
            # resolve.
            has_payment or not payment_required,
            required=payment_required,
        ),
        ChecklistItem("phone_verified", user.phone_verified_at is not None),
    )

    by_key = {entry.key: entry for entry in items}
    sandbox_ready = all(by_key[key].done for key in SANDBOX_ITEMS)
    everything_done = all(entry.done for entry in items)
    number_connected = numbers.has_connected()

    activation_ready = everything_done and number_connected

    blocker: Literal["checklist_incomplete", "number_not_connected"] | None = None
    if not everything_done:
        # Reported before the number, because a customer with neither should
        # be sent to fix the part they can fix themselves; connecting a number
        # needs us.
        blocker = "checklist_incomplete"
    elif not number_connected:
        blocker = "number_not_connected"

    return ActivationChecklist(
        items=items,
        sandbox_ready=sandbox_ready,
        activation_ready=activation_ready,
        blocker=blocker,
    )


def owner_of(session: Session, tenant_id: uuid.UUID) -> User | None:
    """The tenant's owner, for a checklist not tied to the caller.

    Needed by jobs and by admin views, where there is no signed-in member to
    evaluate items 1 and 6 against.
    """
    members = TenantUserRepository(session, tenant_id)
    for membership in members.list_members():
        if membership.role == OWNER_ROLE:
            return UserRepository(session).get(membership.user_id)
    return None
