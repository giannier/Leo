"""The template of a rubro, applied by a business that then owns it (feature 020, phase F3).

A rubro template is a starting point (Giannier, 2026-10-28): it helps a business that does not know
how to sell, or sells without order, with the situations, paths, customer data and payment that
business type usually needs. **Applying** it copies what it brings to the business, which then
changes, switches off or deletes it like anything of its own. When Zentek360 publishes a better
version, what is new or improved arrives as **news**, accepted item by item; nothing is changed
behind the business's back.

What a template brings and where it lands:

* its **suggested situations** become the business's situations (`origen = sugerida_rubro`, with
  the item's key and version): active when complete, drafts when they need a value the business
  must give;
* its **paths, customer data and payment** (`catalog_versions.plantilla`) fill the business's
  process options that are still empty, never the ones it set;
* its **rules** stay rules, in the RUBRO block; the business may write them in its own words
  (`LayerResolver.rewrite`) or switch them off (clarifications C-01).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.catalog import CatalogSuggestedSituation, CatalogVersion
from app.models.situation import Situation
from app.process.service import DEFAULT_CACHE
from app.process.templates import TEMPLATES
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.catalog import CatalogRepository
from app.repositories.situation import SituationRepository
from app.services.situations import SituationInput, SituationService

MAX_TITLE = 80
# The options a template may suggest: what the process takes from it when the business set none.
TEMPLATE_OPTIONS = ("calificacion", "datos_requeridos", "modalidad_pago")


class TemplateError(ValueError):
    """A template that cannot be applied as asked. `code` is the API error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TemplateItem:
    """One suggested situation of a template version."""

    clave: str
    titulo: str
    cuando_aplica: str
    que_hace: str
    # {campo: etiqueta}: what the business must fill before it can be active.
    campos: Mapping[str, str] = field(default_factory=dict)
    # The stages it enters at (None: all) and whether it always enters there.
    etapas: tuple[str, ...] | None = None
    siempre: bool = False


@dataclass(frozen=True)
class NewsItem:
    """What a newer version of the template brings for one item."""

    tipo: Literal["nueva", "mejorada"]
    item: TemplateItem
    # The business's situation from this item, when it has one (a «mejorada»).
    situation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class TemplateView:
    rubro: str | None
    nombre: str | None
    version: int | None
    items: tuple[TemplateItem, ...]
    opciones: Mapping[str, Any]
    aplicada: int | None
    novedades: tuple[NewsItem, ...]


@dataclass(frozen=True)
class Applied:
    version: int
    activas: int
    borradores: int
    ya_estaban: int
    opciones: tuple[str, ...]
    # For the history: what was created, and the process options before (None: untouched).
    creadas: tuple[Situation, ...] = ()
    opciones_antes: Mapping[str, Any] | None = None


def key_from_title(titulo: str) -> str:
    """A key from an item's title: «Si piden factura» → `situacion.si_piden_factura`."""
    plain = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode().lower()
    return "situacion." + ("_".join(re.findall(r"[a-z0-9]+", plain)[:8]) or "sin_titulo")


def item_key(row: CatalogSuggestedSituation) -> str:
    """The item's key across versions: its own, or its title for rows from before migration 0072."""
    return row.clave or key_from_title(row.titulo)


def _items(catalog: CatalogRepository, version: CatalogVersion | None) -> tuple[TemplateItem, ...]:
    if version is None:
        return ()
    return tuple(
        TemplateItem(
            clave=item_key(row),
            titulo=row.titulo[:MAX_TITLE],
            cuando_aplica=row.cuando_aplica,
            que_hace=row.que_hace,
            campos=dict(row.campos or {}),
            etapas=tuple(row.etapas) if row.etapas else None,
            siempre=row.siempre,
        )
        for row in catalog.suggested_situations(version.id)
    )


class RubroTemplateService:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._catalog = CatalogRepository(session)
        self._agents = AgentConfigRepository(session, tenant_id)
        self._situations = SituationService(session, tenant_id)
        self._repo = SituationRepository(session, tenant_id)

    # ------------------------------------------------------------------ read

    def _rubro_key(self) -> str | None:
        profile = BusinessProfileRepository(self._session, self._tenant_id).current()
        return profile.industry if profile is not None else None

    def from_template(self) -> dict[str, Situation]:
        """The business's situations that came from its rubro template, by item key."""
        key = self._rubro_key()
        version = self._catalog.published("rubro", key) if key else None
        return self._from_template(_items(self._catalog, version))

    def _from_template(
        self, items: Sequence[TemplateItem] = (), *, adopt: bool = False
    ) -> dict[str, Situation]:
        """By item key. A copy made from a suggestion before keys existed (the «sugeridas» flow
        of feature 017) is recognised by its title among `items`; writing (`adopt`) gives it the
        key, reading does not."""
        rows = self._repo.listing(origen="sugerida_rubro")
        mine = {s.plantilla_clave: s for s in rows if s.plantilla_clave}
        legacy = {s.titulo: s for s in rows if not s.plantilla_clave}
        for item in items:
            situation = legacy.get(item.titulo)
            if item.clave not in mine and situation is not None:
                if adopt:
                    situation.plantilla_clave = item.clave
                mine[item.clave] = situation
        return mine

    def view(self) -> TemplateView:
        key = self._rubro_key()
        rubro = self._catalog.rubro(key)
        version = self._catalog.published("rubro", key) if key else None
        agent = self._agents.current()
        applied = agent.plantilla_rubro_version if agent is not None else None
        return TemplateView(
            rubro=rubro.key if rubro else None,
            nombre=rubro.nombre if rubro else None,
            version=version.version if version else None,
            items=_items(self._catalog, version),
            opciones=dict(version.plantilla or {}) if version else {},
            aplicada=applied,
            novedades=self._news(key, version, applied),
        )

    def _news(
        self, key: str | None, version: CatalogVersion | None, applied: int | None
    ) -> tuple[NewsItem, ...]:
        if key is None or version is None or applied is None or applied >= version.version:
            return ()
        before = {
            i.clave: i for i in _items(self._catalog, self._catalog.version("rubro", key, applied))
        }
        current = _items(self._catalog, version)
        mine = self._from_template(current)
        news: list[NewsItem] = []
        for item in current:
            old = before.get(item.clave)
            if old is None and item.clave not in mine:
                news.append(NewsItem("nueva", item))
            elif old is not None and (old.cuando_aplica, old.que_hace) != (
                item.cuando_aplica,
                item.que_hace,
            ):
                situation = mine.get(item.clave)
                if situation is not None and (situation.plantilla_version or 0) >= version.version:
                    continue  # already taken
                news.append(
                    NewsItem("mejorada", item, situation.id if situation is not None else None)
                )
        return tuple(news)

    # ----------------------------------------------------------------- write

    def apply(
        self, valores: Mapping[str, Mapping[str, str]] | None = None, *, by: uuid.UUID | None
    ) -> Applied:
        """Copy the template of the business's rubro to the business. Applying twice adds nothing
        the business already has; it only brings what is missing."""
        key = self._rubro_key()
        version = self._catalog.published("rubro", key) if key else None
        if version is None:
            raise TemplateError("no_template", "tu rubro no tiene una plantilla publicada")
        given = valores or {}
        items = _items(self._catalog, version)
        mine = self._from_template(items, adopt=True)
        created: list[Situation] = []
        already = 0
        for item in items:
            if item.clave in mine:
                already += 1
                continue
            created.append(self._create(item, given.get(item.clave, {}), version.version, by=by))
        agent = self._agents.current()
        before = dict(agent.process_options or {}) if agent is not None else {}
        options = self._fill_options(version)
        self._agents.upsert(plantilla_rubro_version=version.version)
        DEFAULT_CACHE.invalidate(self._tenant_id)
        active = sum(1 for s in created if s.estado == "activa")
        return Applied(
            version=version.version,
            activas=active,
            borradores=len(created) - active,
            ya_estaban=already,
            opciones=options,
            creadas=tuple(created),
            opciones_antes={"process_options": before} if options else None,
        )

    def accept(
        self, clave: str, valores: Mapping[str, str] | None = None, *, by: uuid.UUID | None
    ) -> Situation:
        """Take one item of the newest version: a new situation, or the improved text replacing the
        business's copy (its switch, files and stages stay as the business left them)."""
        key = self._rubro_key()
        version = self._catalog.published("rubro", key) if key else None
        items = _items(self._catalog, version)
        item = next((i for i in items if i.clave == clave), None)
        if version is None or item is None:
            raise TemplateError("unknown_item", "ese elemento no está en la plantilla de tu rubro")
        existing = self._from_template(items, adopt=True).get(clave)
        if existing is None:
            return self._create(item, valores or {}, version.version, by=by)
        que_hace, complete = _filled(item, valores or {})
        if existing.cuando_aplica != item.cuando_aplica:
            existing.embedding = None
            existing.embedding_model = None
        existing.titulo = item.titulo
        existing.cuando_aplica = item.cuando_aplica
        existing.que_hace = que_hace
        existing.plantilla_version = version.version
        if not complete:
            existing.estado = "borrador"
        self._session.flush()
        return existing

    def mark_seen(self) -> int | None:
        """The business saw the news: what it did not accept stays as it is."""
        key = self._rubro_key()
        version = self._catalog.published("rubro", key) if key else None
        agent = self._agents.current()
        if version is None or agent is None or agent.plantilla_rubro_version is None:
            return None
        self._agents.upsert(plantilla_rubro_version=version.version)
        return version.version

    # --------------------------------------------------------------- helpers

    def _create(
        self, item: TemplateItem, valores: Mapping[str, str], version: int, *, by: uuid.UUID | None
    ) -> Situation:
        que_hace, complete = _filled(item, valores)
        situation = self._situations.create(
            SituationInput(
                titulo=item.titulo,
                cuando_aplica=item.cuando_aplica,
                que_hace=que_hace,
                etapas=list(item.etapas) if item.etapas else None,
                siempre=item.siempre,
            ),
            by=by,
            origen="sugerida_rubro",
        )
        situation.plantilla_clave = item.clave
        situation.plantilla_version = version
        if complete:
            situation.estado = "activa"
        self._session.flush()
        return situation

    def _fill_options(self, version: CatalogVersion) -> tuple[str, ...]:
        """The template's options where the business has none; the rest stays the business's.

        Each one is tried on its own against the business's process: one it does not take (cash on
        delivery on a prepaid template) is left out, since the template only suggests.
        """
        suggested = dict(version.plantilla or {})
        agent = self._agents.current()
        current = dict(agent.process_options or {}) if agent is not None else {}
        template = TEMPLATES.get((agent.process_template_key if agent is not None else "") or "")
        merged = dict(current)
        accepted: list[str] = []
        for name in TEMPLATE_OPTIONS:
            if name not in suggested or current.get(name):
                continue
            trial = {**merged, name: suggested[name]}
            if template is not None:
                try:
                    template.validate_options(trial)
                except ValidationError:
                    continue
            merged = trial
            accepted.append(name)
        if not accepted:
            return ()
        if template is not None:
            merged = template.validate_options(merged).model_dump(mode="json", exclude_none=True)
        self._agents.upsert(process_options=merged)
        return tuple(accepted)


def _filled(item: TemplateItem, valores: Mapping[str, str]) -> tuple[str, bool]:
    """«Qué hace» with the business's values appended, and whether every field was given."""
    missing = [
        label for name, label in item.campos.items() if not (valores.get(name) or "").strip()
    ]
    if missing or not item.campos:
        return item.que_hace, not missing
    filled = "; ".join(f"{label}: {valores[name].strip()}" for name, label in item.campos.items())
    return f"{item.que_hace} ({filled})", True


__all__: Sequence[str] = (
    "Applied",
    "NewsItem",
    "RubroTemplateService",
    "TemplateError",
    "TemplateItem",
    "TemplateView",
    "item_key",
    "key_from_title",
)
