"""The persistent conversation state (Feature 004, TASK-412).

Two things can write a conversation's state at nearly the same time: the turn
that is answering and an operator confirming a payment. These tests pin the
optimistic-concurrency contract (a stale version is retried once, never
silently lost), the rule that only a *change* leaves an event, and the tenant
boundary: a business cannot create, read or move another's conversation state.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.conversation_state import (
    ConversationStageEvent,
    ConversationState,
    FunnelCode,
)
from app.models.tenant import Tenant
from app.process.facts import Derivado, Facts
from app.process.state import (
    INITIAL_FUNNEL,
    INITIAL_STAGE,
    StateConflict,
    StateRepo,
    facts_snapshot,
)
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.repositories.base import TenantScopeError
from app.repositories.conversation_state import (
    ConversationStageEventRepository,
    ConversationStateRepository,
)
from tests.isolation.conftest import TenantFixture

TEMPLATE = TEMPLATES["productos"]


def _conversation(db: Session, fixture: TenantFixture) -> Conversation:
    conversation = db.get(Conversation, fixture.conversation_id)
    assert conversation is not None
    return conversation


def _save(
    repo: StateRepo,
    fixture: TenantFixture,
    *,
    stage: Stage = Stage.IDENTIFICAR,
    funnel: FunnelCode = FunnelCode.ATENDIDO,
    patch: dict[str, Any] | None = None,
    version: int,
    reason: str = "turno",
    **extra: Any,
) -> Any:
    return repo.save(
        fixture.conversation_id,
        stage=stage,
        funnel=funnel,
        facts_patch=patch,
        reason=reason,
        expected_version=version,
        **extra,
    )


def _events(db: Session, fixture: TenantFixture) -> list[Any]:
    return list(
        ConversationStageEventRepository(db, fixture.tenant_id).for_conversation(
            fixture.conversation_id
        )
    )


class TestGetOrCreate:
    def test_the_first_turn_creates_the_state_at_inicio(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = StateRepo(db, tenant_a.tenant_id)

        state = repo.get_or_create(_conversation(db, tenant_a), TEMPLATE)
        db.refresh(state)

        assert (state.stage_key, state.funnel_code) == (INITIAL_STAGE, INITIAL_FUNNEL)
        assert (state.stage_key, state.funnel_code) == ("INICIO", "NUEVO")
        assert (state.template_key, state.template_version) == ("productos", 1)
        assert state.version == 1
        assert state.facts == {}
        assert state.tenant_id == tenant_a.tenant_id

    def test_a_sandbox_conversation_is_marked_as_such(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        state = StateRepo(db, tenant_a.tenant_id).get_or_create(
            _conversation(db, tenant_a), TEMPLATE
        )

        assert state.is_sandbox is True

    def test_a_real_conversation_is_not(self, db: Session, tenant_a: TenantFixture) -> None:
        conversation = Conversation(
            tenant_id=tenant_a.tenant_id,
            channel="whatsapp",
            contact_id=tenant_a.contact_id,
            status="open",
        )
        db.add(conversation)
        db.flush()

        state = StateRepo(db, tenant_a.tenant_id).get_or_create(conversation, TEMPLATE)

        assert state.is_sandbox is False

    def test_it_is_idempotent_and_writes_no_event(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = StateRepo(db, tenant_a.tenant_id)
        conversation = _conversation(db, tenant_a)

        first = repo.get_or_create(conversation, TEMPLATE)
        second = repo.get_or_create(conversation, TEMPLATE)

        assert first.conversation_id == second.conversation_id
        assert ConversationStateRepository(db, tenant_a.tenant_id).count() == 1
        assert _events(db, tenant_a) == []

    def test_a_conversation_that_already_has_state_keeps_it(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = StateRepo(db, tenant_a.tenant_id)
        conversation = _conversation(db, tenant_a)
        repo.get_or_create(conversation, TEMPLATE)
        _save(repo, tenant_a, patch={"saludo_enviado": True}, version=1)

        again = repo.get_or_create(conversation, TEMPLATE)

        assert again.facts == {"saludo_enviado": True}
        assert again.stage_key == "IDENTIFICAR"

    def test_a_foreign_conversation_is_refused_and_nothing_is_created(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        foreign = _conversation(db, tenant_b)

        with pytest.raises(TenantScopeError):
            StateRepo(db, tenant_a.tenant_id).get_or_create(foreign, TEMPLATE)

        assert ConversationStateRepository(db, tenant_b.tenant_id).count() == 0
        assert ConversationStateRepository(db, tenant_a.tenant_id).count() == 0


class TestSavingAChangeOfStage:
    def _ready(self, db: Session, fixture: TenantFixture) -> StateRepo:
        repo = StateRepo(db, fixture.tenant_id)
        repo.get_or_create(_conversation(db, fixture), TEMPLATE)
        return repo

    def test_it_moves_the_state_and_records_the_event(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)
        started = repo.get(tenant_a.conversation_id)
        assert started is not None
        entered_before = started.stage_entered_at

        result = _save(
            repo,
            tenant_a,
            patch={"saludo_enviado": True},
            version=1,
            reason="herramienta:buscar_productos",
            snapshot={"pago_estado": None},
        )

        assert result.changed_stage is True
        assert result.retried is False
        state = result.state
        assert (state.stage_key, state.funnel_code, state.version) == ("IDENTIFICAR", "ATENDIDO", 2)
        assert state.facts == {"saludo_enviado": True}
        assert state.stage_entered_at > entered_before
        [event] = _events(db, tenant_a)
        assert (event.from_stage, event.to_stage) == ("INICIO", "IDENTIFICAR")
        assert (event.from_funnel, event.to_funnel) == ("NUEVO", "ATENDIDO")
        assert event.reason == "herramienta:buscar_productos"
        assert event.facts_snapshot == {"pago_estado": None}

    def test_a_change_of_funnel_alone_is_an_event_too(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)
        _save(repo, tenant_a, stage=Stage.COBRO, funnel=FunnelCode.NEGOCIACION, version=1)

        _save(repo, tenant_a, stage=Stage.COBRO, funnel=FunnelCode.CUENTAS_ENVIADAS, version=2)

        events = _events(db, tenant_a)
        assert [(e.to_stage, e.to_funnel) for e in events] == [
            ("COBRO", "NEGOCIACION"),
            ("COBRO", "CUENTAS_ENVIADAS"),
        ]
        assert events[1].from_funnel == "NEGOCIACION"

    def test_an_event_without_a_snapshot_stores_none(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)

        _save(repo, tenant_a, version=1)

        assert _events(db, tenant_a)[0].facts_snapshot is None


class TestNotDuplicatingEvents:
    def _ready(self, db: Session, fixture: TenantFixture) -> StateRepo:
        repo = StateRepo(db, fixture.tenant_id)
        repo.get_or_create(_conversation(db, fixture), TEMPLATE)
        return repo

    def test_saving_the_same_stage_writes_no_new_event(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)
        _save(repo, tenant_a, version=1)

        again = _save(repo, tenant_a, patch={"vio_productos": True}, version=2)

        assert again.changed_stage is False
        assert again.event is None
        assert len(_events(db, tenant_a)) == 1
        assert again.state.version == 3

    def test_staying_at_inicio_writes_no_event(self, db: Session, tenant_a: TenantFixture) -> None:
        """The first turn may not leave INICIO (the customer only greeted)."""
        repo = self._ready(db, tenant_a)

        _save(repo, tenant_a, stage=Stage.INICIO, funnel=FunnelCode.NUEVO, version=1)

        assert _events(db, tenant_a) == []

    def test_the_time_in_the_stage_only_moves_when_the_stage_does(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)
        moved = _save(repo, tenant_a, version=1).state
        entered = moved.stage_entered_at

        stayed = _save(repo, tenant_a, patch={"vio_productos": True}, version=2).state

        assert stayed.stage_entered_at == entered

    def test_a_forced_event_is_written_even_when_the_stage_did_not_move(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """Rejecting a receipt leaves VERIFICACION where it is, and the decision is history."""
        repo = self._ready(db, tenant_a)
        moved = _save(repo, tenant_a, version=1, reason="comprobante").state
        entered = moved.stage_entered_at

        forced = _save(repo, tenant_a, version=2, reason="operador:rechazar", force_event=True)

        assert forced.event is not None
        assert [event.reason for event in _events(db, tenant_a)] == [
            "comprobante",
            "operador:rechazar",
        ]
        assert forced.state.stage_entered_at == entered

    def test_a_stale_save_to_the_stage_someone_else_reached_adds_no_event(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """The turn and the operator both land on VERIFICACION: one event, not two."""
        repo = self._ready(db, tenant_a)
        verification: dict[str, Any] = {
            "stage": Stage.VERIFICACION,
            "funnel": FunnelCode.EN_VERIFICACION,
        }
        _save(repo, tenant_a, version=1, reason="comprobante", **verification)

        late = _save(repo, tenant_a, version=1, reason="operador:confirmar", **verification)

        assert late.retried is True
        assert late.changed_stage is False
        assert [e.reason for e in _events(db, tenant_a)] == ["comprobante"]


class TestPatchingTheFacts:
    def _ready(self, db: Session, fixture: TenantFixture) -> StateRepo:
        repo = StateRepo(db, fixture.tenant_id)
        repo.get_or_create(_conversation(db, fixture), TEMPLATE)
        return repo

    def test_a_patch_is_merged_over_what_is_there(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)
        _save(repo, tenant_a, patch={"saludo_enviado": True, "camino": "programa"}, version=1)

        state = _save(repo, tenant_a, patch={"vio_productos": True}, version=2).state

        assert state.facts == {
            "saludo_enviado": True,
            "camino": "programa",
            "vio_productos": True,
        }

    def test_a_key_set_to_none_is_removed(self, db: Session, tenant_a: TenantFixture) -> None:
        """Clearing a handover is how the bot is resumed."""
        repo = self._ready(db, tenant_a)
        derivado = {"motivo": "pide_humano", "hasta": "2026-10-10T21:00:00+00:00"}
        _save(repo, tenant_a, patch={"derivado": derivado, "camino": "x"}, version=1)

        state = _save(repo, tenant_a, patch={"derivado": None}, version=2).state

        assert state.facts == {"camino": "x"}

    def test_a_patch_replaces_a_whole_key(self, db: Session, tenant_a: TenantFixture) -> None:
        repo = self._ready(db, tenant_a)
        _save(repo, tenant_a, patch={"recursos_enviados": ["saludo"]}, version=1)

        state = _save(
            repo, tenant_a, patch={"recursos_enviados": ["saludo", "flyer"]}, version=2
        ).state

        assert state.facts["recursos_enviados"] == ["saludo", "flyer"]

    def test_no_patch_keeps_the_facts(self, db: Session, tenant_a: TenantFixture) -> None:
        repo = self._ready(db, tenant_a)
        _save(repo, tenant_a, patch={"camino": "x"}, version=1)

        state = _save(repo, tenant_a, patch=None, version=2).state

        assert state.facts == {"camino": "x"}

    @pytest.mark.parametrize("key", ["pago_estado", "pedido", "datos_faltantes", "inventado"])
    def test_a_derived_or_unknown_fact_is_refused_and_nothing_is_written(
        self, db: Session, tenant_a: TenantFixture, key: str
    ) -> None:
        repo = self._ready(db, tenant_a)

        with pytest.raises(ValueError, match="not stored facts"):
            _save(repo, tenant_a, patch={key: "x"}, version=1)

        state = repo.get(tenant_a.conversation_id)
        assert state is not None
        assert (state.version, state.facts, state.stage_key) == (1, {}, "INICIO")
        assert _events(db, tenant_a) == []

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_a_change_needs_a_reason(
        self, db: Session, tenant_a: TenantFixture, reason: str
    ) -> None:
        repo = self._ready(db, tenant_a)

        with pytest.raises(ValueError, match="reason"):
            _save(repo, tenant_a, version=1, reason=reason)

    def test_the_payment_methods_record_is_a_stored_fact(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)
        record = {"order_id": str(uuid.uuid4()), "en": datetime.now(UTC).isoformat()}

        state = _save(repo, tenant_a, patch={"metodos_pago_enviados": record}, version=1).state

        assert state.facts["metodos_pago_enviados"] == record

    def test_what_is_stored_reads_back_as_facts(self, db: Session, tenant_a: TenantFixture) -> None:
        repo = self._ready(db, tenant_a)
        hasta = datetime.now(UTC) + timedelta(hours=6)
        derivado = Derivado(motivo="asesoria", resumen="x", hasta=hasta).model_dump(mode="json")

        state = _save(
            repo, tenant_a, patch={"derivado": derivado, "saludo_enviado": True}, version=1
        ).state
        facts = Facts.from_stored(state.facts)

        assert facts.saludo_enviado is True
        assert facts.derivado is not None and facts.derivado.hasta == hasta


class TestConcurrentSaves:
    """Two writers that read the same version: the second must not be lost."""

    def _ready(self, db: Session, fixture: TenantFixture) -> StateRepo:
        repo = StateRepo(db, fixture.tenant_id)
        repo.get_or_create(_conversation(db, fixture), TEMPLATE)
        return repo

    def test_two_saves_with_the_same_version_both_land(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)
        turn = _save(repo, tenant_a, patch={"vio_productos": True}, version=1)
        operator = _save(
            repo,
            tenant_a,
            stage=Stage.CERRADO,
            funnel=FunnelCode.GANADO,
            patch={"seguimiento": {"intentos": 1}},
            version=1,  # read before the turn finished
            reason="operador:confirmar",
        )

        assert turn.retried is False
        assert operator.retried is True
        state = operator.state
        assert state.version == 3
        assert state.facts == {"vio_productos": True, "seguimiento": {"intentos": 1}}
        assert (state.stage_key, state.funnel_code) == ("CERRADO", "GANADO")

    def test_the_retry_compares_against_the_fresh_row_for_the_event(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)
        _save(repo, tenant_a, version=1)  # INICIO -> IDENTIFICAR
        operator = _save(
            repo,
            tenant_a,
            stage=Stage.CERRADO,
            funnel=FunnelCode.GANADO,
            version=1,
            reason="operador:confirmar",
        )

        [first, second] = _events(db, tenant_a)
        assert (first.from_stage, first.to_stage) == ("INICIO", "IDENTIFICAR")
        # From where the row really was, not from where the stale writer saw it.
        assert (second.from_stage, second.to_stage) == ("IDENTIFICAR", "CERRADO")
        assert operator.event is not None

    def test_a_version_ahead_of_the_row_is_also_a_conflict_and_is_rebased(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = self._ready(db, tenant_a)

        result = _save(repo, tenant_a, version=99)

        assert result.retried is True
        assert result.state.version == 2

    def test_a_second_collision_is_raised_not_swallowed(
        self, db: Session, tenant_a: TenantFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = self._ready(db, tenant_a)
        attempts: list[int] = []

        def always_collides(*args: Any, **kwargs: Any) -> None:
            attempts.append(1)

        monkeypatch.setattr(StateRepo, "_try_save", always_collides)

        with pytest.raises(StateConflict):
            _save(repo, tenant_a, version=1)

        assert len(attempts) == 2  # tried, retried once, gave up

    def test_nothing_is_written_when_the_save_gives_up(
        self, db: Session, tenant_a: TenantFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = self._ready(db, tenant_a)
        monkeypatch.setattr(StateRepo, "_try_save", lambda *a, **k: None)

        with pytest.raises(StateConflict):
            _save(repo, tenant_a, patch={"camino": "x"}, version=1)

        state = repo.get(tenant_a.conversation_id)
        assert state is not None
        assert (state.version, state.facts) == (1, {})
        assert _events(db, tenant_a) == []


class TestMissingState:
    def test_saving_without_state_is_an_error(self, db: Session, tenant_a: TenantFixture) -> None:
        with pytest.raises(LookupError):
            _save(StateRepo(db, tenant_a.tenant_id), tenant_a, version=1)


class TestTheTenantBoundary:
    def test_a_foreign_state_cannot_be_read(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        StateRepo(db, tenant_b.tenant_id).get_or_create(_conversation(db, tenant_b), TEMPLATE)

        assert StateRepo(db, tenant_a.tenant_id).get(tenant_b.conversation_id) is None

    def test_a_foreign_state_cannot_be_moved(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        theirs = StateRepo(db, tenant_b.tenant_id)
        theirs.get_or_create(_conversation(db, tenant_b), TEMPLATE)

        with pytest.raises(LookupError):
            StateRepo(db, tenant_a.tenant_id).save(
                tenant_b.conversation_id,
                stage=Stage.CERRADO,
                funnel=FunnelCode.GANADO,
                facts_patch={"camino": "robado"},
                reason="turno",
                expected_version=1,
            )

        untouched = theirs.get(tenant_b.conversation_id)
        assert untouched is not None
        assert (untouched.stage_key, untouched.version, untouched.facts) == ("INICIO", 1, {})
        assert _events(db, tenant_b) == []

    def test_each_business_keeps_its_own_history(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        for fixture in (tenant_a, tenant_b):
            repo = StateRepo(db, fixture.tenant_id)
            repo.get_or_create(_conversation(db, fixture), TEMPLATE)
            _save(repo, fixture, version=1, reason=f"turno-{fixture.tenant_id}")

        mine = _events(db, tenant_a)
        assert [e.reason for e in mine] == [f"turno-{tenant_a.tenant_id}"]
        assert {e.tenant_id for e in mine} == {tenant_a.tenant_id}


class TestTheEventSnapshot:
    def test_it_keeps_the_derived_facts_and_drops_personal_data(self) -> None:
        facts = Facts(
            saludo_enviado=True,
            nombre_cliente="Ana Quispe",
            datos_faltantes=["dni", "correo"],
            pago_estado="por_verificar",
            derivado=Derivado(
                motivo="pide_humano",
                resumen="Ana Quispe, DNI 12345678, quiere factura",
                hasta=datetime(2026, 10, 10, 21, 0, tzinfo=UTC),
            ),
        )

        snapshot = facts_snapshot(facts)

        assert "nombre_cliente" not in snapshot
        assert "resumen" not in snapshot["derivado"]
        assert snapshot["derivado"]["motivo"] == "pide_humano"
        assert snapshot["pago_estado"] == "por_verificar"
        assert snapshot["datos_faltantes"] == ["dni", "correo"]  # field names, not values
        assert "Ana" not in str(snapshot)
        assert "12345678" not in str(snapshot)

    def test_it_is_plain_json(self) -> None:
        snapshot = facts_snapshot(Facts(saludo_enviado=True))

        assert snapshot["saludo_enviado"] is True
        assert snapshot["derivado"] is None


class TestTwoRealConnections:
    """The same contract, with two database connections instead of two calls.

    The other tests simulate a stale writer by passing an old version. This one
    lets the database do the arbitrating: writer A holds the row lock with an
    uncommitted save while writer B, who read the same version, tries to save.
    B's UPDATE waits, finds the version moved when A commits, and must retry on
    top of A's work instead of overwriting it.

    It commits real rows (two connections cannot share the suite's rolled-back
    transaction), so it removes them itself, whatever happens.
    """

    def test_the_writer_that_loses_the_race_rebases_on_the_winner(self, engine: Engine) -> None:
        setup = Session(engine)
        tenant = Tenant(name=f"Concurrency {uuid.uuid4().hex[:6]}")
        setup.add(tenant)
        setup.flush()
        conversation = Conversation(tenant_id=tenant.id, channel="sandbox")
        setup.add(conversation)
        setup.flush()
        StateRepo(setup, tenant.id).get_or_create(conversation, TEMPLATE)
        setup.commit()
        tenant_id, conversation_id = tenant.id, conversation.id
        setup.close()

        a_saved = threading.Event()
        b_read = threading.Event()
        outcome: dict[str, Any] = {}

        def writer_a() -> None:
            with Session(engine) as session:
                repo = StateRepo(session, tenant_id)
                repo.get(conversation_id)
                b_read.wait(10)
                result = repo.save(
                    conversation_id,
                    stage=Stage.IDENTIFICAR,
                    funnel=FunnelCode.ATENDIDO,
                    facts_patch={"vio_productos": True},
                    reason="turno",
                    expected_version=1,
                )
                a_saved.set()
                # Keep the row locked while B tries its save.
                time.sleep(0.5)
                session.commit()
                outcome["a"] = (result.retried, result.state.version)

        def writer_b() -> None:
            with Session(engine) as session:
                repo = StateRepo(session, tenant_id)
                repo.get(conversation_id)
                b_read.set()
                a_saved.wait(10)
                result = repo.save(
                    conversation_id,
                    stage=Stage.CERRADO,
                    funnel=FunnelCode.GANADO,
                    facts_patch={"camino": "x"},
                    reason="operador:confirmar",
                    expected_version=1,
                )
                session.commit()
                outcome["b"] = (result.retried, result.state.version)

        try:
            threads = [threading.Thread(target=writer_a), threading.Thread(target=writer_b)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)

            assert outcome["a"] == (False, 2)
            assert outcome["b"] == (True, 3)
            with Session(engine) as check:
                state = check.get(ConversationState, conversation_id)
                assert state is not None
                assert state.facts == {"vio_productos": True, "camino": "x"}
                assert (state.stage_key, state.version) == ("CERRADO", 3)
                events = ConversationStageEventRepository(check, tenant_id).for_conversation(
                    conversation_id
                )
                assert [(e.from_stage, e.to_stage) for e in events] == [
                    ("INICIO", "IDENTIFICAR"),
                    ("IDENTIFICAR", "CERRADO"),
                ]
        finally:
            with Session(engine) as cleanup:
                for model in (ConversationStageEvent, ConversationState):
                    cleanup.execute(delete(model).where(model.conversation_id == conversation_id))
                cleanup.execute(delete(Conversation).where(Conversation.id == conversation_id))
                cleanup.execute(delete(Tenant).where(Tenant.id == tenant_id))
                cleanup.commit()
