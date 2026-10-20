# ruff: noqa: E501
# Default instruction text kept verbatim from `plantillas.md` (see `base.py`).
"""Template "Venta de productos" (`plantillas.md`, sections 3 and 5).

A shop: several products in a cart, paid up front by default (or on delivery),
no qualification question. Delivery is not an option here: it comes from the
business information (C-22).
"""

from __future__ import annotations

from app.process.templates.base import (
    INICIO_TOOLS_WITH_ORDER,
    Template,
    build_stages,
)
from app.schemas.process_options import Calificacion, ProcessOptions

IDENTIFICAR = """\
Ayúdalo a elegir y llévalo al pedido.
- Si sabe lo que quiere, búscalo con buscar_productos y confírmale precio y disponibilidad.
- Si ya dijo qué productos quiere y cada uno coincide con uno solo, agrégalos en el mismo turno con
  gestionar_pedido(accion="agregar", cantidad=la que dijo): no le preguntes si quiere que los agregues.
- Si no sabe qué elegir, pregúntale para qué lo necesita (una pregunta), recomiéndale UNA opción por esa razón y muestra más con mostrar_opciones solo si las pide.
- Envía la imagen del producto (enviar_recurso) solo si la pide o si ayuda a decidir.
- Cuando elija, agrégalo con gestionar_pedido(accion="agregar") y propón el siguiente paso: cerrar el pedido, o un producto del catálogo que lo complemente si de verdad encaja."""

TEMPLATE = Template(
    key="productos",
    version=1,
    nombre="Venta de productos",
    descripcion=(
        "Una tienda: el cliente arma un pedido con varios productos y lo paga antes "
        "(o contra entrega). Sin pregunta de calificación."
    ),
    stages=build_stages(inicio_tools=INICIO_TOOLS_WITH_ORDER, identificar=IDENTIFICAR),
    defaults=ProcessOptions(
        calificacion=Calificacion(activa=False),
        modalidad_pago="previo",
        datos_requeridos=["nombres", "telefono"],
    ),
    # `modalidad_pago` can be switched to `contra_entrega`: the generic options apply.
    options_model=ProcessOptions,
    tipo_pedido="carrito",
)
