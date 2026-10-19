"""Ready-made templates by industry (Feature 011, HU-01), to clone and adapt.

Written with the named variables of C-105. Meta decides the final category: a reminder about
an order that exists is usually `utility`, anything that invites to buy is `marketing`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LibraryTemplate:
    key: str
    label: str
    category: str
    body: str
    # None: for every industry; otherwise the `rubros.key` values it suits.
    rubros: tuple[str, ...] | None = None
    footer: str | None = None


LIBRARY: tuple[LibraryTemplate, ...] = (
    LibraryTemplate(
        key="pago_pendiente",
        label="Recordatorio de pago pendiente",
        category="utility",
        body=(
            "Hola {{nombre}}, tu pedido en {{negocio}} está pendiente de pago. Si necesitas "
            "ayuda para completarlo, responde a este mensaje."
        ),
    ),
    LibraryTemplate(
        key="aviso_promocion",
        label="Aviso de promoción",
        category="marketing",
        body=(
            "Hola {{nombre}}, en {{negocio}} tenemos una promoción para ti esta semana. "
            "Responde a este mensaje y te contamos los detalles."
        ),
    ),
    LibraryTemplate(
        key="novedades",
        label="Novedades del catálogo",
        category="marketing",
        body=(
            "Hola {{nombre}}, llegaron novedades a {{negocio}}. ¿Quieres que te enviemos el "
            "catálogo? Responde a este mensaje y te lo mandamos."
        ),
    ),
    LibraryTemplate(
        key="recompra",
        label="Volver a comprar",
        category="marketing",
        body=(
            "Hola {{nombre}}, gracias por tu compra de {{producto}} en {{negocio}}. ¿Te gustaría "
            "volver a pedir? Responde a este mensaje y te ayudamos."
        ),
    ),
    LibraryTemplate(
        key="nuevo_programa",
        label="Nuevo curso o programa",
        category="marketing",
        rubros=("instituto",),
        body=(
            "Hola {{nombre}}, en {{negocio}} abrimos inscripciones para un nuevo programa. "
            "¿Quieres recibir la información? Responde a este mensaje."
        ),
    ),
    LibraryTemplate(
        key="menu_semana",
        label="Menú de la semana",
        category="marketing",
        rubros=("restaurante",),
        body=(
            "Hola {{nombre}}, ya está listo el menú de esta semana en {{negocio}}. ¿Te lo "
            "enviamos? Responde a este mensaje."
        ),
    ),
    LibraryTemplate(
        key="stock_nuevo",
        label="Volvió a llegar un producto",
        category="marketing",
        rubros=("retail",),
        body=(
            "Hola {{nombre}}, volvió a llegar {{producto}} a {{negocio}}. ¿Te separamos uno? "
            "Responde a este mensaje."
        ),
    ),
    LibraryTemplate(
        key="recordatorio_control",
        label="Es momento de volver",
        category="marketing",
        rubros=("salud", "servicios"),
        body=(
            "Hola {{nombre}}, en {{negocio}} te recordamos que ya es buen momento para tu "
            "próxima visita. Responde a este mensaje para coordinar."
        ),
    ),
)


def library_for(rubro: str | None) -> list[LibraryTemplate]:
    """The general templates plus the ones of this industry."""
    return [entry for entry in LIBRARY if entry.rubros is None or (rubro in entry.rubros)]


def library_entry(key: str) -> LibraryTemplate | None:
    return next((entry for entry in LIBRARY if entry.key == key), None)
