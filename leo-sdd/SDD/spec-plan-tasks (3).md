# Spec + Plan + Tasks — Agendamiento de Citas

**Feature ID:** 014 · **Módulo:** 14-scheduling · **Estado:** Draft para ratificación
**Depende de:** 003 (agente), 011 (plantillas para recordatorios), 007 (bandeja)

## Resumen
Abre los rubros de servicios (clínicas, barberías, spas, institutos con charlas
informativas, consultorías): el agente agenda, confirma, reprograma y recuerda citas
directamente en la conversación. Calendario propio simple como fuente de verdad +
sincronización opcional con Google Calendar. El caso Darwin lo confirma como funcionalidad
estrella en LATAM ("agenda, confirma o reprograma citas automáticamente").

## User Stories

### HU-01: Configuración de agenda
**Como** dueño, **quiero** definir qué se puede agendar y cuándo.
- Servicios agendables: nombre, duración (15–240 min), precio (opcional, liga a catálogo),
  buffer entre citas, recursos (sillas/consultorios/profesionales: `resources`, cada uno
  con su propio horario semanal y excepciones tipo feriados/vacaciones).
- Reglas: anticipación mínima (default 2h) y máxima (default 30 días), máx citas por
  contacto activas (default 2), política de cancelación en texto (informativa).

### HU-02: Agendar desde la conversación
**Como** cliente final, **quiero** sacar cita escribiendo como siempre.
- Tool `consultar_disponibilidad(servicio, rango)` → slots libres; el agente ofrece
  máx 3 opciones concretas ("mañana 10:00, 11:30 o 4:00 pm, ¿cuál te queda?").
- Tool `agendar_cita(servicio, slot, datos)` → reserva atómica (sin doble reserva bajo
  concurrencia) + confirmación en el chat con resumen.
- WHEN el cliente pide "cámbiala para el viernes", THEN `reprogramar_cita` valida
  disponibilidad y las reglas, y confirma.
- WHEN pide cancelar, THEN se cancela con la política informada; el slot se libera.

### HU-03: Recordatorios y confirmación
- Recordatorio 24h antes (plantilla utility, F011) con botones/respuesta esperada:
  confirmar / reprogramar / cancelar; segundo recordatorio 2h antes (configurable ambos).
- WHEN el cliente no confirma al recordatorio de 24h, THEN estado `unconfirmed` visible
  en la agenda (el negocio decide si llama).
- No-show: el operador lo marca; contador por contacto visible (dato para el negocio;
  sin castigos automáticos en v1).

### HU-04: Agenda en el panel
- Vista día/semana por recurso; crear/editar/cancelar citas manualmente (walk-ins,
  teléfono); bloquear horarios (almuerzo, mantenimiento); todo en vivo (WS de F007).
- Deep-link cita ↔ conversación ↔ lead (F012: cita agendada avanza lead a `interesado`
  si estaba en `nuevo`).

### HU-05: Google Calendar (opcional por recurso)
- Sincronización bidireccional por recurso vía `CalendarProvider` (OAuth del dueño):
  citas de Leo se crean en GCal; eventos de GCal bloquean disponibilidad en Leo.
- WHEN la sync falla (token vencido), THEN alerta clara y la agenda propia sigue siendo
  fuente de verdad (nunca fallar en silencio).

## Requerimientos Funcionales
1. [RF-001] Motor de disponibilidad: horario del recurso − citas − bloqueos − buffers − eventos GCal, con anticipaciones; todo en TZ del tenant.
2. [RF-002] Reserva con lock (`SELECT FOR UPDATE` sobre el slot del recurso) — cero dobles reservas.
3. [RF-003] Estados de cita: `booked → confirmed | unconfirmed → completed | canceled | no_show`; historial append-only.
4. [RF-004] Recordatorios como jobs programados idempotentes, cancelados si la cita cambia.
5. [RF-005] Respuestas al recordatorio procesadas por el listener del canal: confirmación/reprogramación entra al flujo del agente con contexto de la cita.
6. [RF-006] `CalendarProvider` (interfaz) + `GoogleCalendarProvider` (OAuth, watch/refresh); tokens cifrados como toda credencial.
7. [RF-007] Tools de citas activables por tenant (tenant_tools de F004) y visibles solo si hay ≥1 servicio agendable configurado.
8. [RF-008] Límite por plan: recursos activos (Emprende 1, Crece 3, Pro 10 — referencia en `plans`).

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Empate por el mismo slot** (dos clientes a la vez): el lock decide; al perdedor el agente le ofrece el siguiente slot con naturalidad ("¡uy, se acaba de ocupar! ¿te va 11:30?").
- **C-002 Cliente agenda fuera de reglas** ("para dentro de 20 minutos"): el agente explica la anticipación mínima y ofrece lo más cercano válido.
- **C-003 Cambio de horario del recurso con citas ya tomadas:** las citas existentes NO se cancelan automáticamente; se listan como conflictos para resolución humana (llamar/reprogramar) — jamás cancelar citas de clientes en silencio.
- **C-004 GCal como origen ambiguo** (evento privado del dueño): todo evento GCal bloquea disponibilidad; Leo no interpreta contenido (privacidad + simplicidad).
- **C-005 Zona horaria del cliente final:** se asume la del negocio (servicios presenciales); citas virtuales multi-TZ quedan post-v1 explícito.
- **C-006 Recordatorio fuera de ventana sin plantilla aprobada:** el sistema exige plantilla aprobada al activar recordatorios (guard de configuración, no fallo en runtime).

## Fuera de Scope
- Pagos/señas por adelantado para reservar (integrable después con F009/pasarela), citas grupales/clases con cupos, multi-TZ, waitlists.

## Plan técnico

### Constitution Check ✓
✅ Tools registradas, núcleo intacto. ✅ `CalendarProvider` como adaptador. ✅ Compliance de recordatorios (plantillas + topes). ✅ Config sobre código (servicios/recursos/reglas en BD). ✅ Nunca fallar en silencio (C-003, sync GCal).

### Arquitectura
```
AvailabilityService: slots = horario(resource) ⊖ (citas ∪ bloqueos ∪ gcal_busy) ⊕ reglas
BookingService: FOR UPDATE por (resource, rango) ─► appointment + eventos + jobs de recordatorio
Tools: consultar_disponibilidad / agendar_cita / reprogramar_cita / cancelar_cita
ReminderJobs (ARQ diferidos por cita) ─► plantilla ─► listener marca confirmed/…
GoogleCalendarProvider: push de citas + pull de busy (watch + resync periódico)
Agenda UI: vista calendario (day/week) + WS
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Modelo de slots | Cálculo on-the-fly (sin tabla de slots) | Sin sincronizar slots materializados; volúmenes chicos por recurso |
| Anti doble-reserva | Constraint por exclusión (`EXCLUDE USING gist` sobre rango+recurso) + FOR UPDATE | Doble cinturón: la BD garantiza aunque el código falle |
| Recordatorios | Jobs ARQ diferidos con `appointment_version` (se auto-invalidan si la cita cambió) | Idempotencia simple |
| GCal | Solo busy/free hacia Leo; detalle completo hacia GCal | Privacidad (C-004) y simplicidad |
| UI calendario | FullCalendar (dependencia nueva — aprobar) | Estándar maduro day/week/recursos |

### Data Model (migraciones)
```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE bookable_services (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL, duration_min INT NOT NULL CHECK (duration_min BETWEEN 15 AND 240),
  buffer_min INT NOT NULL DEFAULT 0, price NUMERIC(12,2), product_id UUID REFERENCES products(id),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE resources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL, weekly_hours JSONB NOT NULL DEFAULT '{}',
  gcal_connected BOOLEAN NOT NULL DEFAULT FALSE, gcal_tokens_enc BYTEA, gcal_calendar_id TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE resource_services (   -- qué recurso atiende qué servicio
  resource_id UUID REFERENCES resources(id) ON DELETE CASCADE,
  service_id UUID REFERENCES bookable_services(id) ON DELETE CASCADE,
  PRIMARY KEY (resource_id, service_id)
);
CREATE TABLE resource_blocks (      -- bloqueos y excepciones
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL, resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL, reason TEXT
);

CREATE TABLE appointments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  service_id UUID NOT NULL REFERENCES bookable_services(id),
  resource_id UUID NOT NULL REFERENCES resources(id),
  contact_id UUID REFERENCES contacts(id),         -- NULL si walk-in manual
  conversation_id UUID REFERENCES conversations(id),
  lead_id UUID REFERENCES leads(id),
  starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'booked'
    CHECK (status IN ('booked','confirmed','unconfirmed','completed','canceled','no_show')),
  version INT NOT NULL DEFAULT 1,                  -- invalida recordatorios viejos
  gcal_event_id TEXT, notes TEXT,
  created_by TEXT NOT NULL CHECK (created_by IN ('agent','user')),
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
  EXCLUDE USING gist (resource_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
    WHERE (status IN ('booked','confirmed','unconfirmed'))
);
CREATE TABLE appointment_events (
  id BIGSERIAL PRIMARY KEY, tenant_id UUID NOT NULL,
  appointment_id UUID NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
  event TEXT NOT NULL, actor TEXT NOT NULL, detail JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE reminder_settings (
  tenant_id UUID PRIMARY KEY REFERENCES tenants(id),
  h24_enabled BOOLEAN NOT NULL DEFAULT TRUE, h24_template_id UUID REFERENCES templates(id),
  h2_enabled BOOLEAN NOT NULL DEFAULT FALSE, h2_template_id UUID REFERENCES templates(id),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE plans ADD COLUMN max_resources INT NOT NULL DEFAULT 1;
```

### API
- Config: `GET/POST/PUT/DELETE /tenant/scheduling/services|resources|blocks` · `GET/PUT /tenant/scheduling/reminder-settings` · `POST /tenant/scheduling/resources/:id/gcal/connect|disconnect` (OAuth).
- Agenda: `GET /tenant/appointments?view=day|week&resource=` · `POST /tenant/appointments` (manual) · `PUT .../:id` (reprogramar/estado) · `GET /tenant/scheduling/conflicts` (C-003).
- Disponibilidad (interna para tools y UI): `GET /tenant/scheduling/availability?service&from&to`.

### Testing
Unit: motor de disponibilidad (tabla de casos: buffers, bloqueos, anticipaciones, TZ, DST peruano-none pero probar TZ ajenas), invalidación de recordatorios por versión. Integration: doble reserva concurrente (constraint gana), flujo completo agendar→recordar→confirmar por WhatsApp, reprogramación por chat, sync GCal con mock del provider (push/pull/token vencido). Evals: el agente ofrece máx 3 slots, respeta reglas, maneja C-001/C-002 con gracia. E2E: agenda en panel con drag para reprogramar.

### Riesgos
| Riesgo | Mitigación |
|---|---|
| Errores de TZ arruinan citas reales | Todo en TIMESTAMPTZ + TZ del tenant en render; suite de casos horarios |
| GCal API caprichosa (cuotas, tokens) | Agenda propia = fuente de verdad; sync degradable con alerta |
| Recordatorios duplicados/huérfanos | Versionado de cita + jobs idempotentes |

### Estimación
2 semanas. Complejidad: Alta (concurrencia + tiempo + integración externa).

## Tasks
Commits: `feat(scheduling): ... [TASK-14XX]`
- [ ] **TASK-1401: Migraciones (con EXCLUDE gist) + config de servicios/recursos/bloqueos (API+UI).**
- [ ] **TASK-1402: AvailabilityService + suite de casos horarios.** Depends: 1401.
- [ ] **TASK-1403: BookingService (reserva/reprogramación/cancelación atómicas) + eventos + conflictos C-003.** Depends: 1402.
- [ ] **TASK-1404: Tools del agente + evals de agendamiento.** Depends: 1403; toca `agent/` → evals.
- [ ] **TASK-1405: Recordatorios (jobs versionados + listener de respuestas + settings con guard C-006).** Depends: 1403, F011.
- [ ] **TASK-1406: GoogleCalendarProvider (OAuth, push/pull, resync, alertas).** Depends: 1403. Dependencia nueva (SDK Google) — aprobar.
- [ ] **TASK-1407: Agenda UI (FullCalendar — aprobar dependencia) + integración lead/conversación.** Depends: 1403–1405.
- [ ] **TASK-1408: Endurecimiento + carga (100 reservas concurrentes).** Milestone humano: piloto con un negocio de servicios real (barbería/consultorio) una semana; revisar no-shows y fricción del recordatorio.

Dependencias: 1401 → 1402 → 1403 → 1404/1405/1406 → 1407 → 1408.
