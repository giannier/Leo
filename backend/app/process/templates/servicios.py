# ruff: noqa: E501
# Default instruction text kept verbatim from `plantillas.md` (see `base.py`).
"""Template "Servicios con cotización" (`plantillas.md`, sections 3 and 5).

A services business. A fixed-price service goes into an order of one to three
lines (hosting, say); a quote-only service (`modo_venta = cotizacion`) is handed
to an advisor with a summary. Always paid up front.
"""

from __future__ import annotations

from app.process.templates.base import (
    INICIO_TOOLS_WITHOUT_ORDER,
    PrepaidOptions,
    Template,
    build_stages,
)
from app.schemas.process_options import Calificacion, ProcessOptions

# Most lines in a services order (`plantillas.md`, section 3).
MAX_SERVICE_LINES = 3

IDENTIFICAR = """\
Entiende qué necesita antes de ofrecer.
- Haz máximo 2 preguntas para entender su necesidad (qué quiere lograr, para cuándo, tamaño).
- Busca con buscar_productos el servicio que corresponda.
- Si el servicio es de precio fijo, explícalo y, si lo quiere, agrégalo con gestionar_pedido.
- Si es de cotización, pide los datos clave para cotizar y llama a derivar_a_humano con un resumen claro
  (qué necesita, plazos, presupuesto si lo dijo). Dile que un asesor le enviará la cotización."""

TEMPLATE = Template(
    key="servicios",
    version=1,
    nombre="Servicios con cotización",
    descripcion=(
        "Servicios: los de precio fijo se piden y se pagan antes; los de cotización se "
        "derivan a un asesor con un resumen."
    ),
    stages=build_stages(inicio_tools=INICIO_TOOLS_WITHOUT_ORDER, identificar=IDENTIFICAR),
    defaults=ProcessOptions(
        calificacion=Calificacion(activa=False),
        modalidad_pago="previo",
        datos_requeridos=["nombres", "empresa", "correo"],
    ),
    options_model=PrepaidOptions,
    tipo_pedido="carrito",
    max_lineas=MAX_SERVICE_LINES,
)
