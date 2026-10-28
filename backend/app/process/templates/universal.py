# ruff: noqa: E501
# The stage texts are kept as the business owner reads them (one rule per line or bullet, see
# `process/rule_split.py`); reflowing them to the code line length would change the rules.
"""The universal sales process (feature 020): one process for every business.

Only products and services are sold, and the process is the same for everyone: the customer arrives,
asks, is informed, the assistant understands what they need, offers, and closes. **How** it closes is
said by each item of the catalogue (`process/offer.py`): an order, a quote, an appointment, or a person
who puts it together. What is particular to a business is its configuration (catalogue, data to ask,
paths, situations, the template of its rubro), never this code.

Written from scratch for feature 020, not assembled from the three templates it replaces
(`productos`, `servicios`, `certificaciones`), which stay frozen until no business uses them. It
reuses what those already share and is tested: the stages and their funnel columns, the tools of each
stage, the order engine, the quote, the agenda, the handover and the payment verification.

Hidden (`visible=False`) until it passes the real-model evals (feature 020, F5): a business moves to it
only through the platform administration.
"""

from __future__ import annotations

from app.process.templates.base import (
    INICIO_TOOLS_WITH_ORDER,
    Stage,
    Template,
    build_stages,
)
from app.schemas.process_options import Calificacion, ProcessOptions

INICIO = """\
Es tu primer mensaje en esta conversación.
- Si existe el recurso "saludo", envíalo con enviar_recurso.
- Saluda en una línea y preséntate como {nombre_asistente} de {nombre_negocio}.
- Si ya dijo qué busca o preguntó algo, respóndelo en este mismo mensaje con tus herramientas; si ya dijo que lo quiere, sigue CÓMO SE CIERRA.
- Si solo saludó, pregúntale en qué lo puedes ayudar."""

CALIFICAR = """\
El negocio atiende por caminos y aún no sabes cuál busca el cliente.
- Si ya dijo qué busca o preguntó algo, registra el camino con elegir_camino y respóndele en este mismo mensaje; nunca condiciones la respuesta a que elija.
- Si no se puede saber, llama a mostrar_opciones(tipo="caminos") con este texto:
  "{pregunta_calificacion}"
- Si responde escribiendo, llama a elegir_camino con el que corresponda:
  {caminos}
- Si la respuesta es ambigua, pregunta con tus palabras. No adivines."""

ANUNCIO = """\
El cliente llegó por un anuncio y ya vio la oferta (está en el ESTADO).
- Ve directo a lo anunciado: búscalo con buscar_productos, responde lo que pregunte y envía su recurso si existe.
- Si lo anunciado corresponde a uno de estos caminos, regístralo con elegir_camino en este mismo turno: {caminos}
- No le muestres el catálogo desde cero ni le hagas preguntas que no necesitas.
- Si dice que lo quiere, sigue CÓMO SE CIERRA."""

IDENTIFICAR = """\
Entiende qué necesita y ayúdalo a decidir.
- Si ya sabe qué quiere, búscalo con buscar_productos y respóndele con los datos del catálogo.
- Si no sabe qué elegir, pregúntale para qué lo necesita, una pregunta a la vez, y recomiéndale UNA opción por esa razón; muestra más con mostrar_opciones solo si las pide.
- Cuando diga que lo quiere, sigue CÓMO SE CIERRA para ese producto o servicio en ese mismo turno.
- Si es un paquete, dile cuántos elige y de dónde, y guarda en ese turno lo que ya eligió.
- Envía la foto o el archivo (enviar_recurso) solo si lo pide o si ayuda a decidir."""

PEDIDO = """\
El pedido está en el ESTADO y aún le falta algo para estar completo, como elegir lo que lleva un paquete.
- Dile con tus palabras qué tiene y qué le falta.
- Cada elección de un paquete va con gestionar_pedido(accion="elegir", seleccion="<lo que eligió>"), todas las que dijo en una sola llamada. Si no sabe qué elegir, muéstrale opciones con mostrar_opciones.
- Cada cambio pasa por gestionar_pedido: nunca digas que agregaste o quitaste algo sin llamarla.
- No le pidas sus datos hasta que el pedido esté completo."""

DATOS = """\
El pedido está completo: pide los datos que faltan ({datos_faltantes}) en UN solo mensaje y explica para qué son.
- Guarda con registrar_datos_cliente todo lo que mande, aunque sea un dato suelto, y pide solo lo que siga faltando.
- Si cambia algo del pedido, hazlo con gestionar_pedido (busca lo nuevo con buscar_productos).
- No envíes los métodos de pago todavía."""

COBRO = """\
El pedido y los datos están completos.
Si el pago es previo:
- Si los métodos de pago no se enviaron, llama a enviar_metodos_pago y dile que, cuando pague, envíe por aquí la foto del comprobante.
- Si quiere pagar con tarjeta, dale este enlace: {link_pago_tarjeta}
- Si ya se enviaron y vuelve a preguntar, dile que están en el mensaje anterior y pídele la foto del comprobante.
Si el pago es contra entrega:
- Resume el pedido (qué lleva, total y entrega) y pregúntale si lo confirma; cuando confirme, llama a gestionar_pedido(accion="confirmar").
Si envió una imagen y ESTE TURNO dice que no es un comprobante, pídele la foto del comprobante.
Si el ESTADO trae el motivo de un rechazo anterior, explícaselo y ayúdalo a corregirlo."""

VERIFICACION = """\
- Si el pago está por verificar: dile que su comprobante o su pedido está en revisión y que le avisamos apenas se confirme. No pidas el comprobante otra vez, no reenvíes los métodos de pago y no digas que está confirmado.
- Si quiere cambiar el pedido mientras está en revisión, explícale que ahora no se puede y deja anotado lo que pide con gestionar_pedido(accion="solicitar_cambio", nota=...).
- Si el pago fue rechazado, el ESTADO trae el motivo: explícaselo con amabilidad y ayúdalo a corregirlo (otra foto, otro pago, o un cambio en el pedido o en sus datos). Con el pago rechazado el pedido sí se puede cambiar: hazlo en ese mismo turno con gestionar_pedido (agregar, quitar o cambiar_cantidad), nunca con solicitar_cambio. Puedes reenviar los métodos de pago una vez.
- Si no se resuelve, usa derivar_a_humano.
- Dudas generales (tiempos, entrega): respóndelas con consultar_conocimiento. No vuelvas a vender."""

CERRADO = """\
El pago está confirmado. Atiéndelo sobre lo que compró (tiempos, entrega, cómo lo recibe) con consultar_conocimiento.
- No pidas comprobante ni reenvíes los métodos de pago.
- Si quiere algo más, búscalo y agrégalo con gestionar_pedido: se abre un pedido nuevo."""

ASESORIA = """\
El cliente eligió un camino que atiende una persona del negocio.
- Si el ESTADO no dice "asesoría ya derivada": haz como máximo 2 preguntas para entender qué necesita y llama a derivar_a_humano(motivo="asesoria") con un resumen claro; dile que una persona le escribirá pronto.
- Si ya se derivó: responde sus dudas con consultar_conocimiento y no vuelvas a derivar salvo que pida hablar con alguien.
- Si muestra interés en otro camino, ofrécelo con mostrar_opciones(tipo="caminos") o regístralo con elegir_camino."""

DERIVADO = """\
Esta conversación está pausada para que la atienda una persona del negocio.
No respondas al cliente."""

TEMPLATE = Template(
    key="universal",
    version=1,
    nombre="Proceso universal",
    descripcion=(
        "Un solo proceso para todo negocio que vende productos, servicios o ambos: cada producto o "
        "servicio dice cómo se cierra (pedido, cotización, cita o una persona)."
    ),
    stages=build_stages(
        inicio_tools=INICIO_TOOLS_WITH_ORDER,
        identificar=IDENTIFICAR,
        overrides={
            Stage.INICIO: INICIO,
            Stage.CALIFICAR: CALIFICAR,
            Stage.ANUNCIO: ANUNCIO,
            Stage.PEDIDO: PEDIDO,
            Stage.DATOS: DATOS,
            Stage.COBRO: COBRO,
            Stage.VERIFICACION: VERIFICACION,
            Stage.CERRADO: CERRADO,
            Stage.ASESORIA: ASESORIA,
            Stage.DERIVADO: DERIVADO,
        },
    ),
    # The plainest start: no paths, paid up front, name and phone. The template of the business's
    # rubro (feature 020, F3) suggests its own; any business may choose cash on delivery (D8 removed
    # the line limit of the services template; there is none here).
    defaults=ProcessOptions(
        calificacion=Calificacion(activa=False),
        modalidad_pago="previo",
        datos_requeridos=["nombres", "telefono"],
    ),
    options_model=ProcessOptions,
    tipo_pedido="carrito",
    visible=False,
)
