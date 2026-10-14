"""Golden retrieval set for the knowledge base (Feature 006, HU-04).

Two demo businesses, their documents as they would load them (FAQ, policy,
free text, website), and questions the way customers write them. Each question
names a phrase that only the passage answering it contains: retrieval passes
for that question when a passage among the top 5 contains the phrase.
"""

# Golden text: some quoted sentences run past the line limit.
# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Document:
    type: str  # faq | policy | text | web
    title: str
    content: str


@dataclass(frozen=True)
class Question:
    asks: str
    expects: str  # a phrase only the answering passage contains


@dataclass(frozen=True)
class Business:
    name: str
    documents: tuple[Document, ...]
    questions: tuple[Question, ...]


# Hard distractors: long catalogues on the same subject, so the right passage has
# to beat dozens of near ones, not five.
_PAN_ITEMS = [
    ("Pan de molde blanco", "tajado, de miga suave, ideal para tostadas y loncheras escolares"),
    ("Pan de molde integral", "con salvado de trigo y semillas de linaza, sin azúcar añadida"),
    ("Baguette", "de corteza dorada y crujiente, horneada en piedra cada dos horas"),
    ("Pan de centeno", "denso y aromático, con alcaravea, para acompañar quesos y embutidos"),
    ("Pan de aceitunas", "con aceitunas de botija del valle de Ilo y un toque de orégano"),
    ("Pan de camote", "dulce natural, de color naranja, hecho con camote amarillo asado"),
    ("Pan de maíz morado", "de color violeta, ligeramente dulce, con harina de maíz andino"),
    ("Rosquitas de manteca", "crocantes, para el lonche, se venden por bolsa de un cuarto de kilo"),
    ("Bizcochos de anís", "secos y aromáticos, para mojar en el café de la tarde"),
    ("Pan con pasas", "trenzado, con pasas rubias y ralladura de naranja"),
    ("Panes de hamburguesa", "con ajonjolí, suaves, en paquetes de seis unidades"),
    ("Pan pita", "plano y hueco, para rellenar con falafel o pollo a la plancha"),
    ("Focaccia", "con romero, sal de Maras y aceite de oliva extra virgen"),
    ("Brioche", "rico en mantequilla y huevo, en forma de corona para compartir"),
    ("Pan de coco", "con coco rallado tostado y un glaseado ligero de azúcar"),
    ("Galletas de avena", "con pasas y canela, horneadas en lotes pequeños"),
    ("Galletas de chocolate", "con trozos de chocolate de Piura al 60 % de cacao"),
    ("Keke de naranja", "húmedo, con jugo natural, en molde de un kilo"),
    ("Keke de plátano", "con plátano de seda maduro y nueces picadas"),
    ("Torta de chocolate", "tres pisos de bizcocho con fudge, por porción o entera"),
    ("Selva negra", "con cerezas en almíbar, crema chantilly y virutas de chocolate"),
    ("Torta helada", "de gelatina de fresa sobre bizcocho y crema, para fiestas infantiles"),
    ("Milhojas", "de masa hojaldre con manjar blanco y azúcar en polvo"),
    ("Suspiro a la limeña", "en vasito, con manjar de yemas y merengue al oporto"),
    ("Mazamorra morada", "con frutas secas y canela, los fines de semana"),
    ("Arroz con leche", "cremoso, con clavo de olor y canela en rama"),
    ("Pionono", "enrollado con manjar blanco y espolvoreado con azúcar impalpable"),
    ("Cachitos", "de masa hojaldrada, solos o rellenos de jamón y queso"),
    ("Pan de ajo", "con mantequilla de ajo y perejil, para acompañar pastas"),
    ("Tostadas", "de pan francés secado al horno, en bolsas de medio kilo"),
]
_INS_ITEMS = [
    ("Gestión de recursos humanos", "régimen laboral público, planillas y legajos"),
    ("Control interno", "implementación del sistema de control interno en entidades"),
    ("Archivo y gestión documental", "organización, transferencia y eliminación de documentos"),
    ("Atención al ciudadano", "protocolos de atención, libro de reclamaciones y empatía"),
    ("Proyectos de inversión", "ciclo de inversión, fichas técnicas y expedientes técnicos"),
    ("Tesorería pública", "flujo de caja, cuenta única del tesoro y conciliaciones"),
    ("Contabilidad gubernamental", "estados financieros y presupuestarios del sector público"),
    ("Abastecimiento", "programación multianual, cuadro de necesidades y almacenes"),
    ("Procedimiento administrativo", "plazos, notificaciones y recursos administrativos"),
    ("Gestión por procesos", "mapa de procesos, fichas y manuales de procedimientos"),
    ("Ética pública", "deberes, prohibiciones y conflicto de intereses del servidor"),
    ("Liderazgo en el sector público", "trabajo en equipo, comunicación y manejo de conflictos"),
    ("Power BI para el Estado", "tableros con datos de ejecución presupuestal"),
    ("Gobierno digital", "trámites en línea, firma digital e interoperabilidad"),
    ("Seguridad y salud en el trabajo", "comité, matriz de riesgos y plan anual"),
    ("Defensa civil", "gestión del riesgo de desastres y planes de contingencia"),
    ("Saneamiento de bienes", "registro de bienes inmuebles estatales y SINABIP"),
    ("Obras públicas", "valorizaciones, adicionales y liquidación de obras"),
    ("Arbitraje en contrataciones", "conciliación y arbitraje en contratos del Estado"),
    ("Responsabilidad administrativa", "procedimiento sancionador de la Contraloría"),
    ("Planeamiento estratégico", "PEI, POI y articulación con el CEPLAN"),
    ("Presupuesto público", "programación, formulación y evaluación del presupuesto"),
    ("Gestión municipal", "rentas, licencias y servicios locales"),
    ("Contrataciones menores a 8 UIT", "procedimientos internos y órdenes de servicio"),
    ("Oratoria para servidores", "presentaciones efectivas y manejo del miedo escénico"),
]


def _catalogue(intro: str, items: list[tuple[str, str]]) -> str:
    return intro + "\n\n" + "\n\n".join(f"{name}: {description}." for name, description in items)


PANADERIA = Business(
    name="Panadería Doña Rosa",
    documents=(
        Document(
            "faq",
            "Preguntas frecuentes",
            "¿Hacen tortas personalizadas? Sí. Las tortas de cumpleaños personalizadas se piden "
            "con 48 horas de anticipación y un adelanto del 50 %.\n\n"
            "¿Tienen opciones sin gluten? Tenemos pan de molde sin gluten los martes y viernes, "
            "hecho en un horno separado.\n\n"
            "¿Puedo recoger en tienda? Sí, los pedidos web se recogen en Av. Benavides 1520, "
            "Surco, desde las 7 de la mañana.\n\n"
            "¿Venden al por mayor? Atendemos cafeterías y restaurantes con un pedido mínimo de "
            "200 panes diarios y factura.",
        ),
        Document(
            "policy",
            "Envíos y cambios",
            "Delivery: repartimos en Surco, Barranco, Chorrillos y Miraflores. El envío cuesta "
            "S/ 6 y llega en unos 40 minutos. Pedidos mayores a S/ 80 tienen envío gratis.\n\n"
            "Cambios y reclamos: si un producto llega dañado, envíanos una foto dentro de las 2 "
            "horas y lo reponemos sin costo. No aceptamos devoluciones de tortas personalizadas.",
        ),
        Document(
            "text",
            "Nuestra historia y horarios",
            "Doña Rosa empezó a hornear en 1987 en un pequeño local de Chorrillos. Hoy usamos la "
            "misma masa madre, alimentada todos los días.\n\n"
            "Horario: lunes a sábado de 7:00 a 21:00. Domingos y feriados de 8:00 a 13:00.\n\n"
            "Formas de pago: Yape, Plin, efectivo y tarjeta al recibir. Para pedidos grandes "
            "aceptamos transferencia al BCP.",
        ),
        Document(
            "web",
            "Mi web",
            "# Panes\nhttps://donarosa.pe/panes\n\nPan francés, ciabatta, pan de yema y chancay "
            "salen calientes a las 7, a las 12 y a las 17.\n\n"
            "# Talleres\nhttps://donarosa.pe/talleres\n\nTaller de panadería para niños los "
            "sábados a las 10, cupo de 12 niños, S/ 45 por niño con materiales incluidos.\n\n"
            "# Navidad\nhttps://donarosa.pe/navidad\n\nEl panetón artesanal de 1 kg cuesta S/ 59 "
            "y se reserva desde noviembre.",
        ),
        Document(
            "web",
            "Blog de la panadería",
            "# Masa madre\nhttps://donarosa.pe/blog/masa-madre\n\nLa masa madre es una mezcla de "
            "harina y agua que fermenta con levaduras salvajes. La nuestra tiene más de treinta "
            "años y la alimentamos con harina integral de trigo andino. La fermentación lenta, de "
            "dieciocho horas, le da al pan una miga abierta, una corteza crujiente y un sabor "
            "ligeramente ácido que dura varios días.\n\n"
            "# Cómo conservar el pan\nhttps://donarosa.pe/blog/conservar\n\nGuarda el pan en una "
            "bolsa de tela, nunca en la refrigeradora: el frío lo endurece más rápido. Si sobró, "
            "córtalo en tajadas y congélalo; se tuesta directamente sin descongelar. El pan de yema "
            "aguanta tres días en buen estado a temperatura ambiente.\n\n"
            "# Harinas peruanas\nhttps://donarosa.pe/blog/harinas\n\nTrabajamos con harinas de "
            "quinua, kiwicha y cañihua de productores de Puno y Junín. Las mezclamos con trigo para "
            "que el pan suba bien. El pan de quinua tiene más proteína y una textura más densa que "
            "a muchos clientes les gusta para el desayuno con palta.\n\n"
            "# Nuestro equipo\nhttps://donarosa.pe/blog/equipo\n\nSomos catorce personas entre "
            "panaderos, pasteleras y repartidores. El turno de panadería empieza a las tres de la "
            "madrugada. Todos los años capacitamos a dos jóvenes del barrio como aprendices.\n\n"
            "# Recetas con pan del día anterior\nhttps://donarosa.pe/blog/recetas\n\nCon el pan "
            "que sobra se puede hacer budín, torrejas o pan rallado casero. El budín de pan con "
            "pasas y canela es una receta de la abuela de Doña Rosa que todavía vendemos los "
            "domingos en porciones individuales.\n\n"
            "# Pasteles de temporada\nhttps://donarosa.pe/blog/temporada\n\nEn otoño hacemos "
            "pie de manzana y en invierno alfajores de maicena rellenos con manjar blanco. En "
            "octubre sale el turrón de Doña Pepa con miel de chancaca, anís y grageas de colores.",
        ),
        Document(
            "text",
            "Catálogo completo",
            _catalogue("Todo lo que horneamos, de lunes a domingo.", _PAN_ITEMS),
        ),
        Document(
            "text",
            "Carta de productos",
            "Pan francés: el clásico de cada mañana, crocante por fuera y suave por dentro. "
            "Ciabatta: pan italiano de corteza fina y alveolos grandes, ideal para sánguches. Pan "
            "de yema: dulce y amarillo, hecho con huevos de corral. Chancay: pan dulce de anís, "
            "esponjoso, receta del norte chico.\n\n"
            "Croissant de mantequilla: laminado a mano durante dos días. Empanadas de carne, pollo "
            "y queso, horneadas, no fritas. Alfajores de maicena con manjar blanco. Pie de limón con "
            "merengue italiano. Cheesecake de maracuyá. Tres leches en porción y en molde "
            "familiar.\n\n"
            "Bebidas: café pasado de Chanchamayo, chocolate caliente con canela, jugos de papaya "
            "y de naranja, chicha morada de la casa y emoliente por las mañanas.\n\n"
            "Desayunos: pan con chicharrón y camote frito, pan con tortilla de verduras, pan con "
            "palta y huevo, tamales y humitas los fines de semana.",
        ),
    ),
    questions=(
        Question("hola, a q hora abren el domingo?", "Domingos y feriados"),
        Question("cuanto cobran por el delivery a barranco", "El envío cuesta S/ 6"),
        Question("quiero una torta para el cumple de mi hija para el sabado", "48 horas"),
        Question("tienen pan sin gluten?", "sin gluten"),
        Question("se puede pagar con yape?", "Yape, Plin"),
        Question("me llego el pan aplastado que hago", "llega dañado"),
        Question("a que hora sale el pan calientito", "salen calientes"),
        Question("hacen talleres para niños?", "Taller de panadería para niños"),
        Question("precio del paneton", "panetón artesanal"),
        Question("donde queda la tienda para recoger", "Av. Benavides 1520"),
        Question("tengo una cafeteria, me venden por mayor?", "pedido mínimo"),
        Question("desde cuando existen", "1987"),
        Question("el envio es gratis?", "envío gratis"),
        Question("como guardo el pan para que no se ponga duro", "bolsa de tela"),
        Question("tienen pan de quinua?", "pan de quinua"),
        Question("venden tamales?", "tamales y humitas"),
    ),
)

INSTITUTO = Business(
    name="Academia Capacita",
    documents=(
        Document(
            "faq",
            "Preguntas frecuentes",
            "¿El certificado tiene validez? Sí, nuestros certificados son emitidos a nombre de la "
            "academia con registro en el Ministerio de Educación y código de verificación en "
            "línea.\n\n"
            "¿Cuánto demora el certificado? El certificado digital llega al correo en 7 días "
            "hábiles después de aprobar el examen final.\n\n"
            "¿Las clases quedan grabadas? Todas las sesiones en vivo quedan grabadas en el aula "
            "virtual por 6 meses.",
        ),
        Document(
            "policy",
            "Pagos y reembolsos",
            "Los cursos se pagan por Yape, transferencia o tarjeta. Puedes pagar en 2 cuotas sin "
            "intereses si el curso cuesta más de S/ 300.\n\n"
            "Reembolso: devolvemos el 100 % si lo pides antes de la segunda clase. Después ya no "
            "hay devolución, pero puedes cambiarte a otra fecha una sola vez.",
        ),
        Document(
            "text",
            "Cómo funcionan los cursos",
            "Las clases son en vivo por Zoom, martes y jueves de 7 a 9 de la noche. Cada curso "
            "dura 8 semanas.\n\n"
            "Para aprobar necesitas 80 % de asistencia y una nota mínima de 14 en el examen "
            "final. Si desapruebas, tienes un examen de rezagados gratis.\n\n"
            "El certificado físico se envía a provincia por Olva con un costo de S/ 15.",
        ),
        Document(
            "web",
            "Mi web",
            "# Docentes\nhttps://capacita.pe/docentes\n\nNuestros docentes son especialistas con "
            "más de 10 años de experiencia en el sector público.\n\n"
            "# Empresas\nhttps://capacita.pe/empresas\n\nCapacitación in house para empresas "
            "desde 15 colaboradores, con factura y horario a medida.",
        ),
        Document(
            "web",
            "Noticias de la academia",
            "# Nueva sede\nhttps://capacita.pe/noticias/sede\n\nInauguramos una oficina de "
            "atención en el centro de Lima, en el jirón Camaná, para entregar certificados físicos "
            "y resolver dudas en persona de lunes a viernes.\n\n"
            "# Convenios\nhttps://capacita.pe/noticias/convenios\n\nFirmamos convenios con tres "
            "municipalidades distritales para capacitar a su personal en atención al ciudadano, "
            "gestión documental y ética en la función pública.\n\n"
            "# Egresados\nhttps://capacita.pe/noticias/egresados\n\nMás de cuatro mil egresados "
            "trabajan hoy en entidades del Estado. Muchos llegaron a jefaturas después de llevar el "
            "diplomado de gestión pública y el curso de contrataciones.\n\n"
            "# Becas\nhttps://capacita.pe/noticias/becas\n\nCada semestre ofrecemos medias becas "
            "para madres jefas de hogar y para personas con discapacidad, previa evaluación "
            "socioeconómica y entrevista.\n\n"
            "# Aula virtual\nhttps://capacita.pe/noticias/aula\n\nRenovamos el aula virtual: "
            "ahora tiene foros por curso, descarga de materiales en PDF y un calendario con las "
            "fechas de los exámenes y de las entregas de trabajos.",
        ),
        Document(
            "text",
            "Cursos cortos",
            _catalogue("Cursos cortos de cuatro semanas, en vivo y con certificado.", _INS_ITEMS),
        ),
        Document(
            "text",
            "Catálogo de cursos",
            "Curso de contrataciones con el Estado: ley de contrataciones, procedimientos de "
            "selección, ejecución contractual y uso del SEACE. Dirigido a personal de logística.\n\n"
            "Curso de gestión pública: planeamiento estratégico, presupuesto por resultados, "
            "modernización del Estado y control gubernamental.\n\n"
            "Curso de SIAF: registro administrativo, fases del gasto, certificación, compromiso, "
            "devengado y girado, con casos prácticos en el módulo.\n\n"
            "Curso de redacción administrativa: oficios, informes, memorandos y cartas, con "
            "corrección personalizada de cada documento.\n\n"
            "Curso de Excel para la gestión: tablas dinámicas, fórmulas y tableros de control "
            "aplicados a reportes de presupuesto.",
        ),
    ),
    questions=(
        Question("el certificado es valido para el estado?", "Ministerio de Educación"),
        Question("en cuanto tiempo me llega mi certificado", "7 días hábiles"),
        Question("si no puedo entrar a la clase en vivo la puedo ver despues?", "grabadas"),
        Question("puedo pagar en cuotas", "2 cuotas"),
        Question("me puedo retirar y que me devuelvan mi plata", "100 %"),
        Question("que dias son las clases", "martes y jueves"),
        Question("cuanto dura el curso", "8 semanas"),
        Question("con cuanto se aprueba", "nota mínima de 14"),
        Question("vivo en arequipa me mandan el certificado fisico?", "Olva"),
        Question("quienes son los profesores", "especialistas"),
        Question("hacen capacitaciones para mi empresa", "in house"),
        Question("y si jalo el examen?", "rezagados"),
        Question("dan becas?", "medias becas"),
        Question("tienen curso de siaf", "Curso de SIAF"),
        Question("donde recojo el certificado fisico en lima", "jirón Camaná"),
    ),
)

BUSINESSES = (PANADERIA, INSTITUTO)
RECALL_TARGET = 0.8
# With about ten passages per business, the top 5 is half the corpus: recall@5 alone
# says little. The nearest passage being the right one (recall@1) is held too.
RECALL_AT_1_TARGET = 0.7
K = 5
