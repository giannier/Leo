"""The category table of the catalogue (Feature 004, Milestone B, decision 13).

A category is a row per tenant (name, order, active), linked from every product a
`ProductRepository` writes. Two businesses with the same category name have two
rows; one business's categories never show in the other's option list.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.business import Product, ProductCategory
from app.repositories.base import TenantScopeError
from app.repositories.product import ProductCategoryRepository, ProductRepository
from tests.isolation.conftest import TenantFixture


def _product(db: Session, fixture: TenantFixture, name: str, **values: Any) -> Product:
    values.setdefault("price", Decimal("10.00"))
    return ProductRepository(db, fixture.tenant_id).add(name=name, **values)


class TestEveryProductWriteLinksItsCategory:
    def test_a_new_category_text_creates_its_row(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        product = _product(db, tenant_a, "Curso SIAF", category="  Gestión   pública ")

        category = db.get(ProductCategory, product.category_id)
        assert category is not None
        assert (category.nombre, category.orden, category.activa) == ("Gestión pública", 0, True)
        assert product.category == "Gestión pública"

    def test_the_same_name_in_another_case_is_the_same_category(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        first = _product(db, tenant_a, "Curso SIAF", category="Gestión pública")
        second = _product(db, tenant_a, "Curso SIGA", category="GESTIÓN PÚBLICA")

        assert first.category_id == second.category_id
        # The first spelling is the category's name.
        assert second.category == "Gestión pública"

    def test_changing_or_clearing_the_category_moves_the_product(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        products = ProductRepository(db, tenant_a.tenant_id)
        product = _product(db, tenant_a, "Curso Excel", category="Ofimática")

        moved = products.update(product.id, category="Informática")
        assert moved is not None and moved.category == "Informática"
        informatica = ProductCategoryRepository(db, tenant_a.tenant_id).by_name("informática")
        assert informatica is not None and moved.category_id == informatica.id

        cleared = products.update(product.id, category="  ")
        assert cleared is not None
        assert (cleared.category, cleared.category_id) == (None, None)

    def test_a_name_is_unique_per_tenant_ignoring_case(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        categories = ProductCategoryRepository(db, tenant_a.tenant_id)
        categories.add(nombre="Cursos")

        with pytest.raises(IntegrityError):
            categories.add(nombre="cursos")
            db.flush()


class TestCategoriesArePerTenant:
    def test_the_same_name_in_two_businesses_is_two_rows(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = _product(db, tenant_a, "Curso", category="Cursos")
        theirs = _product(db, tenant_b, "Curso", category="Cursos")

        assert mine.category_id != theirs.category_id
        assert ProductCategoryRepository(db, tenant_b.tenant_id).get(mine.category_id) is None  # type: ignore[arg-type]

    def test_a_repository_cannot_create_a_category_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            ProductCategoryRepository(db, tenant_a.tenant_id).add(
                nombre="Ajena", tenant_id=tenant_b.tenant_id
            )

    def test_the_option_list_shows_only_this_tenants_active_categories_in_order(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _product(db, tenant_a, "Curso Excel", category="Ofimática")
        _product(db, tenant_a, "Curso SIAF", category="Gestión pública")
        _product(db, tenant_a, "Curso Word", category="Ofimática")
        _product(db, tenant_a, "Curso viejo", category="Archivo")
        _product(db, tenant_a, "Apagado", category="Oculta", is_active=False)
        _product(db, tenant_b, "Ajeno", category="De otro negocio")
        categories = ProductCategoryRepository(db, tenant_a.tenant_id)
        ofimatica = categories.by_name("Ofimática")
        archivo = categories.by_name("Archivo")
        assert ofimatica is not None and archivo is not None
        ofimatica.orden = -1  # the owner put it first
        archivo.activa = False  # and switched this one off
        db.flush()

        listed = ProductRepository(db, tenant_a.tenant_id).active_categories()

        assert listed == ["Ofimática", "Gestión pública"]
