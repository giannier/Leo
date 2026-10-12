"""Media library (Feature 004, TASK-405).

Two businesses use the same keys (`saludo`, `metodos_pago`), so a lookup by key
that forgets the tenant sends one business's file to the other's customer. The
constraints matter as much as the filter: a duplicate key inside one business
must fail, the same key in another must not, and removing a file must never
remove the product, promotion or payment method that referenced it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.business import PaymentMethodBusiness
from app.models.sales_config import Promotion
from app.repositories.base import TenantScopeError
from app.repositories.media import MediaAssetRepository
from tests.isolation.conftest import TenantFixture


def _asset(clave: str = "saludo", **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "clave": clave,
        "tipo": "imagen",
        "titulo": f"Titulo {clave}",
        "cuando_usar": "Al saludar al cliente",
        "storage_key": f"media/{uuid.uuid4()}.png",
        "filename": f"{clave}.png",
        "mime_type": "image/png",
        "size_bytes": 1024,
    }
    values.update(overrides)
    return values


@pytest.fixture
def seeded(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> None:
    MediaAssetRepository(db, tenant_a.tenant_id).add(**_asset("saludo", titulo="Saludo A"))
    MediaAssetRepository(db, tenant_a.tenant_id).add(**_asset("solo_a"))
    MediaAssetRepository(db, tenant_b.tenant_id).add(**_asset("saludo", titulo="Saludo B"))


class TestMediaAssetsArePerTenant:
    def test_the_same_key_reads_your_own_file(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        mine = MediaAssetRepository(db, tenant_a.tenant_id).by_clave("saludo")
        theirs = MediaAssetRepository(db, tenant_b.tenant_id).by_clave("saludo")

        assert mine is not None and theirs is not None
        assert (mine.titulo, theirs.titulo) == ("Saludo A", "Saludo B")

    def test_a_key_only_the_other_tenant_has_is_not_found(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        assert MediaAssetRepository(db, tenant_b.tenant_id).by_clave("solo_a") is None

    def test_listing_and_counting_are_scoped(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        repository = MediaAssetRepository(db, tenant_b.tenant_id)

        assert [row.clave for row in repository.active()] == ["saludo"]
        assert repository.count() == 1

    def test_reading_another_tenants_asset_by_id_finds_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        mine = MediaAssetRepository(db, tenant_a.tenant_id).by_clave("saludo")
        assert mine is not None

        repository = MediaAssetRepository(db, tenant_b.tenant_id)
        assert repository.get(mine.id) is None
        assert repository.update(mine.id, titulo="Robado") is None
        assert repository.delete(mine.id) is False
        db.refresh(mine)
        assert mine.titulo == "Saludo A"

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            MediaAssetRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, **_asset()
            )

    def test_linked_files_are_found_by_product_and_promotion(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        promotion = Promotion(
            tenant_id=tenant_a.tenant_id, nombre="Dos por uno", tipo="monto_fijo", valor=Decimal(5)
        )
        db.add(promotion)
        db.flush()
        repository = MediaAssetRepository(db, tenant_a.tenant_id)
        repository.add(**_asset("ficha", product_id=tenant_a.product_id))
        repository.add(**_asset("afiche", promotion_id=promotion.id))
        repository.add(**_asset("inactivo", product_id=tenant_a.product_id, is_active=False))

        assert [a.clave for a in repository.for_product(tenant_a.product_id)] == ["ficha"]
        assert [a.clave for a in repository.for_promotion(promotion.id)] == ["afiche"]
        # The other tenant asks for the same product id and gets nothing.
        other = MediaAssetRepository(db, tenant_b.tenant_id)
        assert other.for_product(tenant_a.product_id) == []
        assert other.for_promotion(promotion.id) == []


class TestKeyUniqueness:
    def test_a_duplicate_key_in_the_same_tenant_fails(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repository = MediaAssetRepository(db, tenant_a.tenant_id)
        repository.add(**_asset("saludo"))

        with pytest.raises(IntegrityError), db.begin_nested():
            repository.add(**_asset("saludo"))

    def test_the_same_key_in_another_tenant_is_accepted(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        MediaAssetRepository(db, tenant_a.tenant_id).add(**_asset("saludo"))
        other = MediaAssetRepository(db, tenant_b.tenant_id).add(**_asset("saludo"))

        assert other.tenant_id == tenant_b.tenant_id


class TestRulesLiveInTheDatabase:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"clave": "a"},
            {"clave": "a" * 41},
            {"clave": "Mayuscula"},
            {"clave": "con espacio"},
            {"clave": "con-guion"},
            {"tipo": "sticker"},
            {"cuando_usar": "abcd"},
            {"cuando_usar": "x" * 301},
            {"caption": "x" * 1025},
            {"size_bytes": -1},
        ],
    )
    def test_an_invalid_value_is_rejected(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            MediaAssetRepository(db, tenant_a.tenant_id).add(**_asset(**overrides))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"clave": "ab"},
            {"clave": "a" * 40},
            {"tipo": "documento"},
            {"tipo": "video"},
            {"tipo": "audio"},
            {"cuando_usar": "x" * 5},
            {"cuando_usar": "x" * 300},
            {"caption": "x" * 1024},
            {"caption": None},
            {"size_bytes": 0},
        ],
    )
    def test_the_limits_themselves_are_accepted(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        asset = MediaAssetRepository(db, tenant_a.tenant_id).add(**_asset(**overrides))

        for field, value in overrides.items():
            assert getattr(asset, field) == value

    def test_a_new_asset_starts_active_with_no_provider_copy(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        asset = MediaAssetRepository(db, tenant_a.tenant_id).add(**_asset())
        db.refresh(asset)

        assert asset.is_active is True
        assert asset.provider_media_id is None
        assert asset.provider_media_expires_at is None
        assert asset.created_at is not None


class TestProviderCopy:
    def test_store_and_clear(self, db: Session, tenant_a: TenantFixture) -> None:
        repository = MediaAssetRepository(db, tenant_a.tenant_id)
        asset = repository.add(**_asset())
        expires = datetime.now(UTC) + timedelta(days=30)

        repository.store_provider_media(asset, media_id="wamid-1", expires_at=expires)
        assert (asset.provider_media_id, asset.provider_media_expires_at) == ("wamid-1", expires)

        repository.clear_provider_media(asset)
        assert asset.provider_media_id is None
        assert asset.provider_media_expires_at is None

    def test_expiring_before_lists_only_uploaded_active_assets_of_this_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        now = datetime.now(UTC)
        soon, later = now + timedelta(hours=10), now + timedelta(days=20)
        mine = MediaAssetRepository(db, tenant_a.tenant_id)
        theirs = MediaAssetRepository(db, tenant_b.tenant_id)
        mine.add(**_asset("vence", provider_media_id="m1", provider_media_expires_at=soon))
        mine.add(**_asset("vigente", provider_media_id="m2", provider_media_expires_at=later))
        mine.add(**_asset("sin_subir"))
        mine.add(
            **_asset(
                "apagado",
                provider_media_id="m3",
                provider_media_expires_at=soon,
                is_active=False,
            )
        )
        theirs.add(**_asset("vence", provider_media_id="m4", provider_media_expires_at=soon))

        cutoff = now + timedelta(hours=48)
        assert [a.clave for a in mine.expiring_before(cutoff)] == ["vence"]
        assert [a.provider_media_id for a in theirs.expiring_before(cutoff)] == ["m4"]


class TestDeletingAFileKeepsWhatReferencedIt:
    def test_a_payment_method_keeps_existing_without_its_image(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        asset = MediaAssetRepository(db, tenant_a.tenant_id).add(**_asset("qr_yape"))
        method = PaymentMethodBusiness(
            tenant_id=tenant_a.tenant_id,
            method="yape",
            instructions="Yape al 999",
            media_asset_id=asset.id,
        )
        db.add(method)
        db.flush()

        assert MediaAssetRepository(db, tenant_a.tenant_id).delete(asset.id) is True
        db.refresh(method)

        assert method.media_asset_id is None

    def test_a_payment_method_cannot_point_at_a_missing_asset(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                PaymentMethodBusiness(
                    tenant_id=tenant_a.tenant_id,
                    method="yape",
                    instructions="Yape al 999",
                    media_asset_id=uuid.uuid4(),
                )
            )
            db.flush()

    def test_deleting_the_product_keeps_the_asset_unlinked(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        asset = MediaAssetRepository(db, tenant_a.tenant_id).add(
            **_asset("ficha", product_id=tenant_a.product_id)
        )
        assert asset.product_id == tenant_a.product_id
        db.execute(text("DELETE FROM products WHERE id = :id"), {"id": tenant_a.product_id})
        db.expire_all()
        kept = MediaAssetRepository(db, tenant_a.tenant_id).by_clave("ficha")

        assert kept is not None
        assert kept.product_id is None
