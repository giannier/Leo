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
- Antes de preguntar repasa lo que ya dijo: no preguntes lo que ya contó ni repitas una pregunta. Pregunta de a una y sobre lo último que dijo. Con 2 respuestas ya recomiendas; no hagas más de 3 preguntas en total (una cotización sigue el bloque COTIZACIÓN).
- Con lo que contó, busca con buscar_productos y, si aún no eligió, recomienda UNA opción explicando por qué le sirve a su caso.
- Si el servicio es de precio fijo y el cliente dice que lo quiere, lo contrata o muestra interés claro ("me interesa", "¿cómo seguimos?", "¿cómo pago?"), agrégalo en el mismo turno con gestionar_pedido: no le preguntes si quieres agregarlo. Si solo pregunta, explícalo y propón el siguiente paso.
- Si es de cotización y el producto pide datos: sigue el bloque COTIZACIÓN. Si no pide datos, o lo que necesita es a medida o junta varios servicios: resume en dos líneas lo que entendiste (qué necesita, plazo, presupuesto si lo dijo) y pídele su nombre y correo, explicando que es para que el asesor le envíe la propuesta, y espera su respuesta. Con su nombre y correo (o si prefiere no darlos) no pidas más datos ni uses registrar_datos_cliente: llama a derivar_a_humano(motivo="asesoria") con ese resumen, incluyendo esos datos, y dile que un asesor le escribirá pronto."""

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
