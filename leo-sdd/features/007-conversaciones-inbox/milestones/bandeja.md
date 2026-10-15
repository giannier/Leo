# Milestone 007 — Bandeja de conversaciones

**Tasks:** TASK-701 a TASK-708 · **Quién verifica:** Claude, con pruebas automáticas y Playwright
(instrucción de Giannier del 2026-10-14: los milestones intermedios se verifican así y él prueba a mano al
conectar el número). **Fecha:** 2026-10-14.

El milestone humano de la TASK-708 ("Giannier y un ayudante simulan operación real 30 min con tráfico del
número de prueba") **queda para cuando se conecte el número** (TASK-459), junto con los demás.

## Veredicto

**Aprobado por pruebas automáticas.** La bandeja no llama al modelo: lo que importa aquí es que el agente
se calle mientras una persona tiene la conversación, y eso se prueba con el agente real del pipeline (modo
offline) y con el navegador.

## Criterios de aceptación (spec)

| Criterio | Evidencia | Resultado |
|---|---|---|
| Takeover corta al agente incluso con generación en curso | `test_inbox.py::TestTakeover::test_a_reply_the_model_was_still_writing_is_never_sent`: la conversación se toma mientras el modelo escribe; la respuesta queda `descartado` y nunca llega al proveedor. `test_while_taken_the_agent_is_quiet_and_spends_no_quota`: tomada, el agente no corre ni gasta cupo. El simulador respeta lo mismo (`TestThePipelineGate`) | ✅ |
| Dos operadores: lock correcto, presencia visible, cero duplicados | `test_one_person_wins_the_other_is_told_who` (dos tomas a la vez: una gana, la otra recibe 409 con el nombre). Playwright `e2e/bandeja.spec.ts`: dos navegadores, cada uno ve que el otro mira; al tomar, el otro ve "La tiene …" sin recargar, su compositor se bloquea y no tiene "Tomar"; el mensaje humano llega en vivo una sola vez | ✅ |
| Escalamiento completo: agente deriva → aviso → toma → resolución → devolución con contexto | `test_a_handed_over_conversation_leaves_the_queue_when_taken`, `test_an_unattended_handover_is_emailed_once` (correo una sola vez), `test_writing_takes_it_and_the_agent_later_reads_it` (al devolver, el agente lee lo que escribió la persona) | ✅ |
| Bloqueo de ventana 24 h | `test_outside_the_24_hour_window_it_is_refused` (403 `outside_24h_window`); el panel deshabilita el compositor y lo explica | ✅ |
| WebSocket sobrevive reconexiones (re-sync) | Al conectar y reconectar el panel vuelve a leer por la API (C-107); sin socket, consulta cada 30 s. `test_inbox_realtime.py`: eventos solo del propio negocio; token inválido → 4401; miembro desactivado → 4403 | ✅ |
| Cobertura ≥ 70 % (takeover y guards al 80 %) | `api/v1/inbox` 92 %, `realtime` 89 %, `services/inbox` 87 %, `inbox_jobs` 87 %, `inbox_queries` 84 %, `core/events` 93 %, `workers/inbox` cubierto por `tests/unit/test_inbox_worker.py` | ✅ |

Aislamiento: `tests/isolation/test_inbox_isolation.py`. Con la conversación de otro negocio, los 12 endpoints
responden 404 y no la tocan; la lista y la búsqueda no la muestran aunque el texto coincida; los ajustes son
de cada negocio.

## Corridas

- Backend: `3407 passed, 3 skipped` (los en vivo se corren a mano). Ruff, formato y mypy limpios.
- Frontend: `tsc`, `eslint` y `next build` limpios (`/bandeja` 7,3 kB).
- Playwright, suite completa: 81 de 86 en la primera corrida. Fallaron:
  - la subida de imagen a la biblioteca (necesita MinIO, ya anotado en el Milestone C);
  - tres pruebas del panel de plataforma en Android. El menú de `/admin` creció a cuatro entradas sin
    saltar de línea y desbordaba la pantalla en 412 px, así que la página se desplazaba de lado y un texto
    tapaba los botones. Se corrigió (`flex-wrap`) y `admin.spec.ts` y `navigation.spec.ts` pasan 14 de 14.
- `e2e/bandeja.spec.ts` pasa en escritorio y en Android. Prueba a dos operadores (presencia, toma
  exclusiva en vivo, mensaje humano, devolución, notas) y los ajustes del admin. El operador no los ve.

## Decisiones

**Bloqueantes:** ninguna.

**Para ratificar** (afectan dependencias o datos personales):
- C-101: Web Push con la pestaña cerrada necesita `pywebpush` (dependencia nueva). Hoy hay aviso del
  navegador con la pestaña abierta y correo de cola desatendida.
- C-102: borrar los datos de un contacto a pedido (datos personales). No se implementó; el opt-out sí,
  auditado.

**Informativas:** C-103 a C-111 en [clarifications.md](../clarifications.md). Por ejemplo: la lista
pagina sin virtualizar; los comprobantes se aprueban en el pedido, con un enlace desde la bandeja;
"Bandeja" reemplaza a "Catálogo" en la barra del teléfono.
