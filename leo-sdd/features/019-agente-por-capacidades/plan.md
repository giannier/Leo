# 019 · Leo como agente general: capacidades, oferta y canales (propuesta, 2026-10-21)

Estado: **propuesta para ratificar**. Reemplaza la primera versión de esta página (2026-10-21), que corregía los
síntomas de un negocio concreto y no el modelo.

## 0. La pregunta
Giannier busca un agente que cualquier negocio configure para **atender, vender, cotizar, agendar visitas y citas
en un calendario**, y que **canalice a todos los clientes que llegan por sus redes sociales**. ¿El proceso actual
(plantillas por tipo de negocio y un asistente de inicio lineal) sirve para eso?

## 1. Veredicto: no, por cuatro razones de diseño (no de implementación)

1. **El cerebro es un embudo de pedidos, no un agente general.** `process/engine.py` calcula la etapa con una sola
   cadena fija (inicio → calificar → identificar → pedido → datos → cobro → verificación → cerrado, más "asesoría" y
   "derivado"). Las tres plantillas "comparten las mismas reglas y solo difieren por opciones y textos" (comentario
   del propio motor). No hay un concepto de cotización, cita, visita, captación de lead ni soporte: lo que no es
   un pedido termina en "derivar a una persona".
2. **El comportamiento lo decide el tipo de negocio, y debería decidirlo la oferta.** Un mismo negocio vende un
   hosting (se compra ya), una web (se cotiza desde un precio de referencia), un sistema (se cotiza), y mañana un
   plan SaaS y consultas con cita. La plataforma ya distingue `precio fijo` o `se cotiza` por producto, pero la
   plantilla elegida para todo el negocio manda sobre eso.
3. **Un solo canal con la identidad atada al teléfono.** `conversations.channel` admite `sandbox` y `whatsapp`; un
   contacto se identifica por teléfono (o por el id de WhatsApp). Las redes sociales (Instagram, Messenger, web) no
   caben sin generalizar el canal y la identidad.
4. **La configuración sigue al tipo de negocio, no a lo que el agente hará.** Por eso pide cosas que no usa, no pide
   las que sí necesita (categorías, periodo del precio, datos para cotizar, horarios para agendar) y arrastra
   ejemplos del primer cliente (cursos y certificaciones).

Lo que **sí está bien y se conserva**: aislamiento por negocio, puertos de proveedores, reglas versionadas (017),
conocimiento con citas, bandeja y CRM, guardas contra inventar, idempotencia y evals. Es una base sólida. Lo que
cambia es qué se construye encima y cómo se configura.

## 2. El modelo correcto: núcleo + capacidades + oferta + canales

### 2.1 Núcleo (siempre activo)
Conversación y memoria, contactos con identidad por canal, conocimiento del negocio, perfil y horarios,
derivación a una persona, CRM y métricas, y las guardas (no inventar, no prometer, datos personales).

### 2.2 Capacidades (el negocio enciende las que necesita)
Cada capacidad trae: **su configuración** (pasos del asistente), **sus datos**, **sus herramientas** para el modelo,
**su objetivo de conversación** (qué es "cerrar" en ella) y **su efecto en el CRM**.

| Capacidad | Cerrar significa | Datos y pasos | Estado hoy |
|---|---|---|---|
| **Atender** (información) | resolver y dejar el siguiente paso | conocimiento, horarios, ubicación, preguntas frecuentes | hecho |
| **Vender** (pedido y cobro) | pedido pagado | catálogo, pagos, entregas | hecho (plantillas) |
| **Cotizar** | cotización pedida con datos completos, entregada a un asesor, seguimiento hasta aceptada | qué datos se piden por servicio, precio referencial, a quién se deriva | parcial: solo "derivar con resumen" |
| **Agendar** (citas y visitas) | cita confirmada en el calendario | servicios agendables, duración, recursos, horarios, calendario | especificada (014), sin construir |
| **Captar leads** | contacto calificado y siguiente paso acordado | preguntas de calificación, destino del lead | parcial (CRM) |
| **Seguimiento y campañas** | respuesta o recompra | plantillas, reglas | hecho (011) |

El agente de cada negocio es la combinación: una cafetería = Atender + Vender (+ Agendar reservas); una agencia =
Atender + Vender (hosting) + Cotizar (webs, sistemas) + Agendar (reunión); una inmobiliaria = Atender + Captar +
Agendar (visita).

### 2.3 Oferta: el tipo de venta lo lleva cada ítem
Un ítem del catálogo tiene tipo (producto, servicio, plan, curso…) y **cómo se vende**: pedido directo, cotización
(con o sin precio referencial "desde S/ X"), cita, o solo informativo. Más: periodo del precio (único, mes, año),
categoría, y los datos que hay que pedir para cotizar o agendar. El agente se comporta según el ítem del que se
habla; el negocio no elige una "plantilla" para todo.

Las tres plantillas actuales pasan a ser **presets de arranque** ("Tienda", "Servicios", "Educación"), no el
concepto central.

### 2.4 Canales
Un puerto de canal (WhatsApp ya existe) y una tabla de identidades por contacto (`canal`, `id externo`). Orden
realista: (1) **WhatsApp**, con atribución de origen cuando el cliente llega desde un anuncio o enlace de una
red social (la forma más común hoy, y ya hay `entrada_anuncio`); (2) **chat web**; (3) **Instagram y Messenger** por
la API de Meta (requiere revisión de la app, semanas de plazo externo). TikTok y otras redes sin API de mensajes
entran por enlace a WhatsApp. Una sola bandeja y un solo CRM para todos.

### 2.5 El motor
El cerebro deja de ser una cadena fija y pasa a ser **un objetivo por conversación**, que se deduce de la oferta
de la que se habla (vender, cotizar, agendar, informar). Las etapas se vuelven un subflujo de cada capacidad: la de
pedidos (la actual) se mantiene tal cual, y cotizar y agendar tienen las suyas. El **bloque de venta** que ya se
escribió (sondear, recomendar, objeciones, siguiente paso) se generaliza: cada capacidad trae su cierre.

## 3. La configuración que sale de ahí
Un solo recorrido, compuesto:

1. **Tu negocio**: nombre, rubro, web (una vez, con "leer mi web"), moneda, zona.
2. **Qué hará tu agente**: Atender, Vender, Cotizar, Agendar, Captar leads (se pueden marcar varias) y **por dónde te
   escriben tus clientes** (WhatsApp, y los canales que haya).
3. **Tu oferta**: catálogo con tipo y cómo se vende cada ítem, categorías, descripciones.
4. **Pasos de cada capacidad encendida**: cobros (Vender), datos a pedir y a quién derivar (Cotizar), servicios,
   horarios y calendario (Agendar).
5. **Tu agente**: nombre, tono, saludo, objetivo.
6. **Pruébalo y enciéndelo**: simulador y una lista de progreso **calculada** de lo que las capacidades piden.

Cada dato se pide una vez y su dueño es único (la web, la descripción, el contacto, la oferta). El Asistente es el
mismo recorrido como menú. Los ejemplos salen del rubro elegido; sin rubro, neutros.

## 4. Hoja de ruta (cada fase deja algo usable)

| Fase | Contenido | Por qué en este orden |
|---|---|---|
| **A · Oferta y capacidades** | tipo y "cómo se vende" por ítem; periodo del precio; categorías con pantalla; capacidades como interruptores; web una sola vez; ejemplos neutros; una sola lista de progreso; presets en lugar de plantillas | es lo que desbloquea todo lo demás y cambia poco el motor |
| **B · Cotizar de verdad** | datos a pedir por servicio, resumen estructurado, estado de la cotización en el CRM, plazo de respuesta y seguimiento | caso inmediato; reutiliza derivación y CRM |
| **C · Agendar** | la 014: servicios agendables, disponibilidad con bloqueo anti doble reserva, recordatorios con plantilla, calendario propio y Google Calendar | la capacidad más pedida; necesita OAuth y plantillas aprobadas |
| **D · Canales** | identidades por canal, chat web, atribución de origen, luego Instagram y Messenger | depende de aprobaciones externas, se empieza a tramitar antes |
| **E · Aprender** | resultados por capacidad y rubro, y mejora de las instrucciones con revisión humana (017, fases 3 y 4) | necesita datos reales de varios negocios |

Riesgos honestos: Google Calendar con acceso a eventos exige verificar la aplicación; Instagram y Messenger exigen
revisión de Meta; el recordatorio de citas fuera de las 24 horas exige plantilla aprobada de WhatsApp; el cambio
del motor (2.5) es lo más grande y se hace por capas, con los evals actuales como red de seguridad.

## 5. Decisiones que se piden
1. ¿Se ratifica el modelo (núcleo + capacidades + oferta + canales) como dirección de Leo?
2. Orden de las fases: ¿A, B, C, D como arriba?
3. Agendar: ¿agenda propia primero y Google Calendar después, o Google desde el inicio?
4. Canales: ¿se empieza ya el trámite de Instagram y Messenger con Meta mientras se construye lo demás?
