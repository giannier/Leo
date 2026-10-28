# Spec + Plan — Proceso universal de venta y plantillas de rubro editables

**Feature ID:** 020 · **Estado:** Diseño, por aprobar (2026-10-28) · **Depende de:** 004, 014, 016, 017, 019
**Origen:** pedido de Giannier del 2026-10-28 (sección 1). Decisiones abiertas en [clarifications.md](clarifications.md).
**Investigación que lo respalda:** `019-agente-por-capacidades/investigacion.md` §3 (qué necesita cada rubro), §4 (patrones)
y §7 (cómo lo hacen Ventor, Intercom, Decagon, Salesforce y Sierra); `019/plan.md` §2.4 y §2.7, que ya habían decidido que
«cada ítem lleva su forma de venderse» y que «las tres plantillas actuales pasan a ser presets».

## Resumen

Leo tiene hoy tres plantillas de proceso en el código (productos, servicios, certificaciones). Una de ellas,
certificaciones, es el proceso de un negocio concreto (Capacita) metido en la plataforma. Esta feature las reemplaza por
**un solo proceso universal, escrito desde cero**, que sirve a cualquier negocio que vende productos, servicios o ambos.
Lo que cambia de un negocio a otro deja de estar en el código y pasa a ser configuración:

- **cómo se cierra cada producto o servicio** (pedido, cotización, cita o solo informar) lo dice el propio ítem del catálogo;
- **los paquetes** (elige N entre varios) son de cualquier negocio: herramientas, servicios, certificaciones;
- **la plantilla del rubro** es un punto de partida editable: el negocio la aplica, la cambia, la mejora o la quita;
- **las reglas propias** del negocio siguen siendo sus situaciones.

No se combinan las tres plantillas: se escribe un proceso nuevo y se **reutilizan las piezas que ya están probadas**
(herramientas, motor de pedidos, cotización, agenda, derivación, verificación de pagos). Se construye **al lado** de lo
actual, con un interruptor por negocio y vuelta atrás; las plantillas viejas se retiran solo cuando ningún negocio las use.

## 1. Lo que pidió Giannier (requisitos de origen)

1. Solo se venden **productos** (físicos o digitales) y **servicios**, o ambos a la vez.
2. El proceso es el mismo para todos: el cliente llega, consulta, se le informa, se entiende qué necesita, se le ofrece y se
   cierra: una venta, una cotización, una reunión o una visita. Cómo cierra cada negocio depende de su rubro y su forma de
   trabajar.
3. Cada negocio tiene sus reglas, pero **no se personaliza el código por negocio**. El código cambia para mejorar a todos o
   para sumar funciones que varios negocios piden.
4. Los **paquetes** los tiene cualquier negocio: un paquete de herramientas donde se elige entre martillo, taladro y
   escuadras; uno de servicios con auditoría, mantenimiento y soporte; uno de certificaciones con N temas.
5. Las **plantillas por rubro** ayudan a los negocios desordenados o que no saben cómo vender: son una opción inicial que
   pueden cambiar, mejorar o quitar.
6. Certificaciones es parte del rubro **educación**, no la definición general.
7. Construir el proceso nuevo **desde cero**, sin combinar las tres plantillas, sin romper lo que funciona y sin cabos
   sueltos.

## 2. Lo que hay hoy (inventario)

| Pieza | Qué hace hoy | Dónde | Qué pasa en 020 |
|---|---|---|---|
| Motor de etapas | Calcula la etapa desde los hechos de la conversación; ya es el mismo para las tres plantillas | `process/engine.py` | Se reutiliza; aprende el cierre por ítem (§5) |
| Las 3 plantillas | Texto de cada etapa, herramientas del primer mensaje, valores por defecto (datos del cliente, pago, caminos) y un límite de 3 servicios por pedido | `process/templates/*.py` | Se congelan; conviven hasta migrar; luego se retiran |
| Reglas del proceso en la base | Versiones `proceso` v1 a v5, reglas por etapa que el negocio puede apagar | `catalog_versions`, migraciones 0026…0068 | Nueva clave `universal` v1; las tres viejas no cambian más |
| Herramientas por etapa | Qué herramienta ve el modelo en cada etapa | `templates/base.py` | Se reutilizan |
| Motor de pedidos | Carrito, paquetes con elección, totales, promociones, verificación | `orders/service.py` | Se reutiliza; se quita el límite de 3 líneas (§5.3) |
| Cotización | `anotar_cotizacion`, datos a pedir por producto, pase a una persona | `agent/tools/quote.py` | Se reutiliza |
| Agenda | horarios, reservas, recordatorios | `scheduling/` | Se reutiliza |
| Derivación | traspaso tibio con resumen | `agent/tools/handover.py` | Se reutiliza |
| Postventa | estado del pedido, devoluciones, documentos | herramientas de postventa | Se reutiliza |
| Capacidades → plantilla | `derive_template` elige productos, servicios o certificaciones | `process/capabilities.py`, `services/capabilities.py` | Desaparece la elección de plantilla |
| Rubros | 13 rubros con plantilla sugerida, preguntas para calificar, datos a pedir sugeridos, capacidades sugeridas; versiones con ~4 reglas, preguntas frecuentes, objeciones y situaciones sugeridas; **las reglas solo se apagan** | `models/catalog.py`, `process/layers.py` | Pasan a ser plantillas de arranque editables (§6) |
| Situaciones | Reglas propias del negocio en tarjetas | `models/situation.py` | Se reutilizan; reciben lo que trae la plantilla del rubro |
| Caminos | Pregunta inicial con caminos (venta o asesoría); hoy solo los usa certificaciones | `schemas/process_options.py` | Opción general de cualquier negocio |
| Panel | Asistente › Proceso (plantilla y reglas); el selector de capacidades muestra la plantilla | `frontend/app/(panel)/asistente/proceso`, `components/assistant/capability-picker.tsx` | Cambia (§8) |
| Administración | Catálogo de plantillas y rubros; evals al publicar, por plantilla | `admin/catalogo`, `services/catalog_evals.py`, `api/v1/platform_catalog.py` | Se adapta al proceso universal y a las plantillas de rubro |
| Aprendizaje | El redactor de sugerencias lee las reglas de la plantilla | `learning/drafter.py` | Se adapta |
| Versión por conversación | Una conversación sigue con la versión con la que empezó | `conversation_state.template_version` | Se reutiliza para la convivencia |
| Evals | Flujos por plantilla y conversaciones reales | `backend/evals` | Batería universal por tipo de negocio (§9) |
| Producción | 2 negocios: uno en productos (rubro «otros», activo) y Nayu en servicios (b2b, inactivo); 13 productos, 0 paquetes, 2 situaciones | — | Migración pequeña |

**Los problemas del diseño actual:**
1. Un proceso de negocio (Capacita) está en el código de la plataforma; cualquier mejora suya obliga a tocar la plataforma.
2. Hay que mantener y evaluar tres textos que dicen casi lo mismo.
3. La forma de cerrar se elige por negocio, no por ítem: un negocio que vende productos y servicios no tiene dónde caber
   (servicios limita el pedido a 3 líneas y no saluda con el pedido abierto; productos no deriva lo que es a medida).
4. Los paquetes parecen cosa de certificaciones, aunque el motor de pedidos ya los soporta para cualquiera.
5. Las plantillas de rubro son delgadas y no se pueden editar, solo apagar.

## 3. Principios

1. **Un solo motor, de la plataforma**, igual para todos los negocios (patrón de Intercom, Decagon, Salesforce y Sierra).
2. **Solo hay productos y servicios.** Lo que cambia es cómo se cierra cada uno, y eso lo dice el ítem.
3. **Lo propio de un negocio es configuración**, nunca código.
4. **La plantilla del rubro es un punto de partida editable**, y las mejoras de la plataforma llegan como novedades que el
   negocio acepta o no.
5. **Las transacciones son pasos fijos con validación en código** (pedido, cotización, cita: no se inventa un total, no se
   pasa una cotización incompleta, no se reserva un horario ocupado); la conversación la lleva el modelo. Es lo que
   recomienda Anthropic para agentes: flujos fijos donde los pasos son fijos, agente donde es abierto.
6. **Nada sale sin pruebas con el modelo real** (constitución: no se cambian prompts en producción sin evals).
7. **Se construye al lado de lo actual**, con interruptor por negocio y vuelta atrás.

## 4. La oferta: productos y servicios

### 4.1 Cada ítem del catálogo

| Campo | Para qué | Hoy |
|---|---|---|
| **Tipo**: producto o servicio | Lo que se vende | Nuevo (hoy se deduce de la plantilla) |
| Formato: físico o digital (productos) | Si pide envío o se entrega por archivo o enlace | Existe (`es_digital`) |
| Precio y periodo (único, mes, año) | Venta única o suscripción | Existe |
| **Cómo se cierra**: pedido, cotización, cita o solo informar | Lo que hace el agente cuando el cliente decide | Parcial: `modo_venta` (precio fijo o cotización) y `agendable` |
| Stock (productos físicos) | No ofrecer lo agotado | Existe |
| Datos a pedir | Lo que se pregunta antes de cotizar o agendar | Existe (`datos_a_pedir`) |
| Duración y recursos (citas) | Calcular horarios | Existe |
| Paquete (§4.2) | Elige N entre varios | Existe en parte |
| Fotos, archivos, categoría, etiquetas, destacado | Mostrar y recomendar | Existe |

### 4.2 Cómo se cierra cada ítem

| Cómo se cierra | Producto (ejemplos) | Servicio (ejemplos) | Qué hace el agente | Herramientas |
|---|---|---|---|---|
| **Pedido** (precio fijo) | ropa, clavos, agua por menor, curso grabado | hosting, mantenimiento mensual, una certificación | Lo agrega cuando el cliente lo quiere → datos → cobro → verificación | `gestionar_pedido`, `registrar_datos_cliente`, `enviar_metodos_pago` |
| **Cotización** | agua por mayor, muebles a medida | web a medida, auditoría, itinerario de viaje | Junta los datos que el negocio pide → pasa a una persona con el resumen | `anotar_cotizacion` |
| **Cita, visita o reunión** | prueba de manejo | consulta médica, visita a un inmueble, clase de prueba | Muestra horarios → reserva → recuerda | `consultar_horarios`, `agendar_cita`, `gestionar_cita` |
| **Solo informar** | — | un programa que arma un asesor | Responde, entiende y pasa a una persona | `derivar_a_humano` |

Un negocio mezcla los cuatro. **El agente se comporta según el ítem del que se habla**, no según una plantilla.

### 4.3 Paquetes (de cualquier negocio)

| Tipo | Ejemplo | Cómo se modela |
|---|---|---|
| Combo fijo | «Kit de pintura: rodillo, brocha y bandeja» | Un ítem normal con su descripción (ya funciona) |
| Paquete con elección, entre ítems del negocio | «Paquete de 3 herramientas: elige entre martillo, taladro, escuadras…» | `bundle_size` = 3; se elige entre ítems del negocio (hoy) **o solo de una categoría** (nuevo, decisión D2) |
| Paquete con elección, también libre | «Paquete de 5 certificaciones: cualquier tema» | `bundle_permite_libres` (hoy) |
| Paquete de servicios | «Plan empresa: auditoría + mantenimiento + soporte» | Combo fijo, o con elección si el cliente escoge |

El motor de pedidos ya cuenta cuántos faltan, valida lo elegido y no deja avanzar a los datos hasta completar el paquete.

## 5. El proceso universal (escrito desde cero)

### 5.1 Los momentos de una conversación

| Momento | Cuándo | Qué hace el agente | Etapa técnica (se reutiliza) |
|---|---|---|---|
| 1. Bienvenida | Primer mensaje | Saluda, se presenta y, si el cliente ya preguntó algo, lo responde en ese mismo mensaje | INICIO |
| 2. Orientar (opcional) | El negocio definió caminos y el cliente aún no tiene uno | Pregunta el camino o lo deduce de lo que dijo; nunca condiciona la respuesta | CALIFICAR |
| 3. Anuncio | Llegó por un anuncio reciente | Va directo a lo anunciado | ANUNCIO |
| 4. Entender y ofrecer | Por defecto | Entiende qué necesita, informa, recomienda una opción, maneja objeciones; cuando decide, aplica el cierre del ítem | IDENTIFICAR |
| 5a. Cerrar con pedido | Hay un pedido en borrador | Completa el pedido (y el paquete) → pide los datos → cobra | PEDIDO, DATOS, COBRO |
| 5b. Cerrar con cotización | El ítem es a cotizar | Junta los datos → pasa a una persona con el resumen | IDENTIFICAR con el bloque de cotización |
| 5c. Cerrar con cita | El ítem es agendable | Horarios → reserva | IDENTIFICAR con el bloque de agenda |
| 6. Verificar y cerrado | Pago por revisar, rechazado o confirmado | Lo que ya existe | VERIFICACION, CERRADO |
| 7. Asesoría | Camino de asesoría | Datos mínimos → persona | ASESORIA |
| 8. Postventa | Pregunta por un pedido hecho | Estado, devoluciones, documentos | En cualquier momento |
| 9. Pausado | Lo atiende una persona | No responde | DERIVADO |

**Qué se reutiliza y qué es nuevo.** Se mantienen las etapas técnicas y su columna en el embudo: el CRM, los seguimientos,
los reportes y las métricas siguen funcionando sin cambios. Es nuevo y se escribe desde cero, sin copiar las plantillas:

- el **texto de cada etapa**, uno solo, genérico, ordenado por cómo se cierra;
- el bloque **«Cómo se cierra»** del prompt, que se arma con los tipos de cierre que el negocio usa de verdad: una tienda sin
  cotizaciones nunca lee el bloque de cotización (menos tokens, menos instrucciones que choquen);
- la **decisión del cierre por ítem** (el «enrutador» del plan 019 §2.7): con el producto o servicio del que se habla, el
  código sabe qué cierre aplica y se lo dice al modelo, en vez de que el modelo lo adivine.

### 5.2 Qué decide el código y qué decide el modelo

| Lo decide el código (determinista) | Lo decide el modelo (conversación) |
|---|---|
| La etapa, desde los hechos | Entender qué necesita el cliente |
| Qué cierre aplica al ítem | Qué recomendar y cómo explicarlo |
| Totales, paquetes completos, stock | Cómo manejar una objeción |
| Si una cotización está completa | Con qué palabras pedir un dato |
| Si un horario está libre | Cuándo el cliente ya decidió |
| Un menú por turno, sin mensajes duplicados, avisos del sistema | El tono, dentro del estilo del negocio |
| Pasar a una persona si la pide | — |

### 5.3 Lo que hoy depende de la plantilla y pasa a ser del negocio

| Hoy, en la plantilla | En 020 |
|---|---|
| Datos del cliente antes de cobrar (productos: nombre y teléfono; servicios: nombre, empresa y correo; certificaciones: DNI y más) | Del negocio; el valor inicial lo sugiere la plantilla del rubro |
| Pago previo o contra entrega (servicios y certificaciones solo previo) | Del negocio, para cualquier rubro |
| Límite de 3 servicios por pedido | Se quita (decisión D8) |
| «Primer mensaje: solo saludar y pedir el nombre» (certificaciones) | Situación sugerida de la plantilla de educación, editable |
| Caminos «certificar o estudiar» | Caminos sugeridos de la plantilla de educación |
| «Paso 1, 2, 3» y el precio ancla | Situaciones sugeridas de la plantilla de educación |

## 6. Plantillas de rubro: punto de partida editable

### 6.1 Qué trae una plantilla

| Pieza | Ejemplo (educación) | Hoy |
|---|---|---|
| Capacidades sugeridas | vender, agendar (clase de prueba) | Existe |
| Caminos sugeridos | «¿Quieres certificar tu experiencia o estudiar un programa?» | Solo en la plantilla certificaciones |
| Datos del cliente sugeridos | DNI, nombres, apellidos, correo, teléfono | En la plantilla de proceso |
| Preguntas para calificar | programa, experiencia previa, plazo | Existe |
| Datos a pedir sugeridos (cotizar o agendar) | — | Existe |
| Situaciones sugeridas | «Antes del precio del paquete, menciona el precio por certificado»; «Se certifica cualquier tema» | Existe |
| Preguntas frecuentes y objeciones | «¿La certificación tiene validez?» | Existe |
| Ítems de ejemplo (opcional) | «Paquete de 5 certificaciones (elige 5 temas)» | Nuevo |
| Avisos de política | Lo que no se puede vender por WhatsApp | Existe |

### 6.2 Cómo se usa

1. Al elegir el rubro (en la configuración inicial o después), Leo muestra lo que trae la plantilla y el dueño la aplica.
2. Lo aplicado **se copia al negocio**: las situaciones, los caminos, los datos y las preguntas pasan a ser suyos (con su
   origen anotado: «plantilla educación v1»). Los edita, los apaga o los borra.
3. Cuando Zentek360 mejora la plantilla, la mejora llega como **novedad**: el dueño la acepta o la ignora, cambio por cambio.
   El mecanismo de novedades ya existe (`tenant_catalog_seen`); se extiende a las copias.
4. Las preguntas frecuentes y las objeciones del rubro se siguen consultando con `consultar_conocimiento`; el negocio puede
   apagar una y sumar las suyas.
5. **«Otros»** es la plantilla genérica, con ejemplos neutros, para quien no encaja en ningún rubro.

### 6.3 Las reglas del rubro de hoy

Hoy cada rubro trae ~4 reglas que entran al prompt y solo se pueden apagar. En 020 pasan a ser **situaciones sugeridas**
(las generales, marcadas «siempre»), editables como cualquier situación (decisión D3).

### 6.4 La plantilla de educación (lo que hoy es certificaciones)

- Caminos: «Solo certificar» (venta) y «Programa completo» (asesoría).
- Datos del cliente: DNI, nombres, apellidos, correo, teléfono.
- Situaciones: pedir el nombre en el primer mensaje (opcional); el precio por certificado antes del paquete; «se certifica
  cualquier tema»; si no sabe qué tema, mostrar categorías; programa completo: enviar la malla si existe y preguntar horario.
- Ítems de ejemplo: certificación individual (pedido), paquete de N certificaciones con elección libre (pedido), programa
  (solo informar → asesor).
- Preguntas frecuentes y objeciones del rubro.

Capacita conserva su forma de vender, pero como **su configuración**, no como código de la plataforma.

## 7. Convivencia y migración (no romper lo que funciona)

- Se crea el proceso **`universal` v1** (código y reglas en la base). Las tres plantillas viejas se **congelan**: no se
  tocan más y siguen funcionando para los negocios que aún estén en ellas.
- **Interruptor por negocio** (`agent_configs.process_template_key = "universal"`), con vuelta atrás.
- Las **conversaciones abiertas** pasan al proceso nuevo en su siguiente mensaje **sin perder nada**: las etapas, el pedido, la
  cotización y los datos del cliente son los mismos en los dos procesos (el motor de etapas y los hechos no cambian). Se
  revisó el código: cada mensaje ya usa el proceso actual del negocio (`conversation_state.template_version` solo registra
  con cuál empezó), así que mantener una conversación en el proceso viejo agregaría complejidad sin beneficio. Por eso la
  migración se hace en un horario tranquilo y con el visto bueno de Giannier.
- **Orden de migración:** negocios de prueba → Nayu (inactivo) → el negocio activo de producción. Cada paso, después de que
  pasen las pruebas y con el visto bueno de Giannier.
- Al migrar un negocio, se le **aplica la plantilla de su rubro** y se convierte lo que hoy viene de su plantilla vieja
  (datos del cliente, pago, caminos) a su configuración, para que se comporte igual o mejor.
- Las plantillas viejas se **retiran** cuando ningún negocio las use, en una entrega aparte.
- **Vuelta atrás:** cambiar el interruptor. Las copias de la plantilla de rubro no se borran al volver.

## 8. Panel y administración

- **Asistente › Proceso:** desaparece la elección de plantilla. Muestra «cómo vende tu agente» (resumen según sus
  capacidades y su catálogo) y la plantilla de rubro aplicada, con sus novedades.
- **Producto o servicio:** tipo, cómo se cierra (pedido, cotización, cita o solo informar) y, si es paquete, cuántos se
  eligen y de dónde. Los valores iniciales salen del rubro.
- **Configuración inicial:** rubro → vista previa de la plantilla → aplicar.
- **Administración de la plataforma:** editar y publicar el proceso universal y las plantillas de rubro, con evals antes
  de publicar.

## 9. Pruebas

- **Automáticas:** la suite completa en verde, incluida la de aislamiento entre negocios.
- **Batería universal con el modelo real**, por tipo de negocio, con conversaciones nuevas en cada fase (nunca usadas para
  ajustar):

  | Negocio de prueba | Qué prueba |
  |---|---|
  | Tienda física con envío | Pedido, datos, cobro, promociones |
  | Tienda digital | Pedido sin envío, entrega por enlace |
  | Ferretería con paquete a elegir | Paquete con elección dentro de una categoría |
  | Distribuidora a cotizar (catálogo real de Nayu) | Cotización con preguntas propias, pase a una persona |
  | Agencia de servicios | Precio fijo y a cotizar en el mismo negocio |
  | Clínica | Citas |
  | Inmobiliaria | Calificar y agendar visitas |
  | Educación | Caminos, certificaciones y paquete con elección libre |
  | Negocio mixto | Productos y servicios a la vez |

- **Umbrales:** flujos ≥ 90 %, cotizaciones ≥ 85 %, calidad de venta ≥ 75 %, cero mensajes duplicados y lectura del juez.
  **Dos corridas** por umbral: la misma prueba varía ±2 casos de una vez a otra.
- **Comparación:** antes de migrar un negocio, el proceso universal debe igualar o superar a su plantilla actual en los
  mismos casos.
- **Costo estimado** (decisión D5): ~US$0,6–0,9 por pasada completa con la caché; dos pasadas por umbral. **No aprobado
  por ahora:** hasta que Giannier apruebe el presupuesto se prueba con la suite automática y con conversaciones simuladas con
  Claude como agente; el paso de un negocio de producción al proceso universal espera esa prueba (constitución).

## 10. Fases

| Fase | Qué | Se cierra cuando |
|---|---|---|
| F0 | Este diseño y las decisiones de Giannier | Giannier lo aprueba |
| F1 | Oferta (backend y API): tipo producto o servicio, cómo se cierra cada ítem, paquetes por categoría; migración de datos (se deduce de `modo_venta` y `agendable`) | Suite en verde |
| F2 | Proceso universal v1 (texto desde cero, bloque «Cómo se cierra», cierre por ítem), detrás del interruptor | Suite en verde y primera batería con el modelo real |
| F3 | Plantillas de rubro editables: aplicar, copias propias, novedades; contenido de educación y de los rubros de las 20 empresas, luego el resto | Suite en verde |
| F4 | Panel y administración (incluye el formulario del producto o servicio) | Capturas a 1 900, 1 280 y 390 px |
| F5 | Batería universal (dos corridas) → migración por negocio → retiro de las plantillas viejas | Umbrales cumplidos y «despliega» de Giannier |

Cada fase termina con la suite en verde. Nada se despliega sin el «despliega» de Giannier.

## 11. Riesgos

| Riesgo | Cómo se controla |
|---|---|
| Un solo proceso para todo junta instrucciones que chocan | Solo entran los bloques de los cierres que el negocio usa; prueba del presupuesto de 6 000 tokens; evals |
| El modelo cambia de comportamiento con el texto nuevo | Batería por fase, dos corridas, comparación con la plantilla actual antes de migrar |
| Las copias de la plantilla se alejan de las mejoras de la plataforma | Novedades por cambio, que el dueño acepta o no |
| El panel se vuelve complejo | Valores iniciales del rubro; lo avanzado, plegado |
| Volver a personalizar por negocio | Regla de trabajo: lo de un negocio es configuración; un pedido que varios repiten se evalúa como función |

## 12. Fuera de alcance

- **Procedimientos con pasos** y simulaciones que pasan o fallan, como los de Intercom y Decagon: es la evolución natural
  de las situaciones (feature futura).
- Canales nuevos.
- Un mercado de plantillas compartidas entre negocios (como Ventor Share).
