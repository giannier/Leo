"""Prepare the data of the Milestone B script in one command (`milestones/B.md`, section 2).

    make seed-milestone-b

Replaces the hand-run SQL of section 2. Safe to run again: nothing is duplicated.

* **Tienda Demo** (template `productos`): created with its own owner if it does not
  exist (`tienda.demo@leo.test`, password printed), with the three products of
  `B-catalogo-tienda.csv`, delivery to three zones with free shipping from S/ 150,
  the 2x1 promotion on the polo and a Yape payment method.
* **Escuela Capacita**: if it exists (Milestone A), it gets its Yape payment method.

Development only: it refuses to run in staging or production.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.security import hash_password
from app.models.business import AgentConfig, BusinessProfile, PaymentMethodBusiness, Product
from app.models.sales_config import Promotion
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.product import ProductRepository
from app.repositories.sales_config import BusinessInfoRepository
from app.repositories.tenant import TenantRepository, TenantUserRepository

SHOP = "Tienda Demo"
SCHOOL = "Escuela Capacita"
SHOP_EMAIL = "tienda.demo@leo.test"
SHOP_PASSWORD = "tienda-demo-2026"

PRODUCTS = (
    ("Polo básico", Decimal("35.00"), True),
    ("Gorra urbana", Decimal("25.00"), False),
    ("Mochila ejecutiva", Decimal("120.00"), False),
)
DELIVERIES = {
    "hace_delivery": True,
    "zonas": [
        {"nombre": "Miraflores", "costo": "8.00", "tiempo": "24 h"},
        {"nombre": "San Isidro", "costo": "8.00", "tiempo": "24 h"},
        {"nombre": "Callao", "costo": "12.00", "tiempo": "24 a 48 h"},
    ],
    "envio_gratis_desde": "150.00",
    "recojo_en_tienda": False,
    "notas": "Entregas de lunes a sábado.",
}
YAPE = {
    SHOP: (
        "Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del "
        "comprobante por aquí."
    ),
    SCHOOL: (
        "Yape al 987654321 a nombre de Escuela Capacita. Cuando pagues, envía la foto del "
        "comprobante por aquí."
    ),
}


def _tenant(session: Session, name: str) -> Tenant | None:
    return session.execute(select(Tenant).where(Tenant.name == name)).scalars().first()


def _shop(session: Session) -> Tenant:
    tenant = _tenant(session, SHOP)
    if tenant is not None:
        print(f"  {SHOP}: ya existe, se completa lo que falte")
        return tenant
    user = session.execute(select(User).where(User.email == SHOP_EMAIL)).scalars().first()
    if user is None:
        user = User(
            email=SHOP_EMAIL,
            password_hash=hash_password(SHOP_PASSWORD),
            full_name="Dueño Tienda Demo",
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
    tenant = TenantRepository(session).create_trial(
        name=SHOP, plan_id="trial", trial_ends_at=datetime.now(UTC) + timedelta(days=14)
    )
    TenantUserRepository(session, tenant.id).add_member(user_id=user.id, role="owner")
    session.add_all(
        [
            BusinessProfile(
                tenant_id=tenant.id,
                business_name=SHOP,
                industry="retail",
                description="Tienda de ropa y accesorios urbanos en Lima.",
                currency="PEN",
            ),
            AgentConfig(tenant_id=tenant.id, agent_name="Leo", is_active=True),
        ]
    )
    session.flush()
    print(f"  {SHOP}: creada. Entra con {SHOP_EMAIL} / {SHOP_PASSWORD}")
    return tenant


def _catalogue(session: Session, tenant: Tenant) -> Product:
    products = ProductRepository(session, tenant.id)
    existing = {
        p.name: p
        for p in session.execute(
            select(Product).where(Product.tenant_id == tenant.id, Product.deleted_at.is_(None))
        ).scalars()
    }
    for name, price, featured in PRODUCTS:
        if name not in existing:
            existing[name] = products.add(
                name=name,
                price=price,
                category="Ropa" if name != "Mochila ejecutiva" else "Accesorios",
                es_digital=False,
                destacado=featured,
            )
    print("  catálogo: 3 productos")
    return existing["Polo básico"]


def _promotion(session: Session, tenant: Tenant, polo: Product) -> None:
    name = "2x1 en polos"
    found = session.execute(
        select(Promotion).where(Promotion.tenant_id == tenant.id, Promotion.nombre == name)
    ).scalars()
    if found.first() is None:
        session.add(
            Promotion(
                tenant_id=tenant.id,
                nombre=name,
                descripcion="Llevas 2 polos y pagas 1",
                uso="automatica",
                tipo="nxm",
                n=2,
                m=1,
                aplica_a="productos",
                product_ids=[polo.id],
            )
        )
    print("  promoción: 2x1 en polos")


def _yape(session: Session, tenant: Tenant) -> None:
    found = session.execute(
        select(PaymentMethodBusiness).where(
            PaymentMethodBusiness.tenant_id == tenant.id, PaymentMethodBusiness.method == "yape"
        )
    ).scalars()
    if found.first() is None:
        session.add(
            PaymentMethodBusiness(
                tenant_id=tenant.id, method="yape", instructions=YAPE[tenant.name]
            )
        )
    print(f"  método de pago Yape de {tenant.name}")


def seed(session: Session) -> Tenant:
    """Load everything; the caller commits. Returns the shop."""
    shop = _shop(session)
    polo = _catalogue(session, shop)
    BusinessInfoRepository(session, shop.id).upsert("entregas", dict(DELIVERIES))
    print("  entregas: Miraflores, San Isidro, Callao; envío gratis desde S/ 150")
    _promotion(session, shop, polo)
    _yape(session, shop)
    school = _tenant(session, SCHOOL)
    if school is None:
        print(f"  {SCHOOL}: no existe (se crea en el Milestone A); se omite")
    else:
        _yape(session, school)
    session.flush()
    return shop


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    if get_settings().environment not in ("development", "test"):
        print("Solo para desarrollo: este script no corre en staging ni en producción.")
        return 1
    with get_session_factory()() as session:
        seed(session)
        session.commit()
    print("Listo. Si ya usabas el simulador, espera 60 s o reinicia el backend (caché).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
