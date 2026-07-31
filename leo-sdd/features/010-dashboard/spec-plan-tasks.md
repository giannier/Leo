# Spec + Plan — Dashboard de Métricas

**Feature ID:** 010 · **Módulo:** 10-dashboard · **Estado:** Draft para ratificación
**Depende de:** 002, 003, 004, 007, 009 (consume datos de todas)
**Tamaño:** feature corta — documento combinado.

## Resumen
El dashboard cierra el MVP demostrando valor con números: el dueño ve qué hace su agente
por él (conversaciones atendidas, ventas asistidas, horas ahorradas) y Giannier ve la
salud del negocio (tenants, consumo, costos de LLM, conversión trial→pago). La
investigación mostró que las plataformas serias venden con el dashboard ("actívalo y
observa conversaciones, conversiones y CSAT").

## User Stories — Panel del Tenant
- **HU-01 Resumen del período:** conversaciones totales, atendidas 100% por el agente vs con humano, mensajes in/out, tiempo de primera respuesta, escalamientos por motivo.
- **HU-02 Ventas asistidas:** pedidos informales registrados (F004 C-005), comprobantes aprobados (monto total), productos más consultados/vendidos.
- **HU-03 Valor percibido:** "horas ahorradas" estimadas (conversaciones del agente × tiempo medio humano configurable, default 4 min) — el número que renueva suscripciones.
- **HU-04 Consumo:** uso del período (mensajes IA, asientos, RAG) con proyección — reusa /tenant/usage.
- **HU-05 Filtros:** período (7/30 días, período de facturación), exportar CSV.

## User Stories — Panel Admin (Giannier)
- **HU-06 Salud del negocio:** tenants por estado, registros/semana, conversión trial→pago, churn, MRR (desde subscriptions), cola de conexiones pendiente.
- **HU-07 Costos:** tokens y costo estimado de LLM por tenant/período (desde agent_traces) vs ingreso del tenant → margen por tenant (la métrica que decide precios).
- **HU-08 Operación:** dead-letters, fallos de envío, latencia p95 del agente, quality ratings en riesgo, evals del último release.

## Requerimientos y decisiones
- [RF-001] Métricas pre-agregadas por job nocturno en `metrics_daily` (tenant_id, fecha, contadores) — los dashboards no hacen agregaciones pesadas en vivo; el día en curso se calcula en vivo solo para el tenant que mira.
- [RF-002] Costo LLM: tabla `model_costs` (modelo, costo por 1K tokens in/out, vigencia) mantenida por admin; el costo por traza se calcula al registrar.
- [RF-003] Export CSV por rango (límite 90 días por request).
- [RF-004] Todo respeta roles (operator no ve dinero) y aislamiento.
- **C-001 "Ventas asistidas" honestas:** solo comprobantes aprobados + pedidos con confirmación explícita; nada inflado — la confianza vale más que el número.
- **C-002 CSAT:** post-MVP (requiere encuesta al cliente final por plantillas F011); el diseño deja la columna.
- **C-003 Retención de metrics_daily:** 24 meses; trazas crudas 6 meses (job de purga).

## Plan técnico (delta)
- Migraciones: `metrics_daily` (tenant_id, date, métrica JSONB o columnas fijas — elegir columnas fijas por consulta), `model_costs`; ALTER agent_traces ADD cost_usd NUMERIC(10,6).
- Job `metrics_rollup` (nocturno, idempotente por fecha, re-ejecutable).
- API: `GET /tenant/metrics?from&to` · `GET /tenant/metrics/export.csv` · `GET /platform-admin/metrics` · `GET/PUT /platform-admin/model-costs`.
- Frontend: gráficas con Recharts (ya en stack de artifacts del ecosistema; para Next.js usar recharts npm), tarjetas de KPI, comparación vs período anterior.
- Testing: rollup idempotente y re-ejecutable; exactitud de costos con model_costs versionados; roles en endpoints; snapshot tests de agregaciones con fixtures.
- Riesgo principal: métricas mentirosas por bugs de conteo → tests de reconciliación (suma de metrics_daily == conteo directo en rangos de prueba).

## Tasks
Commits: `feat(dashboard): ... [TASK-10XX]`
- [ ] **TASK-1001: Migraciones + model_costs + costo por traza.**
- [ ] **TASK-1002: Job metrics_rollup + tests de reconciliación.** Depends: 1001.
- [ ] **TASK-1003: API de métricas tenant/admin + export CSV + roles.** Depends: 1002.
- [ ] **TASK-1004: UI dashboard del tenant** (KPIs, gráficas, consumo, export). Depends: 1003.
- [ ] **TASK-1005: UI dashboard admin** (salud, márgenes por tenant, operación) + purgas de retención. Depends: 1003. Milestone humano: validar con datos reales del primer tenant piloto que los números cuadran a mano.

Estimación: 1 semana. Complejidad: Media.
