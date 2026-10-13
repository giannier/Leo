# Tasks — Configuración en capas y aprendizaje (017)

**Convención de commits:** `feat(módulo): descripción [TASK-XXX]`.
**Gate de cada task:** `ruff check`, `ruff format --check`, `mypy backend/app` y los tests del módulo en
verde. Los tests de aislamiento se escriben junto con cada tabla nueva.

## Fase 1 · Capas y situaciones manuales

Se implementa **como la Fase C de la 004** (C-05): tasks TASK-460 a 469 y las 434 a 442 ajustadas, en
`004-motor-de-ventas/tasks.md`. Empieza cuando Giannier apruebe el Milestone B (TASK-433), después de la
prueba con un número real por túnel (TASK-459).

## Secuencia hasta las Fases 2 a 4 (C-05)

Prueba con número real por túnel (TASK-459) → Fase 1 → Fases D y E de la 004 → 007 (toma de control humana) → despliegue en VPS con número real →
piloto del holding → Fases 2 a 4 de esta feature.

## Fase 2 · Importación de chats y estilo (después del piloto)

Se ratifica C-06 y C-07 al arrancar. Tasks a detallar entonces:

- [ ] TASK-1701: Importación de chats exportados de WhatsApp con autorización registrada (`chat_imports`)
- [ ] TASK-1702: Puerto de transcripción (`TranscriptionProvider`) y transcripción de audios de la importación
- [ ] TASK-1703: Anonimizador de texto (reglas + modelo) y borrado del original, también si falla
- [ ] TASK-1704: Perfil de estilo desde los mensajes escritos (como sugerencia)
- [ ] TASK-1705: Conversaciones ejemplo anonimizadas con recuperación por similitud
- [ ] TASK-1706: Argumentos y objeciones (incluye audios) como sugerencias
- [ ] TASK-1707: 🧑 Milestone 017-2 con un negocio del holding

## Fase 3 · Ciclo de aprendizaje (después de la 007 y del piloto)

Se ratifica C-08 al arrancar.

- [ ] TASK-1711: `suggestions` y bandeja del revisor (aprobar, editar y aprobar, rechazar, probar en el simulador; no repetir rechazadas)
- [ ] TASK-1712: Fuente "toma de control humana" (evento de la 007)
- [ ] TASK-1713: Fuente "venta confirmada": ejemplos ganadores, aprobación en lote
- [ ] TASK-1714: Fuente "venta perdida": análisis semanal
- [ ] TASK-1715: Fuente "pregunta sin respuesta": `consultar_conocimiento` sin resultados
- [ ] TASK-1716: Historial de cambios y deshacer (`config_history`)
- [ ] TASK-1717: 🧑 Milestone 017-3 con el piloto

## Fase 4 · Mejora del rubro y redactor asistido

- [ ] TASK-1721: Situaciones parecidas por rubro en el panel de plataforma → borrador de versión de rubro → evals → publicar
- [ ] TASK-1722: Redactor asistido con detección de choques
- [ ] TASK-1723: 🧑 Milestone 017-4
