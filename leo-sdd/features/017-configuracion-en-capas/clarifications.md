# Clarifications — Configuración en capas y aprendizaje (017)

Respuestas de Giannier al mapeo (`mapeo.md`), 2026-10-13. Ratificadas salvo donde se indica.

---

## C-01: Las etapas no se editan; sus reglas se apagan
**Decisión (Giannier):** se quita la edición de textos de etapa de la 004 (C-05 y
`process_stage_overrides`) y los textos se parten en reglas con interruptor. No hay datos reales, así que
no se migran ediciones. Los evals se corren una vez, al cerrar la Fase 1.
**Impacto:** TASK-435 y 440 de la 004 cambian; la tabla `process_stage_overrides` (0016) se elimina en
una migración de la Fase 1.

## C-02: Sin texto libre del dueño; situaciones sugeridas al final del wizard
**Decisión (Giannier):** lo ya escrito en `agent_configs.restrictions` pasa a situaciones y el wizard deja
de pedir texto libre. Al final del wizard se ofrecen de 3 a 5 situaciones sugeridas del rubro (por
ejemplo "si piden factura") para activar o completar. Nadie empieza con una pantalla vacía.

## C-03: Rubro y plantilla de proceso son dos dimensiones
**Decisión (Giannier):**
- **Plantilla de proceso** (`productos`, `certificaciones`, `servicios`): **cómo** se vende, las etapas.
- **Rubro**: **qué** se sabe, con reglas, preguntas frecuentes y objeciones propias.
- Cada rubro sugiere una plantilla por defecto y el negocio puede cambiarla.
- "Mismo rubro" significa mismo rubro, no misma plantilla.
- **Prioridad entre capas:** situación > perfil del negocio > rubro > plantilla de proceso.

## C-04: Proceso en código; reglas de plantilla y de rubro en base de datos
**Decisión (Giannier, sobre la propuesta del mapeo):** orden de etapas, condiciones, herramientas y
embudo siguen en código. Reglas, preguntas frecuentes y objeciones de plantilla y rubro viven en base de
datos, versionadas, editables solo desde el panel de plataforma. Además:
- una versión nueva de plantilla o rubro **pasa los evals antes de publicarse**;
- las reglas nuevas llegan **activas** a los negocios, salvo las que el negocio apagó;
- el revisor ve un **aviso de novedades**.

## C-05: Orden de implementación
**Decisión (Giannier):** la Fase 1 del brief **reemplaza la Fase C de la 004**. Las Fases 2 a 4 de la 017
van **después del MVP**, con el piloto del holding corriendo con número real, porque el aprendizaje
necesita conversaciones reales.
**Secuencia:** prueba con número real por túnel (TASK-459 de la 004, agregada a sugerencia aceptada por
Giannier) → Fase 1 → Fases D y E de la 004 → 007 → despliegue en VPS con número real → piloto → 017
Fases 2 a 4.
**Pendiente de confirmar:** dónde quedan la 006 (conocimiento avanzado), la 009 (billing) y la 010
(dashboard), que no aparecen en la secuencia. Lectura conservadora mientras tanto: después de la 007,
en el orden del `CLAUDE.md`, sin bloquear el piloto.

## C-06 a C-08: Datos de chats, transcripción y "venta perdida"
**Decisión (Giannier):** se ratifican cuando arranque la 017 (Fases 2 y 3), no ahora. Las propuestas
siguen en `mapeo.md` §3 (C6, C7 y C8).

## C-09: El revisor es un permiso
**Decisión:** como se propuso. `puede_revisar` en la membresía del negocio; el dueño lo tiene por defecto
y lo puede dar a cualquier miembro. Entra en la Fase 1 porque el aviso de novedades es para el revisor.

## C-10: Qué situaciones entran en cada mensaje
**Decisión (Giannier):** las marcadas "siempre" más las que coinciden con el mensaje **y con la etapa**,
hasta un máximo configurable (5 por defecto).
**Diferencia con la propuesta del mapeo:** la propuesta no filtraba por etapa y tenía un máximo fijo;
se adopta la versión de Giannier. "Coincide con el mensaje" se mide por similitud de embeddings entre el
mensaje y el "cuándo aplica" (pgvector, una llamada de embedding por turno, sin llamada extra al modelo de
chat). "Coincide con la etapa": la situación no tiene etapas (aplica en todas) o incluye la actual.

## C-11: La prioridad se declara en el prompt
**Decisión:** como se propuso, con el orden de C-03: el prompt la dice tal cual y presenta las capas de la
menos a la más específica, para que lo último que lea el modelo sea lo que manda.

## C-12: Milestone B y arranque
**Decisión (Giannier):** confirma el Milestone B después de probar los escenarios 4 y 5. La Fase 1 no se
implementa hasta esa aprobación; mientras tanto se escriben spec, plan y tasks y el glosario.

## C-13: TASK-459 al final (Giannier, 2026-10-14)
**Decisión:** "Sigue con la Fase C, termina de construir todo el proyecto y al final conecto el número". La
Fase 1 ya no depende de TASK-459: TASK-460 depende de TASK-433 (Milestone B aprobado). La prueba con número
real va después de construir el resto del MVP. Detalle de la secuencia en `CLAUDE.md`.
