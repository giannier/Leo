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
Ayúdalo a encontrar lo que necesita.
- Si sabe lo que quiere, búscalo con buscar_productos y confírmale precio y disponibilidad.
- Si no sabe, muéstrale categorías o productos destacados con mostrar_opciones.
- Envía la imagen del producto (enviar_recurso) solo si la pide o si ayuda a decidir.
- Cuando elija, agrégalo con gestionar_pedido(accion="agregar") y pregúntale si desea algo más."""

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
