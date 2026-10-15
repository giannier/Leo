# Milestone 010 — Dashboard

**Tasks:** TASK-1001 a TASK-1005 · **Quién verifica:** Claude, con pruebas automáticas y Playwright.
**Fecha:** 2026-10-15. El milestone humano ("validar con los datos del primer piloto que los números cuadran
a mano") queda para cuando se conecte el número.

## Veredicto

**Aprobado por pruebas automáticas.** El riesgo que nombra el plan (métricas mentirosas por errores de
conteo) se cubre con una prueba de reconciliación: lo que suman las filas diarias es igual al conteo directo
sobre el mismo rango, y recalcular no cambia nada.

| Qué | Evidencia | Resultado |
|---|---|---|
| Rollup idempotente y recalculable | `test_metrics.py::TestReconciliation` (dos corridas, mismas filas; suma igual al conteo directo; hasta ayer en hora local) | ✅ |
| Un día con todas sus trampas | Mensajes a ambos lados de la medianoche de Lima, el simulador, una toma, una derivación con motivo, pedidos en soles y en dólares, una traza con costo: cada número esperado | ✅ |
| Costo por traza versionado | El precio vigente en el momento; sin precio, sin costo | ✅ |
| Informe del negocio | Totales, período anterior, horas ahorradas, más vendidos; operador sin dinero; CSV con tope de 90 días | ✅ |
| Informe de la plataforma | MRR (anual / 12), conversión, margen por negocio, operación; solo para admin (404 para el resto) | ✅ |
| Retención | Purga de métricas (24 meses) y trazas (6 meses) | ✅ |
| Aislamiento | `tests/isolation/test_metrics_isolation.py` | ✅ |
| Pantallas | Playwright `resultados.spec.ts` en escritorio y Android: números en cero sin inventar ventas, cambio de período, vista de tabla, minutos guardados, CSV; la plataforma fija el precio de un modelo. Captura revisada con datos sembrados (gráfica legible, tooltip, leyenda) | ✅ |

## Decisiones

**Bloqueantes:** ninguna. **Para ratificar:** los precios de los modelos los carga Giannier (no se
sembraron), y que el costo de OCR, comprobantes y embeddings todavía no se mide por negocio (C-103).
**Informativas:** C-101 a C-105 en [clarifications.md](../clarifications.md). `recharts` 2.15.4 (aprobada
el 2026-10-14).
