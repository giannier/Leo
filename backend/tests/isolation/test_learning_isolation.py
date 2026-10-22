"""One business never reaches another's suggestions, history, questions or ceiling (017)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.repositories.base import TenantScopeError
from app.repositories.learning import (
    ChatImportRepository,
    ConfigHistoryRepository,
    ExampleRepository,
    LearningSettingsRepository,
    LearningUsageRepository,
    SuggestionRepository,
    UnansweredQuestionRepository,
)
from tests.isolation.conftest import TenantFixture

WEEK = date(2026, 10, 19)


def _suggestion(db: Session, fixture: TenantFixture, label: str) -> uuid.UUID:
    return (
        SuggestionRepository(db, fixture.tenant_id)
        .add(
            tipo="dato",
            fuente="pregunta_sin_respuesta",
            resumen=f"de {label}",
            propuesta={"pregunta": f"¿{label}?"},
            huella="same-fingerprint",
        )
        .id
    )


class TestSuggestions:
    def test_each_business_sees_only_its_own_and_may_share_a_fingerprint(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine, theirs = _suggestion(db, tenant_a, "a"), _suggestion(db, tenant_b, "b")

        repo = SuggestionRepository(db, tenant_a.tenant_id)

        assert [s.resumen for s in repo.listing(estado=None)] == ["de a"]
        assert repo.get(theirs) is None and repo.get_for_update(theirs) is None
        assert repo.many([mine, theirs])[0].id == mine and len(repo.many([mine, theirs])) == 1
        assert repo.by_huella("same-fingerprint") is not None
        assert repo.pending_by_type() == {"dato": 1}

    def test_a_row_cannot_be_written_for_another_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            SuggestionRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id,
                tipo="dato",
                fuente="importacion",
                resumen="x",
                huella="h",
            )


class TestHistoryAndQuestions:
    def test_the_history_is_per_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        for fixture, label in ((tenant_a, "a"), (tenant_b, "b")):
            ConfigHistoryRepository(db, fixture.tenant_id).add(
                entidad="situacion", accion="crear", resumen=f"de {label}"
            )

        assert [h.resumen for h in ConfigHistoryRepository(db, tenant_a.tenant_id).recent()] == [
            "de a"
        ]

    def test_a_question_is_counted_per_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = UnansweredQuestionRepository(db, tenant_a.tenant_id)
        theirs = UnansweredQuestionRepository(db, tenant_b.tenant_id)

        mine.record("h", "¿Envíos?", None)
        mine.record("h", "¿Envíos?", None)
        theirs.record("h", "¿Envíos?", None)

        assert [q.veces for q in mine.due(1)] == [2] and [q.veces for q in theirs.due(1)] == [1]


class TestTheCeiling:
    def test_the_settings_and_the_spending_are_per_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        LearningSettingsRepository(db, tenant_a.tenant_id).ensure().activo = True
        db.flush()
        mine, theirs = (
            LearningUsageRepository(db, tenant_a.tenant_id),
            LearningUsageRepository(db, tenant_b.tenant_id),
        )

        assert mine.spend_calls(WEEK, 2, ceiling=2) is True
        assert mine.spend_calls(WEEK, 1, ceiling=2) is False
        assert theirs.spend_calls(WEEK, 1, ceiling=2) is True
        assert LearningSettingsRepository(db, tenant_b.tenant_id).ensure().activo is False
        assert (mine.week(WEEK) or object()).llamadas == 2  # type: ignore[attr-defined]


class TestImportsAndExamples:
    def test_each_business_sees_only_its_own_chats_and_examples(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        for fixture, label in ((tenant_a, "a"), (tenant_b, "b")):
            ChatImportRepository(db, fixture.tenant_id).add()
            ExampleRepository(db, fixture.tenant_id).add(
                titulo=f"de {label}", tema="tema", mensajes=[], origen="importacion"
            )

        imports = ChatImportRepository(db, tenant_a.tenant_id)
        examples = ExampleRepository(db, tenant_a.tenant_id)
        theirs = ExampleRepository(db, tenant_b.tenant_id).listing()[0]

        assert len(imports.listing()) == 1 and imports.running() is not None
        assert [e.titulo for e in examples.listing()] == ["de a"]
        assert examples.get(theirs.id) is None

    def test_the_closest_example_is_always_the_businesss_own(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        vector = [1.0] + [0.0] * 1023
        for fixture, label in ((tenant_a, "a"), (tenant_b, "b")):
            row = ExampleRepository(db, fixture.tenant_id).add(
                titulo=f"de {label}", tema="tema", mensajes=[], origen="importacion"
            )
            row.embedding, row.embedding_model = vector, "m"
        db.flush()

        found = ExampleRepository(db, tenant_a.tenant_id).closest(
            vector, model="m", max_distance=1.0, limit=5
        )

        assert [example.titulo for example, _ in found] == ["de a"]
