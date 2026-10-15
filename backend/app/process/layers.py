"""The layers of a business's configuration, resolved (Feature 017, TASK-461).

Four layers, from the least to the most specific (C-03):

    plantilla de proceso → rubro → perfil del negocio → situaciones

This module resolves the first two, the ones that come from the catalogue: the
published version of the business's process template and of its rubro, minus the
rules the business switched off. Rules a new version adds arrive **on**, unless
the business had switched that `rule_key` off before (C-04); its reviewer sees
them as news until marking the version seen.

The profile is read by `ProcessConfigService`, and the situations by the selector
(TASK-463). Nothing here is cached: `ProcessConfigService` caches the result per
tenant and every write here invalidates it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from sqlalchemy.orm import Session

from app.models.catalog import CatalogVersion
from app.process.rule_split import join_rules
from app.repositories.catalog import CatalogRepository
from app.repositories.layers import TenantCatalogSeenRepository, TenantRuleSwitchRepository


class UnknownRule(LookupError):
    """The rule key is not in the published version the business runs."""


@dataclass(frozen=True)
class LayerRule:
    """A rule as the panel shows it: its text and whether the business switched it off."""

    rule_key: str
    texto: str
    etapa: str | None
    apagada: bool


@dataclass(frozen=True)
class EffectiveRules:
    """The catalogue layers as one turn runs them."""

    process_version: int | None = None
    # Active process rules by stage. A stage present with an empty tuple means
    # every rule of it was switched off; an absent stage means the version has
    # none (the caller then falls back to the code text).
    process_by_stage: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    rubro_key: str | None = None
    rubro_version: int | None = None
    rubro_version_id: uuid.UUID | None = None
    rubro_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class News:
    """Rules a published version added since the version the reviewer saw."""

    kind: str
    key: str
    version: int
    seen: int
    rules: tuple[LayerRule, ...]


class LayerResolver:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._catalog = CatalogRepository(session)
        self._switches = TenantRuleSwitchRepository(session, tenant_id)

    def resolve(self, template_key: str, rubro_key: str | None) -> EffectiveRules:
        process = self._catalog.published("proceso", template_key)
        off = self._switches.off_by_kind()
        off_process = off.get("proceso", frozenset())
        by_stage: dict[str, list[str]] = {}
        if process is not None:
            for rule in self._catalog.rules(process.id):
                if rule.etapa is None:
                    continue
                stage_rules = by_stage.setdefault(rule.etapa, [])
                if rule.rule_key not in off_process:
                    stage_rules.append(rule.texto)

        rubro = self._catalog.published("rubro", rubro_key) if rubro_key else None
        rubro_rules: tuple[str, ...] = ()
        if rubro is not None:
            off_rubro = off.get("rubro", frozenset())
            rubro_rules = tuple(
                rule.texto
                for rule in self._catalog.rules(rubro.id)
                if rule.rule_key not in off_rubro
            )
        return EffectiveRules(
            process_version=process.version if process is not None else None,
            process_by_stage=MappingProxyType({k: tuple(v) for k, v in by_stage.items()}),
            rubro_key=rubro.key if rubro is not None else None,
            rubro_version=rubro.version if rubro is not None else None,
            rubro_version_id=rubro.id if rubro is not None else None,
            rubro_rules=rubro_rules,
        )

    # ------------------------------------------------------------- the panel

    def _published(self, kind: str, key: str | None) -> CatalogVersion | None:
        return self._catalog.published(kind, key) if key else None

    def rules(self, kind: str, key: str | None) -> list[LayerRule]:
        """Every rule of the published version, with the business's switch."""
        version = self._published(kind, key)
        if version is None:
            return []
        off = self._switches.off_keys(kind)
        return [
            LayerRule(rule.rule_key, rule.texto, rule.etapa, rule.rule_key in off)
            for rule in self._catalog.rules(version.id)
        ]

    def switch(
        self, kind: str, key: str | None, rule_key: str, *, apagada: bool, by: uuid.UUID | None
    ) -> LayerRule:
        """Switch a rule of the published version on or off. Raises `UnknownRule`."""
        version = self._published(kind, key)
        rules = {r.rule_key: r for r in self._catalog.rules(version.id)} if version else {}
        rule = rules.get(rule_key)
        if rule is None:
            raise UnknownRule(rule_key)
        self._switches.set(kind, rule_key, apagada=apagada, by=by)
        # The next turn must see it; other workers within the cache's 60 s.
        from app.process.service import DEFAULT_CACHE

        DEFAULT_CACHE.invalidate(self._tenant_id)
        return LayerRule(rule.rule_key, rule.texto, rule.etapa, apagada)

    def news(self, kind: str, key: str | None) -> News | None:
        """What the published version added since the one the reviewer saw.

        A business that never saw any version starts at the current one: the
        rules it found when it chose the template or rubro are not news.
        """
        version = self._published(kind, key)
        if version is None or key is None:
            return None
        seen_repo = TenantCatalogSeenRepository(self._session, self._tenant_id)
        seen = seen_repo.seen(kind, key)
        if seen is None:
            seen_repo.mark(kind, key, version.version)
            return None
        if seen >= version.version:
            return None
        before = self._catalog.version(kind, key, seen)
        known = {r.rule_key for r in self._catalog.rules(before.id)} if before else set()
        off = self._switches.off_keys(kind)
        added = tuple(
            LayerRule(r.rule_key, r.texto, r.etapa, r.rule_key in off)
            for r in self._catalog.rules(version.id)
            if r.rule_key not in known
        )
        return News(kind, key, version.version, seen, added)

    def mark_seen(self, kind: str, key: str | None) -> None:
        version = self._published(kind, key)
        if version is not None and key is not None:
            TenantCatalogSeenRepository(self._session, self._tenant_id).mark(
                kind, key, version.version
            )


def stage_texts_from(
    rules: EffectiveRules, fallback: Mapping[str, str], stages: Sequence[str]
) -> dict[str, str]:
    """Each stage's text: its active rules joined, or the code text if the version has none."""
    return {
        stage: join_rules(rules.process_by_stage[stage])
        if stage in rules.process_by_stage
        else fallback[stage]
        for stage in stages
    }
