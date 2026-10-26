"""The versioned catalogue of templates and rubros (017, TASK-460)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.process.layers import LayerResolver, stage_texts_from
from app.process.rule_split import join_rules, rule_keys, split_rules
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.repositories.catalog import CatalogRepository, VersionFrozen

RUBROS = (
    "automotriz",
    "b2b",
    "belleza",
    "hogar",
    "inmobiliaria",
    "instituto",
    "otros",
    "restaurante",
    "retail",
    "salud",
    "servicios",
    "tecnologia",
    "turismo",
)


class TestTheSplit:
    @pytest.mark.parametrize("key", sorted(TEMPLATES))
    def test_every_shipped_stage_text_round_trips(self, key: str) -> None:
        for stage in Stage:
            text_ = TEMPLATES[key].stage(stage).instrucciones
            rules = split_rules(text_)
            assert join_rules(rules) == text_
            assert all(rule.strip() for rule in rules)

    def test_a_header_keeps_its_bullets_and_a_paragraph_its_lines(self) -> None:
        rules = split_rules("Uno.\nSi A:\n- hace a\n  sigue a\nDos largo que\nsigue aquí.\n- tres")

        assert rules == [
            "Uno.",
            "Si A:\n- hace a\n  sigue a",
            "Dos largo que\nsigue aquí.",
            "- tres",
        ]

    def test_keys_are_readable_and_unique(self) -> None:
        assert rule_keys("COBRO", ["- Pide el pago.", "- Pide el pago."]) == [
            "COBRO.pide_pago",
            "COBRO.pide_pago_2",
        ]


LATEST_PROCESS_VERSION = 4


class TestVersionOne:
    @pytest.mark.parametrize("key", sorted(TEMPLATES))
    def test_the_prompt_text_from_the_database_is_today_s_text(self, db: Session, key: str) -> None:
        from uuid import uuid4

        rules = LayerResolver(db, uuid4()).resolve(key, None)
        code = {s.value: TEMPLATES[key].stage(s).instrucciones for s in Stage}
        texts = stage_texts_from(rules, code, list(code))

        # The newest published version (v4 since migration 0067) is today's code text.
        assert rules.process_version == LATEST_PROCESS_VERSION
        assert texts == code

    def test_thirteen_rubros_with_content_and_a_suggested_template(self, db: Session) -> None:
        catalog = CatalogRepository(db)

        assert [r.key for r in catalog.rubros()] == list(RUBROS)
        for key in RUBROS:
            rubro = catalog.rubro(key)
            version = catalog.published("rubro", key)
            assert rubro is not None and rubro.plantilla_sugerida in TEMPLATES
            assert version is not None
            assert catalog.rules(version.id)
            assert len(catalog.faqs(version.id)) == 5
            assert len(catalog.objections(version.id)) == 5
            assert 3 <= len(catalog.suggested_situations(version.id)) <= 5
        instituto = catalog.rubro("instituto")
        assert instituto is not None and instituto.plantilla_sugerida == "certificaciones"

    def test_a_tenant_reads_the_published_rules(self, db: Session) -> None:
        # A tenant without configuration runs `productos` from the catalogue.
        from uuid import uuid4

        config = ProcessConfigService(db, cache=ProcessConfigCache())._load(uuid4())

        assert config.process_version == LATEST_PROCESS_VERSION


class TestVersionFour:
    """Migration 0067: the quote flow, as the fourth published version of each template."""

    @pytest.mark.parametrize("key", sorted(TEMPLATES))
    def test_it_keeps_every_rule_key_of_version_three(self, db: Session, key: str) -> None:
        catalog = CatalogRepository(db)
        v3, v4 = catalog.version("proceso", key, 3), catalog.version("proceso", key, 4)

        assert v3 is not None and v4 is not None
        assert v3.estado == v4.estado == "publicada"
        # A business that switched a rule off keeps it off: the keys are the same.
        assert [r.rule_key for r in catalog.rules(v4.id)] == [
            r.rule_key for r in catalog.rules(v3.id)
        ]

    def test_only_the_servicios_identification_text_changed(self, db: Session) -> None:
        catalog = CatalogRepository(db)
        changed: list[tuple[str, str]] = []
        for key in sorted(TEMPLATES):
            v3, v4 = catalog.version("proceso", key, 3), catalog.version("proceso", key, 4)
            assert v3 is not None and v4 is not None
            before = {r.rule_key: r.texto for r in catalog.rules(v3.id)}
            changed += [
                (key, r.rule_key) for r in catalog.rules(v4.id) if before[r.rule_key] != r.texto
            ]

        assert changed
        assert {key for key, _ in changed} == {"servicios"}
        assert all(rule_key.startswith("IDENTIFICAR.") for _, rule_key in changed)

    def test_version_three_is_left_as_it_was(self, db: Session) -> None:
        catalog = CatalogRepository(db)
        v3 = catalog.version("proceso", "servicios", 3)

        assert v3 is not None
        texts = "\n".join(r.texto for r in catalog.rules(v3.id))
        assert "pídele su nombre y correo" in texts or "nombre y correo" in texts
        assert "COTIZACIÓN" not in texts


class TestPublishedIsFrozen:
    def _rule_id(self, db: Session) -> object:
        catalog = CatalogRepository(db)
        version = catalog.published("proceso", "productos")
        assert version is not None
        return catalog.rules(version.id)[0].id

    def test_the_database_refuses_editing_a_published_rule(self, db: Session) -> None:
        rule_id = self._rule_id(db)
        with pytest.raises(DBAPIError, match="make a new version"), db.begin_nested():
            db.execute(text("UPDATE catalog_rules SET texto = 'x' WHERE id = :id"), {"id": rule_id})

    def test_the_database_refuses_deleting_or_adding_to_a_published_version(
        self, db: Session
    ) -> None:
        rule_id = self._rule_id(db)
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(text("DELETE FROM catalog_rules WHERE id = :id"), {"id": rule_id})
        version = CatalogRepository(db).published("proceso", "productos")
        assert version is not None
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO catalog_rules (version_id, orden, rule_key, texto) "
                    "VALUES (:v, 99, 'X.nueva', 'nueva')"
                ),
                {"v": version.id},
            )

    def test_a_published_version_cannot_go_back_to_draft(self, db: Session) -> None:
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(
                text("UPDATE catalog_versions SET estado = 'borrador' WHERE estado = 'publicada'")
            )

    def test_a_change_is_a_new_draft_with_the_same_content(self, db: Session) -> None:
        catalog = CatalogRepository(db)
        published = catalog.published("rubro", "retail")
        assert published is not None

        draft = catalog.new_draft("rubro", "retail", notas="prueba")
        draft_rules = catalog.rules(draft.id)
        draft_rules[0].texto = "Texto nuevo"
        db.flush()

        assert draft.version == 2 and draft.estado == "borrador"
        assert [r.rule_key for r in draft_rules] == [
            r.rule_key for r in catalog.rules(published.id)
        ]
        assert len(catalog.faqs(draft.id)) == 5
        # Readers keep the published one until the draft is published.
        assert catalog.published("rubro", "retail") is published
        catalog.publish(draft, by=None)
        assert catalog.published("rubro", "retail") is draft
        with pytest.raises(VersionFrozen):
            catalog.publish(draft, by=None)
