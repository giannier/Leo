# 019 · Investigación de mercado para un agente general (2026-10-21)

Qué se investigó: qué ya funciona en el mercado de agentes de WhatsApp y atención con IA, qué reglas de plataforma
condicionan el diseño, qué necesita cada rubro y qué se puede hacer mejor. Se buscaron fuentes oficiales cuando
existen. **Casi todo lo demás es contenido de proveedores** (blogs y páginas comerciales): sirve para saber qué se
ofrece y qué se promete, no para creer sus cifras. Cada hallazgo dice qué tan firme es su respaldo.

Leyenda: **[oficial]** documentación de la plataforma · **[estudio]** investigación publicada · **[proveedor]**
afirmación de quien vende el producto · **[no verificado]** no se pudo confirmar.

## 1. El mercado

**Quién hace qué**
- Plataformas de mensajería con IA agregada: Wati (Astra), Respond.io, Manychat, Kommo, Trengo, Gallabox, AiSensy,
  Interakt, Chatbase, Botpress, Yellow.ai. [proveedor] Las guías de comparación las escriben ellas mismas; coinciden en
  qué comparar: conexión oficial a la API de WhatsApp, caso de uso (reservas), integración con CRM y calendario,
  calidad de respuesta, traspaso a humano, modelo de precio y tiempo para salir en vivo. ([AiSensy](https://m.aisensy.com/blog/best-whatsapp-ai-agent-platforms/), [Uptail](https://www.uptail.ai/blog/best-whatsapp-ai-agent-for-small-business-2026-how-to-choose-compare-and-set-one-up))
- América Latina: Darwin AI (bandeja compartida + IA que vende y califica), Jelou (comercio por WhatsApp con pagos y
  seguimiento), Yavendió (Lima, agentes para comercio electrónico). [proveedor] ([Darwin](https://blog.getdarwin.ai/en/bandeja-compartida-de-whatsapp-por-qué-tu-negocio-pierde-ventas-usando-ia), [Jelou](https://www.jelou.ai/en/blog/how-smbs-in-latin-america-are-revolutionizing-their-sales-with-whats-app-and-ai), [Yavendió](https://www.contxto.com/en/funding/yavendio-raises-850000-to-boost-e-commerce-sales-in-latin-america-through-ai))
- Atención al cliente de referencia: Intercom Fin cobra 0,99 USD por resolución y reporta 56 % de resolución en promedio
  a 30 días [proveedor, caso propio]; la calidad de la base de conocimiento es el predictor más fuerte, y el **traspaso
  a una persona es lo que casi todos construyen peor**. ([guía](https://www.open.cx/blog/intercom-ai-auto-responses), [traspasos](https://www.eesel.ai/blog/fin-ai-handovers))

**Lo que casi todos hacen:** responder preguntas con la base de conocimiento, reservar, calificar, recordar, derivar.
**Lo que casi nadie hace bien** (oportunidad para Leo): componer las capacidades según el rubro, cotizar de verdad,
medir resultados de negocio (no solo "resoluciones"), un traspaso con contexto, y arrancar sin que el dueño sea técnico.

## 2. Reglas de la plataforma que condicionan el diseño

| Regla | Qué implica para Leo | Respaldo |
|---|---|---|
| Meta prohíbe desde el 15-ene-2026 los chatbots de propósito general en la API de WhatsApp; la IA **de un negocio para atender y vender** sigue permitida, y Meta decide a su criterio si la IA es la función principal | Leo es del tipo permitido. Debe seguir siendo un agente **del negocio** (ya habla solo de él) y no un asistente abierto. Conviene decirlo en los términos y en la configuración | [proveedor/prensa] ([Respond.io](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban), [Alibaba Cloud](https://www.alibabacloud.com/help/en/chatapp/use-cases/whatsapp-ai-policy-2026-guide)); texto oficial no verificado |
| Ventana de servicio de 24 h; los anuncios que abren WhatsApp dan 72 h de conversación gratis; desde el 1-oct-2026 se cobran los mensajes de servicio y utilidad dentro de la ventana del cliente (1.000 mensajes de servicio gratis al mes por número, según un proveedor) | Atribuir el origen (anuncio o enlace) y **mostrar el costo estimado** de mensajes. Fuera de 24 h solo plantillas aprobadas | [proveedor] ([Respond.io](https://respond.io/blog/whatsapp-pricing-change-2026), [Enrich Labs](https://www.enrichlabs.ai/blog/whatsapp-advertising-complete-guide-2026)); cifras exactas no verificadas |
| Política de comercio: categorías prohibidas o restringidas (armas, drogas, juego con dinero, préstamos de día, farmacia solo para recordatorios y citas, etc.) | Aviso en la configuración según el rubro; no ofrecer lo que la plataforma no deja vender por WhatsApp | [proveedor] ([Tyntec](https://www.tyntec.com/helpcenter/docs/faqs/whatsapp-business/whatsapp-commerce-policy/), [360dialog](https://docs.360dialog.com/waba-platform-policy-enforcement)) |
| Instagram y Messenger: ventana de 24 h; la etiqueta de agente humano da 7 días **solo para respuestas manuales**; sin etiquetas para promociones; revisión de la aplicación | Diseñar las automatizaciones de redes sociales con 24 h como límite duro | [oficial/guías] ([Meta](https://developers.facebook.com/docs/messenger-platform/send-messages), [política](https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy)) |
| Google Calendar: leer eventos es un permiso **sensible**: verificación con declaración y video (3 a 5 días hábiles en promedio); los restringidos exigen evaluación de seguridad | Calendario **propio primero**; Google Calendar y Cal.com como adaptadores | [oficial] ([Google](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification), [Nylas](https://developer.nylas.com/docs/provider-guides/google/google-verification-security-assessment-guide/)) |
| Eventos de conversión a Meta (Lead, Compra) por la API de Conversiones: Meta dice que la optimización por compra no está disponible para WhatsApp (sí para Messenger); los proveedores dicen lo contrario | Registrar el clic del anuncio (`ctwa_clid`) y enviar eventos queda como mejora opcional, a verificar | [oficial vs proveedor, en conflicto] ([Meta](https://developers.secure.facebook.com/docs/marketing-api/conversions-api/business-messaging/)) |
| Perú: Ley 29733 y su reglamento (DS 016-2024-JUS): consentimiento informado y finalidad; más estricto con mensajes comerciales | Registro de consentimiento y baja ya existen en campañas; el aprendizaje entre negocios requiere anonimizar y consentimiento | [oficial parcial] ([resumen](https://regulations.clym.io/law-for-personal-data-protection-lpdp-peru)); sin texto específico para IA |

### 2.1 Lo que dice el texto oficial (política de mensajería de WhatsApp Business, actualizada el 23-sep-2026)

Leída en [whatsappbusiness.com/policy](https://whatsappbusiness.com/policy/) (la política de comercio de Meta, enlazada desde ahí, no se leyó).

* **Sección 4, prohibido** "a criterio de Meta": armas de fuego; alcohol y tabaco; drogas (con o sin receta); **productos médicos y de salud**;
  divisas reales o virtuales; apuestas con dinero real; servicios para adultos y de citas; marketing multinivel; y
  "préstamos de día, adelantos de sueldo, préstamos entre personas, cobranza y fianzas". Aplica aunque el negocio tenga licencia.
* **Sección 5, excepciones por país, con licencia y solo para mayores de 18**: apuestas en línea (entre otros países, Perú),
  medicamentos sin receta y alcohol (unos 90 países; **si Perú está en esas dos listas no se verificó**). Prohibido ofrecer una
  experiencia de compra de esos bienes y prohibido mensajear sobre ellos desde la app WhatsApp Business.
* **Sección 3, datos**: "no compartas ni pidas números completos de tarjeta, de cuenta ni **de documento de identidad** u otros
  identificadores sensibles"; y no usar WhatsApp para telemedicina ni para enviar o pedir información de salud si la regulación lo
  restringe.
* **Sección 2, automatización**: se permite responder con automatización dentro de las 24 h, pero con una vía de escalamiento
  "pronta, clara y directa" (traspaso en el chat, teléfono, correo, web, tienda o formulario).
* **No dice nada** sobre IA ni sobre avisar que es un asistente virtual (eso estaría en otros documentos de Meta, no leídos), ni
  si dar soporte antes o después de la venta de un bien restringido cuenta como "promover".

Consecuencia para Leo: el aviso de cada rubro sale de aquí (migración 0053). **Hallazgo abierto:** la plantilla `certificaciones`
pide el DNI en el chat, y la sección 3 pide no hacerlo; la decisión es de Giannier (datos personales), ver `clarifications.md` C-01.

## 3. Qué necesita cada rubro (capacidades observadas)

| Rubro | Lo que el agente hace allí | Particularidades |
|---|---|---|
| Restaurante y café | menú y horarios, **reserva de mesa**, pedido para llevar o delivery, recordatorios, pedir reseña | un pedido lo ve la cocina; evita comisiones de apps de delivery ([Aurora](https://www.aurorainbox.com/en/2025/11/22/restaurants-and-cafes-turn-whatsapp-messages-into-reservations-and-recurring-orders-3/)) |
| Tienda y comercio electrónico | consultas de producto (talla, stock), **estado del pedido** (el motivo de consulta más frecuente), devoluciones, carrito abandonado | catálogo de WhatsApp con carrito: hasta 30 productos por mensaje ([Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/catalogs/catalogs-overview/)); el pago nativo en el chat es solo de Singapur |
| Salud y bienestar | citas, recordatorios con confirmar/reprogramar/cancelar, preguntas de logística, **escalar lo clínico** | la política de WhatsApp limita el uso en salud a citas y recordatorios; datos de salud son sensibles |
| Belleza, spa, gimnasio | reservas, reprogramar con un toque, recordatorios 24 h y 1 h, paquetes y membresías | poca evidencia sobre saldos de paquetes ([Chakra](https://chakrahq.com/article/whatsapp-for-spa-salon-use-cases/)) |
| Educación | consulta → calificar (programa, formación previa, plazo) → **reunión o clase de prueba** → seguimiento en 3 contactos → recordatorios de pago (7, 3 y 1 día) | entradas por anuncios y códigos QR ([CueDesk](https://cuedesk.com/blog/from-applicants-to-students-whatsapp-lead-nurturing-guide-for-educators)) |
| Inmobiliaria | calificar (presupuesto, zona, tipo, plazo), mostrar inmuebles que calzan, **agendar visita**, pasar leads calientes | ([Spoki](https://spoki.com/en/blog/whatsapp-for-real-estate-qualify-leads-schedule-viewings)) |
| Automotriz | interés por modelo, presupuesto, plazo, retoma del auto usado, **prueba de manejo**, financiamiento | señales de alta intención: financiamiento, retoma, entrega ([eGrow](https://www.egrow.com/en/car-dealerships-ai-agent)) |
| Servicios del hogar y técnicos | **cotizar con fotos o video**, visita técnica, seguimiento de la cotización, urgencias (inundación, gas) | la respuesta rápida y la cotización por fotos evitan visitas inútiles ([Quiq](https://quiq.com/blog/ai-tools-for-home-service-businesses/)) |
| Servicios profesionales (legal, contable, consultoría) | **ingreso del cliente**, reunión de consulta, lista de documentos | no pedir en el chat datos financieros sensibles; descargo de IA y registro ([hyperleap](https://hyperleap.ai/blog/ai-chatbot-accounting-firms)) |
| Turismo y hotelería | disponibilidad y precio por fechas, **cotización de itinerario**, datos del pasajero, enlace de pago, upsell con contexto | responder fuera de horario y en varios idiomas ([ChatDaddy](https://chatdaddy.tech/blog/whatsapp-for-travel-agencies)) |
| B2B y distribución | **repetir pedidos**, listas de precios, mínimos y plazos, calificar volumen | el cliente mayorista pide casi lo mismo cada vez ([Duotach](https://duotach.com/en/blog/bot-whatsapp-tomar-pedidos)) |
| Tecnología y software | consulta → sondeo → plan o cotización → reunión; planes con precio y características; prueba o demostración | el mismo mapa que servicios profesionales, más suscripción |

## 4. Patrones de diseño que se repiten

1. **Calificar antes de pasar** con las preguntas que predicen la venta en cada rubro (presupuesto, plazo, necesidad,
   autoridad), y clasificar en caliente, tibio y frío para decidir quién actúa. No existe un estándar de umbrales.
   [proveedor] ([SleekFlow](https://sleekflow.io/en-sg/blog/what-is-lead-scoring))
2. **La velocidad de respuesta importa mucho**, pero las cifras (8×, 21×, 391 %) circulan sin fuente primaria.
   [no verificado] ([Kixie](https://www.kixie.com/sales-blog/speed-to-lead-response-time-statistics-that-drive-conversions/)). Leo ya responde en segundos: es una ventaja que hay que medir en lugar de prometer.
3. **Recordatorios: hay evidencia.** Un metaanálisis de 21 ensayos aleatorizados encontró 25 % menos inasistencias con
   notificaciones electrónicas (15 % frente a 21 %) y que **varios recordatorios funcionan mejor que uno**; una
   revisión posterior vio un efecto menor y no significativo para el SMS solo, y algunos ensayos favorecen la voz.
   [estudio] ([PubMed](https://pubmed.ncbi.nlm.nih.gov/27798006/), [revisión 2023](https://jhmhp.amegroups.org/article/view/10215/html)). Conclusión: recordar sí, con confirmar/reprogramar, más de una vez, y medir.
4. **Seguimiento de cotizaciones:** cadencia corta y espaciada (día 2 a 3, 7, 14 y cierre a los 21 a 25), cada contacto
   **aporta algo** (no "¿viste mi propuesta?"), con mensaje de cierre cortés. La cifra "80 % necesita 5 contactos" es
   repetida y sin fuente sólida. [proveedor] ([Agiled](https://agiled.app/hub/agencies/proposal-follow-up-sequence), [Zoomforth](https://www.zoomforth.com/blog/how-to-follow-up-on-a-proposal/))
5. **El traspaso a una persona decide la experiencia.** Debe ser "tibio": resumen, intención, ánimo, datos ya
   recogidos; una petición explícita se atiende de inmediato; informar espera estimada u ofrecer que lo llamen. Medir
   la tasa de "repetir información". [proveedor] ([Twilio](https://www.twilio.com/en-us/blog/insights/ai-to-human-handoff-context), [Haptik](https://www.haptik.ai/blog/warm-transfer-escalation-design-between-ai-human-agents))
6. **Medir la calidad, no solo la contención.** La contención sin precisión engaña: se mide junto con exactitud,
   escalamiento apropiado, resolución confirmada por el cliente y contactos repetidos; muestreo semanal de
   conversaciones y un responsable por señal. [proveedor] ([Parloa](https://www.parloa.com/knowledge-hub/ai-agent-monitoring/), [Voiceflow](https://www.voiceflow.com/blog/what-is-ai-agent-observability.md))
7. **Salir en vivo por etapas:** empezar con un porcentaje pequeño de conversaciones, revisar transcripciones cada
   semana, probar las reglas de escalamiento antes. Prueba previa con al menos 10 a 20 preguntas reales.
   [proveedor] ([open.cx](https://www.open.cx/blog/intercom-ai-auto-responses), [Chatbase](https://fritz.ai/chatbase-review/))
8. **Arranque desde la web o los documentos:** URL, archivos o texto pegado; 12 a 30 minutos hasta salir en vivo; lo que
   decide la calidad es el contenido y el alcance, no la herramienta. [proveedor] (Leo ya lo tiene: web, PDF, texto)
9. **Formularios dentro del chat** (WhatsApp Flows) para datos estructurados: captación, reservas con selector de fecha,
   encuestas, cotizaciones. Se activan desde plantilla o botón; cortos, una sola acción. [proveedor] ([ChatDaddy](https://www.chatdaddy.tech/blog/whatsapp-flows), [wanotifier](https://wanotifier.com/whatsapp-flows-101-guide/))
10. **Varias sedes:** número único con pregunta de sede (o por ubicación), o un número por sede; base de conocimiento y
    calendarios **por sede**. [proveedor] ([YCloud](https://www.ycloud.com/blog/whatsapp-api-multi-location-service-businesses))
11. **Audios:** en América Latina las notas de voz son habituales; transcribirlas y responder es esperado.
    [proveedor, sin datos de uso] ([Wolkvox](https://www.wolkvox.com/?p=57613))
12. **Arquitectura:** Anthropic recomienda lo más simple que funcione: **flujos predefinidos** donde los pasos son
    fijos y bajo tolerancia a error, y **agentes** donde es abierto; el patrón de **enrutamiento** (clasificar y enviar a
    un manejador especializado, y las consultas fáciles a un modelo menor) encaja con varias capacidades.
    [oficial] ([Anthropic](https://www.anthropic.com/research/building-effective-agents))

## 5. Qué hacer mejor que el mercado (los diferenciales de Leo)

1. **Capacidades por rubro, no un bot genérico:** paquetes de rubro (preguntas de calificación, objeciones,
   plantillas de recordatorio, ejemplos y avisos de política) que se activan con una elección.
2. **Cotizar y agendar como capacidades de primera clase,** con su propio cierre y su estado en el CRM.
3. **Traspaso tibio medible** (resumen estructurado, motivo, ánimo, tiempo hasta respuesta humana).
4. **Resultados de negocio, no "resoluciones":** ventas, leads calificados, citas confirmadas, cotizaciones aceptadas,
   costo de mensajes por resultado.
5. **Salida en vivo por etapas con evaluación propia:** conjunto de preguntas de prueba generado del negocio, modo
   piloto (la persona aprueba las primeras respuestas), apagado inmediato.
6. **Realidad de América Latina:** pagos locales (Yape, Plin; Culqi y Niubiz para tarjeta), comprobantes y, después,
   boleta electrónica; notas de voz; origen por anuncio o enlace. (Sobre la API pública de Yape o Plin y la
   emisión de boletas **no se encontró información**: hay que consultarlo en sus documentos oficiales).
7. **Honestidad como producto:** el agente dice lo que no sabe, nunca inventa precios, plazos ni urgencia, y se declara
   asistente virtual si el cliente lo pregunta.

## 6. Lo que no se pudo verificar (para no construir sobre humo)
- El texto oficial de la política de Meta sobre IA y su exigencia de declarar que es un asistente virtual.
- Las cifras de impacto de casi todos los proveedores (conversión, inasistencias, resolución).
- Si Yape y Plin ofrecen API de cobro para comercios, y cómo se enlaza un pago con la boleta electrónica.
- La clasificación exacta de los permisos de Google Calendar (`freebusy`, `events`) para la verificación.
- Los costos exactos de Meta a partir del 1-oct-2026.
