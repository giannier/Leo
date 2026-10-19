"""Templates: written with names, sent to Meta with positions, reviewed by webhook (011)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.campaigns import Campaign
from app.providers.whatsapp.base import TemplateStatusUpdate
from app.services.campaigns import CampaignError
from app.services.campaigns.library import library_for
from app.services.campaigns.templates import (
    MARKETING_FOOTER,
    TemplateService,
    apply_review,
    meta_name,
    positional,
    rejection_hint,
)
from tests.integration.campaign_fixtures import WABA, FakeWhatsApp, business, template


class TestWriting:
    def test_named_variables_are_read_in_order(self, db: Session) -> None:
        tenant, _ = business(db)
        created = TemplateService(db, tenant.id).create(
            {
                "label": "Volvió el pan de yema",
                "category": "marketing",
                "body": (
                    "Hola {{nombre}}, volvió {{producto}} a {{negocio}}. ¡Te esperamos {{nombre}}!"
                ),
            }
        )
        assert created.variables == ["nombre", "producto", "negocio"]
        assert created.name == "volvio_el_pan_de_yema"
        assert created.status == "draft"
        # Meta asks marketing messages to say how to stop them.
        assert created.footer == MARKETING_FOOTER

    def test_a_utility_template_keeps_no_footer(self, db: Session) -> None:
        tenant, _ = business(db)
        created = TemplateService(db, tenant.id).create(
            {
                "label": "Pago",
                "category": "utility",
                "body": "Hola {{nombre}}, tu pago está pendiente.",
            }
        )
        assert created.footer is None

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            ("Hola {{apellido}}, tenemos promo.", "variable_desconocida"),
            ("{{nombre}}, tenemos promo.", "variable_en_borde"),
            ("Tenemos promo para {{nombre}}", "variable_en_borde"),
            ("   ", "texto_vacio"),
        ],
    )
    def test_what_meta_would_reject_is_refused_here(
        self, db: Session, body: str, code: str
    ) -> None:
        tenant, _ = business(db)
        with pytest.raises(CampaignError) as caught:
            TemplateService(db, tenant.id).create(
                {"label": "Promo", "category": "marketing", "body": body}
            )
        assert caught.value.code == code

    def test_names_are_unique_within_the_business(self) -> None:
        assert meta_name("Promo Julio", {"promo_julio"}) == "promo_julio_2"
        assert meta_name("¡Ñandú!", set()) == "nandu"

    def test_positions_follow_the_variables(self) -> None:
        assert (
            positional("Hola {{nombre}}, {{negocio}} te saluda, {{nombre}}.", ["nombre", "negocio"])
            == "Hola {{1}}, {{2}} te saluda, {{1}}."
        )


class TestReview:
    async def test_submit_sends_positions_and_examples(self, db: Session) -> None:
        tenant, _ = business(db, name="Panadería Rosa")
        draft = template(db, tenant, status="draft")
        fake = FakeWhatsApp()

        await TemplateService(db, tenant.id).submit(draft, provider=fake, storage=None)

        assert draft.status == "pending" and draft.provider_template_id == "9001"
        [spec] = fake.submitted
        assert spec.body == "Hola {{1}}, en {{2}} tenemos 2x1 esta semana."
        assert spec.examples == ("Ana", "Panadería Rosa")
        assert spec.category == "marketing"

    async def test_submit_needs_a_connected_number(self, db: Session) -> None:
        tenant, number = business(db)
        number.status = "disconnected"
        draft = template(db, tenant, status="draft")
        with pytest.raises(CampaignError) as caught:
            await TemplateService(db, tenant.id).submit(
                draft, provider=FakeWhatsApp(), storage=None
            )
        assert caught.value.code == "sin_numero"

    def test_the_webhook_approves_or_rejects_with_a_hint(self, db: Session) -> None:
        tenant, _ = business(db)
        pending = template(db, tenant, status="pending")

        apply_review(
            db,
            TemplateStatusUpdate(
                waba_id=WABA,
                name=pending.name,
                language="es",
                status="rejected",
                reason="INCORRECT_CATEGORY",
            ),
        )
        assert pending.status == "rejected" and pending.rejection_reason == "INCORRECT_CATEGORY"
        assert "Marketing" in (rejection_hint(pending.rejection_reason) or "")

        apply_review(
            db,
            TemplateStatusUpdate(waba_id=WABA, name=pending.name, language="es", status="approved"),
        )
        assert pending.status == "approved" and pending.rejection_reason is None

    def test_a_review_for_another_account_touches_nothing(self, db: Session) -> None:
        tenant, _ = business(db, waba="999")
        pending = template(db, tenant, status="pending")
        apply_review(
            db,
            TemplateStatusUpdate(waba_id=WABA, name=pending.name, language="es", status="approved"),
        )
        assert pending.status == "pending"

    def test_two_businesses_of_one_account_both_get_the_verdict(self, db: Session) -> None:
        first, _ = business(db, waba="holding-1")
        second, _ = business(db, waba="holding-1")
        a = template(db, first, status="pending", label="Promo holding")
        b = template(db, second, status="pending", label="Promo holding")
        apply_review(
            db,
            TemplateStatusUpdate(
                waba_id="holding-1", name=a.name, language="es", status="approved"
            ),
        )
        assert a.status == b.status == "approved"


class TestLifecycle:
    def test_an_approved_template_is_duplicated_not_edited(self, db: Session) -> None:
        tenant, _ = business(db)
        approved = template(db, tenant)
        service = TemplateService(db, tenant.id)
        with pytest.raises(CampaignError) as caught:
            service.update(
                approved, {"label": "x", "category": "marketing", "body": "Hola {{nombre}}."}
            )
        assert caught.value.code == "no_editable"
        copy = service.duplicate(approved)
        assert copy.status == "draft" and copy.name != approved.name

    def test_a_rejected_template_edited_is_a_draft_again(self, db: Session) -> None:
        tenant, _ = business(db)
        rejected = template(db, tenant, status="rejected")
        rejected.rejection_reason = "INVALID_FORMAT"
        TemplateService(db, tenant.id).update(
            rejected,
            {"label": "Promo", "category": "marketing", "body": "Hola {{nombre}}, hay promo."},
        )
        assert rejected.status == "draft" and rejected.rejection_reason is None

    def test_a_template_in_use_is_not_deleted(self, db: Session) -> None:
        tenant, _ = business(db)
        used = template(db, tenant)
        db.add(Campaign(tenant_id=tenant.id, name="Julio", template_id=used.id, segment={}))
        db.flush()
        with pytest.raises(CampaignError) as caught:
            TemplateService(db, tenant.id).delete(used)
        assert caught.value.code == "en_uso"


class TestLibrary:
    def test_the_industry_adds_its_own(self) -> None:
        keys = {entry.key for entry in library_for("restaurante")}
        assert "menu_semana" in keys and "aviso_promocion" in keys
        assert "nuevo_programa" not in keys

    def test_every_library_template_is_valid(self, db: Session) -> None:
        tenant, _ = business(db)
        service = TemplateService(db, tenant.id)
        from app.services.campaigns.library import LIBRARY

        for entry in LIBRARY:
            created = service.clone(entry.key)
            assert created.library_key == entry.key and created.status == "draft"
