# ruff: noqa: E501
# Default instruction text kept verbatim from `plantillas.md` (see `base.py`).
"""Template "Cursos y certificaciones" (`plantillas.md`, sections 3 and 5).

Built from Capacita v21: the customer chooses between certifying experience they
already have (a sale of a package with N selections) and a full programme
(handed to an advisor). Always paid up front. The assistant only greets in the
first turn and the offer comes in the next one.
"""

from __future__ import annotations

from app.process.templates.base import (
    INICIO_TOOLS_GREET_ONLY,
    PrepaidOptions,
    Stage,
    Template,
    build_stages,
)
from app.schemas.process_options import Calificacion, Camino, ProcessOptions

INICIO = """\
Es tu primer mensaje. Envía el recurso "saludo" si existe, saluda con calidez y pregúntale su nombre.
Una sola pregunta y te detienes. Aunque venga de un anuncio, la oferta va en el siguiente turno."""

IDENTIFICAR = """\
Paso 1 · El tema. Si aún no lo dijo, pregúntale qué tema necesita certificar.
Cuando lo diga, búscalo con buscar_productos. La respuesta es SIEMPRE SÍ: se certifica cualquier tema.
Si no aparece en el catálogo, confírmalo con el nombre que escribió el cliente. Prohibido decir "no lo
tenemos" o derivar por este motivo. Si menciona varios temas, confírmalos todos juntos en una lista.
Si no sabe qué pedir, muéstrale categorías con mostrar_opciones.

Paso 2 · El ancla. Antes del precio del paquete, menciona el precio por unidad del certificado.

Paso 3 · Los paquetes. Muéstralos con mostrar_opciones(tipo="productos", filtro="paquetes"), envía el recurso
del paquete destacado y empuja el destacado en un mensaje corto. Copia los precios tal cual.
Cuando elija, llama a gestionar_pedido(accion="agregar") con el paquete y las selecciones que ya dijo."""

PEDIDO = """\
El paquete aún no tiene todas sus certificaciones (el ESTADO dice cuántas faltan). No avances a los datos.
"¡Buenísimo! Ya tienes [X]. Te faltan [N] más 😊 ¿Qué otros temas te sirven?"
Cada tema nuevo: gestionar_pedido(accion="elegir", seleccion="<tema>"). Si no se le ocurren, muéstrale
categorías con mostrar_opciones. Si quiere cambiar de paquete, quita el actual y agrega el nuevo."""

ASESORIA = """\
Quiere un programa completo.
Si el ESTADO NO dice "asesoría ya derivada": envía la malla si existe el recurso, pregúntale qué programa
le interesa y su disponibilidad de horario, y llama a derivar_a_humano(motivo="asesoria") con el resumen.
Si ya se derivó: responde dudas generales y, si dice que prefiere solo certificar su experiencia, llama a
elegir_camino("certificacion")."""

TEMPLATE = Template(
    key="certificaciones",
    version=1,
    nombre="Cursos y certificaciones",
    descripcion=(
        "Cursos, certificaciones y paquetes: el cliente elige entre certificar lo que ya sabe "
        "o llevar un programa completo, que atiende un asesor. Siempre pago previo."
    ),
    stages=build_stages(
        inicio_tools=INICIO_TOOLS_GREET_ONLY,
        identificar=IDENTIFICAR,
        overrides={Stage.INICIO: INICIO, Stage.PEDIDO: PEDIDO, Stage.ASESORIA: ASESORIA},
    ),
    defaults=ProcessOptions(
        calificacion=Calificacion(
            activa=True,
            pregunta=(
                "¿Quieres solo certificar la experiencia que ya tienes, o llevar un programa completo?"
            ),
            caminos=[
                Camino(
                    clave="certificacion",
                    titulo="Solo certificar",
                    tipo="venta",
                    descripcion="Ya tiene la experiencia",
                ),
                Camino(
                    clave="programa",
                    titulo="Programa completo",
                    tipo="asesoria",
                    descripcion="Quiere estudiar",
                ),
            ],
        ),
        modalidad_pago="previo",
        datos_requeridos=["dni", "nombres", "apellidos", "correo", "telefono"],
    ),
    options_model=PrepaidOptions,
    tipo_pedido="paquete",
)
