"""What a business decided about the catalogue's rules (017, TASK-461).

Two small tenant-scoped tables: the template and rubro rules the business
switched off, and the version whose news its reviewer already saw. Both are
keyed by natural keys, not by row id.
"""

from __future__ import annotations

import uuid

from app.models.catalog import TenantCatalogSeen, TenantRuleSwitch
from app.repositories.base import BaseRepository


class TenantRuleSwitchRepository(BaseRepository[TenantRuleSwitch]):
    model = TenantRuleSwitch

    def get(self, entity_id: uuid.UUID) -> TenantRuleSwitch | None:
        raise NotImplementedError("keyed by (kind, rule_key): use off_keys() and set()")

    def _row(self, kind: str, rule_key: str) -> TenantRuleSwitch | None:
        statement = self._scoped().where(
            TenantRuleSwitch.kind == kind, TenantRuleSwitch.rule_key == rule_key
        )
        return self._session.execute(statement).scalar_one_or_none()

    def off_keys(self, kind: str) -> frozenset[str]:
        """The rule keys of `kind` this business switched off."""
        statement = self._scoped().where(
            TenantRuleSwitch.kind == kind, TenantRuleSwitch.apagada.is_(True)
        )
        return frozenset(row.rule_key for row in self._session.execute(statement).scalars())

    def off_by_kind(self) -> dict[str, frozenset[str]]:
        """Every switched-off key, by kind, in one query (read once per turn config)."""
        statement = self._scoped().where(TenantRuleSwitch.apagada.is_(True))
        found: dict[str, set[str]] = {}
        for row in self._session.execute(statement).scalars():
            found.setdefault(row.kind, set()).add(row.rule_key)
        return {kind: frozenset(keys) for kind, keys in found.items()}

    def set(
        self, kind: str, rule_key: str, *, apagada: bool, by: uuid.UUID | None
    ) -> TenantRuleSwitch:
        row = self._row(kind, rule_key)
        if row is None:
            return self.add(kind=kind, rule_key=rule_key, apagada=apagada, cambiado_por=by)
        row.apagada = apagada
        row.cambiado_por = by
        self._session.flush()
        return row


class TenantCatalogSeenRepository(BaseRepository[TenantCatalogSeen]):
    model = TenantCatalogSeen

    def get(self, entity_id: uuid.UUID) -> TenantCatalogSeen | None:
        raise NotImplementedError("keyed by (kind, key): use seen() and mark()")

    def _row(self, kind: str, key: str) -> TenantCatalogSeen | None:
        statement = self._scoped().where(
            TenantCatalogSeen.kind == kind, TenantCatalogSeen.key == key
        )
        return self._session.execute(statement).scalar_one_or_none()

    def seen(self, kind: str, key: str) -> int | None:
        row = self._row(kind, key)
        return row.version_vista if row is not None else None

    def mark(self, kind: str, key: str, version: int) -> None:
        row = self._row(kind, key)
        if row is None:
            self.add(kind=kind, key=key, version_vista=version)
        elif version > row.version_vista:
            row.version_vista = version
            self._session.flush()
