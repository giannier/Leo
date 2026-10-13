# Mapeo del brief contra el repo — 017 Configuración en capas y aprendizaje

**Estado:** borrador para Giannier. Pasos 1 y 2 de las instrucciones del brief. La spec, el plan y las
tasks (paso 4) y el glosario del `CLAUDE.md` (paso 5) se escriben **después** de tu confirmación (paso 3).
Medido sobre `feat/004-motor-de-ventas` al 2026-10-13.

Leyenda: ✅ existe · 🟡 existe a medias · 🆕 nuevo.

---

## 1. Los términos del brief y su nombre en el repo

| Término del brief | Qué hay en el repo | Estado |
|---|---|---|
| **Plantilla del rubro** | `Template` en `backend/app/process/templates/` (`productos`, `certificaciones`, `servicios`): etapas, condiciones, herramientas por etapa, embudo, textos de cada etapa, seguimientos y mensajes del sistema. Vive en **código** (C-05). El "rubro" del negocio es otra cosa: `business_profiles.industry` (`restaurante, instituto, retail, servicios, salud, otros`) | 🟡 Falta: preguntas frecuentes y objeciones propias de la plantilla, y reglas identificables que se puedan apagar una por una |
| **Proceso de venta típico** | Las 11 etapas del motor (`process/engine.py`) | ✅ |
| **Reglas por defecto** | Texto de cada etapa (`base.py`, `plantillas.md §5`). Son párrafos, no reglas sueltas con un id | 🟡 Hay que partirlas en reglas con id |
| **Apagar una regla de la plantilla** | No existe. Hoy el tenant puede **editar** el texto de cada etapa (`process_stage_overrides`, migración 0016; TASK-435 y 440 lo exponen) | 🆕, y **contradice** lo planeado (ver C1) |
| **Perfil del negocio** | Repartido en `business_profiles` (nombre, rubro, descripción, horario, moneda, zona horaria), `agent_configs` (nombre del agente, `tone`, `objective`, `ritmo`, `detalle`, seguimiento, mensajes del sistema, `restrictions`), `business_info` (entregas, locales, tipos de cliente, devoluciones, contacto), `payment_methods_business`, `promotions` | 🟡 Faltan: tú o usted, emojis, saludo, política de descuentos y cuentas de cobro (titular). `restrictions` es texto libre (ver C2) |
| **Situación** (tarjeta: cuándo aplica, qué hace, opcionales) | No existe. Lo más parecido es `agent_configs.restrictions`, que entra al prompt como "Reglas adicionales del dueño" (un solo cuadro de texto, el problema de Ventor) | 🆕 Tabla de situaciones con activar/desactivar |
| **Archivos de una situación** | Biblioteca multimedia (`media_assets`, migración 0017) y `enviar_recurso` | ✅ Se reutiliza |
| **Derivar a un humano** (opcional de la situación) | `derivar_a_humano` declarada, **stub** hasta TASK-443 | 🟡 |
| **Probar en el simulador** | Simulador con modo depuración (TASK-422) | 🟡 Falta probar una situación en borrador sin activarla para los clientes |
| **El agente solo lee lo que aplica** | El prompt por etapa ya filtra herramientas y textos por etapa, pero no hay selección de reglas por mensaje | 🆕 Selector de situaciones por mensaje |
| **Perfil de estilo** | `tone` (`profesional, cercano, juvenil`), `ritmo`, `detalle` | 🟡 No sale de chats reales |
| **Cuestionario de estilo** | Paso 2 del wizard de la 001 (personalidad: nombre, tono, objetivo, restricciones libres) | 🟡 Se amplía |
| **Importar chats de WhatsApp con audios** | No existe. No hay transcripción de audio (la 004 vieja la tenía como HU-05 y se archivó). Subida de archivos a MinIO sí | 🆕 |
| **Anonimizar** | `agent/privacy.py` enmascara por **nombre de campo** en trazas. No anonimiza texto libre | 🆕 Anonimizador de texto |
| **Conversaciones ejemplo** | No existe | 🆕 |
| **Argumentos y objeciones** | No existe (ni por tenant ni por plantilla) | 🆕 |
| **Sugerencia** | No existe | 🆕 Bandeja de sugerencias |
| **Revisor** | Roles `owner`, `tenant_admin`, `operator` (`TENANT_ROLES`). No hay permisos sueltos | 🆕 Permiso asignable |
| **Toma de control humana** | Feature 007 (spec en borrador, **sin construir**). `derivar_a_humano` es stub | 🆕 Depende de la 007 |
| **Venta confirmada** | `orders.estado = confirmado` con `confirmed_at` (TASK-425 y 430) | ✅ |
| **Venta perdida** | No existe como estado. Lo más cercano: pedido `descartado` (manual) y el job de 7 días sin actividad (TASK-444, sin construir) | 🆕 Definirla |
| **Pregunta sin respuesta** | `consultar_conocimiento` y `agent_traces.rag_sources`; no se marca cuando no encuentra nada | 🟡 |
| **Ejemplo ganador** | No existe | 🆕 |
| **Historial y deshacer** | `audit_logs` registra acciones, pero no guarda versiones ni permite volver atrás | 🆕 |
| **El rubro aprende** (subir una situación a la plantilla) | Panel de plataforma (TASK-024). Las plantillas están en código: subir una regla hoy es un cambio de código y un deploy | 🆕 |
| **Redactor asistido** | No existe | 🆕 |

---

## 2. Las tasks 434–442 contra las capas (lo que pediste antes de la Fase C)

| Task | Capa | Ajuste propuesto |
|---|---|---|
| 434 API de información del negocio, FAQ y promociones | Perfil | Se mantiene. Suma: cuentas de cobro (ya anotado), política de descuentos y los campos nuevos de tono |
| 435 API de comportamiento y proceso | Perfil y plantilla | **Se parte.** Comportamiento y opciones de la plantilla se quedan. "Editar y restaurar etapas" **se reemplaza** por encender y apagar reglas de la plantilla. Se agrega una task nueva: API de situaciones |
| 436 API de multimedia | Archivos de las situaciones | Sin cambios |
| 437 Overview y lista de activación | Todas | Suma "creaste tu primera situación" y "probaste una situación en el simulador" |
| 438 Front: Información del negocio | Perfil | Sin cambios de fondo; suma los campos nuevos |
| 439 Front: Comportamiento | Perfil (estilo) | Suma tú o usted, emojis y saludo |
| 440 Front: Proceso de venta | Plantilla | **Cambia.** Sale el editor de instrucciones por etapa; entra la lista de reglas de la plantilla con interruptor. Se agrega una task nueva: pantalla de **Situaciones** (tarjetas, activar, probar en simulador) |
| 441 Front: Multimedia, checklist y depuración | — | Sin cambios |
| 442 Milestone C | Todas | El guion suma: crear una situación, probarla, activarla, y apagar una regla de la plantilla |

**Tasks nuevas que necesita la Fase 1 del brief** (si se hace dentro de la Fase C de la 004):
- Catálogo de reglas de la plantilla con id (partir los textos de etapa en reglas) y su interruptor por negocio.
- Modelo y API de situaciones (incluye versión, estado activa o borrador y el historial).
- Selector de situaciones por mensaje en el pipeline (solo las que aplican) y su lugar en el prompt.
- Probar una situación en borrador en el simulador.
- Migrar el texto libre de `restrictions` a situaciones.
- Evals: una situación activa se cumple, una apagada no, y una regla de plantilla apagada deja de aplicarse.

---

## 3. Contradicciones con lo implementado o con specs anteriores

Separadas como pide la regla del `CLAUDE.md`.

### Bloqueantes (necesito tu decisión antes de la spec)

**C1. Editar las etapas frente a "apagar, no editar".** La 004 decidió (C-05) que el negocio **edita** el
texto de cada etapa y puede restaurarlo; la migración 0016 ya creó `process_stage_overrides` y TASK-435 y
440 lo iban a exponer. El brief dice que el negocio **no edita** la plantilla: la apaga regla por regla y,
si quiere otra cosa, crea una situación. **Propuesta:** el negocio no edita etapas; la tabla 0016 queda
solo para el operador de la plataforma (Zentek360) o se borra en una migración. Hay que partir los textos
de etapa en reglas con id para poder apagarlas, y eso cambia el prompt (corren los evals).

**C2. Las reglas del dueño en texto libre.** El onboarding de la 001 (paso 2, "restricciones libres") y
la 004 (`agent_configs.restrictions` como "reglas adicionales del dueño", 600 caracteres en la sección
ESTILO del prompt) guardan exactamente el cuadro de texto que el brief quiere eliminar. **Propuesta:** el
wizard deja de pedirlo; lo que ya esté escrito se convierte en una situación por negocio (el revisor la
parte después), y el campo deja de entrar al prompt.

**C3. Rubro frente a plantilla.** El brief habla de "plantilla del rubro", pero en el repo hay **6 rubros**
(`restaurante, instituto, retail, servicios, salud, otros`) y **3 plantillas** (`productos,
certificaciones, servicios`) que no están atadas entre sí: el negocio elige su plantilla aparte. "El rubro
aprende" necesita saber qué negocios son del mismo rubro. **Pregunta:** ¿la plantilla se elige por el
rubro (instituto → certificaciones, retail y restaurante → productos, servicios y salud → servicios) o el
rubro y la plantilla siguen siendo independientes y "mismo rubro" significa misma plantilla?

**C4. Dónde vive la plantilla.** C-05 la puso en **código**, versionada. El brief dice que la mantiene
Zentek360 y que puede subirle reglas aprendidas. Con la plantilla en código, cada mejora es un cambio de
código y un deploy (lo hago yo con tu aprobación). Con la plantilla en base de datos, Zentek360 la edita
desde el panel de plataforma, pero un error se propaga a todos los negocios del rubro al instante.
**Propuesta:** proceso, etapas y herramientas en código como hoy; reglas, preguntas frecuentes y
objeciones de la plantilla en base de datos, versionadas, editables solo desde el panel de plataforma.

**C5. Orden de features.** El brief dice "después del motor de ventas", pero:
- la Fase 1 (capas y situaciones) **choca** con la Fase C de la 004, que configura lo mismo;
- la Fase 3 (aprendizaje) necesita la **toma de control humana**, que es la 007 y no está construida;
- la Fase 2 (chats con audio) necesita transcripción, que no está en ninguna feature viva.

**Propuesta:** la Fase 1 del brief **reemplaza** la Fase C de la 004 (tasks 434–442 ajustadas como en el
§2); las Fases 2 a 4 son la feature nueva **017**, y su Fase 3 va **después de la 007**. Orden del MVP:
004 (con la Fase 1 del brief) → 006 → 007 → 017 (Fases 2 a 4) → 009 → 010. ¿Lo confirmas, o la 017
entera va después del MVP?

### Para ratificar (tienen propuesta; las aplico si no dices otra cosa)

**C6. Datos personales en los chats importados (Ley 29733).** El brief pide autorización del negocio,
anonimizar antes de guardar y borrar los originales (decisión confirmada). Hoy no hay anonimizador de
texto libre. **Propuesta:** reglas para teléfonos, DNI/RUC, correos y direcciones con número, más un
paso del modelo para nombres propios y direcciones sin número; el original se procesa en memoria o en
MinIO temporal y se borra al terminar, también si falla. La autorización queda registrada en
`audit_logs` con quién y cuándo.

**C7. Transcripción de audios.** Es una capacidad nueva del proveedor LLM (como la visión de TASK-429:
un puerto aparte, sin tocar `LLMProvider`), con la API de audio de OpenAI por `httpx`, **sin dependencia
nueva**. Tiene costo por minuto. **Propuesta:** solo en la importación (nunca en mensajes en vivo, que
siguen siendo "solo leo texto"), con un tope de minutos por importación.

**C8. Qué es una "venta perdida".** **Propuesta:** un pedido que llegó a COBRO (o tuvo métodos de pago
enviados) y terminó `descartado`, o que el job de 7 días descartó (TASK-444). El análisis semanal agrupa
esas conversaciones y propone situaciones.

**C9. El revisor.** **Propuesta:** un permiso (`puede_revisar`) en la membresía del negocio, no un rol
nuevo; el dueño lo tiene por defecto y lo puede dar a cualquier miembro. Tú ya eres dueño de las
empresas del holding.

**C10. Cómo se eligen las situaciones de cada mensaje.** **Propuesta:** por similitud de embeddings
entre el mensaje del cliente y el "cuándo aplica" de cada situación (pgvector ya existe), hasta 5 por
turno, más las que estén marcadas como "siempre". Sin una llamada extra al modelo, para no sumar costo
ni latencia.

**C11. Prioridad entre capas.** El brief dice: situación > perfil > plantilla. Hoy el prompt no tiene
esa jerarquía explícita. **Propuesta:** el prompt la dice tal cual y las situaciones van después de las
reglas de la plantilla.

### Informativas

- **I1.** Los specs viven en `leo-sdd/features/NNN-*/` (no en `specs/`, como dice el brief). Uso la
  carpeta del repo: `leo-sdd/features/017-configuracion-en-capas/`.
- **I2.** El cuestionario de estilo amplía el paso 2 del wizard de la 001 (tono pasa a tú o usted,
  emojis, saludo) y no agrega pasos nuevos.
- **I3.** La 007 menciona "transcripciones de audio" en la bandeja; con la transcripción de la 017 se
  puede reutilizar, pero solo si decides transcribir mensajes en vivo (hoy fuera de alcance).
- **I4.** "Subir una regla al rubro" cruza negocios. Solo el operador de plataforma lo hace, solo copia
  el texto de la regla (revisado a mano, porque puede tener precios o nombres de un negocio) y queda en
  `audit_logs` de la plataforma. La suite de aislamiento lo cubre.
- **I5.** Con `BILLING_ENFORCED` apagado, nada de esto tiene límites por plan. Los costos de modelo
  (transcripción, análisis semanal, redactor) se miden en las trazas desde el día uno.
- **I6.** El Milestone B todavía no está aprobado en `tasks.md` (TASK-433). La Fase C de la 004 no
  empieza hasta que lo confirmes.

---

## 4. Lo que necesito de ti

1. Las decisiones **C1 a C5**.
2. Que me digas si alguna de **C6 a C11** no te convence (si no, quedan como están).
3. Si el **Milestone B queda aprobado** (o qué falta), para poder empezar la Fase C.

Con eso escribo la spec, el plan y las tasks (la Fase C de la 004 ajustada y la 017) y el glosario
del `CLAUDE.md`.
