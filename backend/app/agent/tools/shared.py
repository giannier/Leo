"""Helpers the sales tools have in common (Feature 004, TASK-420).

Money formatting in the business's own currency, WhatsApp-safe truncation, and how
a promotion reads in a tool result. None of it decides anything: the totals and
discounts are `pricing`'s (TASK-424); these only describe what is configured, in
the words and numbers the owner entered.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.sales_config import Promotion
from app.repositories.business import BusinessProfileRepository

CURRENCY_SYMBOLS: Mapping[str, str] = {"PEN": "S/", "USD": "US$"}
DEFAULT_CURRENCY = "PEN"

_ELLIPSIS = "…"


def currency_code(session: Session, tenant_id: uuid.UUID) -> str:
    """The ISO code of the business's currency (`business_profiles.currency`).

    A business that never filled in its profile is a Peruvian one (`PEN`): the
    column's own default.
    """
    profile = BusinessProfileRepository(session, tenant_id).current()
    return (profile.currency if profile else DEFAULT_CURRENCY) or DEFAULT_CURRENCY


def currency_symbol(session: Session, tenant_id: uuid.UUID) -> str:
    """The symbol of the business's currency (`business_profiles.currency`).

    An unknown code is printed as it is rather than guessed at.
    """
    code = currency_code(session, tenant_id)
    return CURRENCY_SYMBOLS.get(code, code)


def money(amount: Decimal, symbol: str) -> str:
    return f"{symbol} {amount:.2f}"


def clip(text: str, limit: int) -> str:
    """`text` on one line, at most `limit` characters, ending in an ellipsis if cut."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + _ELLIPSIS


def describe_promotion(promotion: Promotion, symbol: str) -> str:
    """The rule of a promotion as stored, in one phrase.

    Only what the owner configured: the amount actually taken off an order is the
    pricing module's to compute, and this text must never be read as a quote.
    """
    value = promotion.valor
    if promotion.tipo == "porcentaje" and value is not None:
        rule = f"{format(value.normalize(), 'f')} % de descuento"
    elif promotion.tipo == "monto_fijo" and value is not None:
        rule = f"{money(value, symbol)} de descuento"
    elif promotion.tipo == "nxm" and promotion.n and promotion.m:
        rule = f"lleva {promotion.n} y paga {promotion.m}"
    elif promotion.tipo == "precio_cantidad" and value is not None and promotion.m:
        rule = f"{promotion.m} por {money(value, symbol)}"
    else:
        rule = promotion.tipo
    if promotion.monto_minimo is not None:
        rule += f" (compra mínima {money(promotion.monto_minimo, symbol)})"
    return rule


def automatic(promotions: Iterable[Promotion]) -> list[Promotion]:
    """The promotions that apply by themselves, without the assistant offering them."""
    return [promotion for promotion in promotions if promotion.uso == "automatica"]


def applies_to(promotion: Promotion, product: Product) -> bool:
    """Whether the promotion's scope covers this product.

    Scope only: a minimum amount or a quantity condition depends on the order,
    which is why the result says "aplicable" and not "aplicada".
    """
    if promotion.aplica_a == "productos":
        return product.id in promotion.product_ids
    if promotion.aplica_a == "categorias":
        if not product.category:
            return False
        return product.category.casefold() in {name.casefold() for name in promotion.categorias}
    return True
