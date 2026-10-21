# CLAUDE.md — Instrucciones para Claude Code (proyecto Leo)

> Copia este archivo a la raíz del repositorio de Leo. Claude Code lo lee automáticamente.

## Antes de tocar cualquier cosa
1. Lee `leo-sdd/constitution.md`. Es ley. Sus "Restricciones para la IA" no admiten excepciones.
2. Lee el spec, plan y tasks de la feature en curso (`leo-sdd/features/NNN-*/`).
3. Trabaja **una task por sesión**, en el orden y con las dependencias de `tasks.md`.
4. Si una decisión no está en los docs y afecta dinero, datos personales, contratos públicos o el costo
   del LLM, NO la inventes: propónla, espera aprobación de Giannier, y documenta la decisión en el plan
   de la feature antes de implementarla. El resto se decide con la lectura conservadora (ver
   "Reglas que no están en la spec").

## Orden de desarrollo del MVP
001-onboarding → 002-canal-whatsapp → 003-agent-core → 004-tools →
006-conocimiento-avanzado → 007-conversaciones-inbox → 009-billing → 010-dashboard
(005-catalog y 08-inbox están absorbidos por 001/004 y 007 respectivamente.)

### Secuencia acordada (Giannier, 2026-10-13)
Milestone B de la 004 → prueba con número real por túnel (TASK-459) → Fase C de la 004 = Fase 1 de la 017
(capas y situaciones) → Fases D y E de la 004 → 007 → despliegue en VPS con número real → piloto del
holding → 017 Fases 2 a 4. Dónde quedan 006, 009 y 010 está pendiente de confirmar (ver
`017/clarifications.md` C-05).

### Cambio de secuencia (Giannier, 2026-10-14)
"Sigue con la Fase C, termina de construir todo el proyecto y al final conecto el número": la prueba con
número real (TASK-459) pasa **al final**, antes del despliegue. Orden: Fase C (= 017 Fase 1) → Fases D y
E de la 004 → 007 → 006 → 009 → 010 → TASK-459 (número real) → VPS → piloto → 017 Fases 2 a 4.
Los milestones humanos intermedios (C, D, E y los de 007, 006, 009 y 010) los verifica Claude con pruebas
automáticas contra el modelo real y Playwright, deja la evidencia en `milestones/` y sigue; Giannier los
prueba a mano al conectar el número. Las decisiones de dinero, datos personales, contratos públicos y costo
del LLM se siguen consultando.

### Experiencia simple (Giannier, 2026-10-15)
Tras comparar con Ventor: antes del número real se construye la **018-experiencia-simple** (catálogo de verdad,
asistente en un solo lugar, "¿Respondió bien?" en el simulador), sin copiloto ni diagnóstico con IA (costo
del LLM), sin login con Google y sin dependencias nuevas. Orden: 018 → TASK-459 (número real) → VPS → piloto.

## Fase 2 (post-MVP) — solo tras cerrar el MVP y con ratificación de Giannier por feature
011-campanas → 012-crm-leads → 013-analytics-csat → (014-citas o 016-pedidos-delivery,
según tenants reales) → la restante → 015-integraciones. Ver `leo-sdd/post-mvp/README.md`.

## Reglas duras (resumen operativo de la Constitution)
- Toda query de negocio filtra por `tenant_id` en la capa de repositorio. La suite de
  aislamiento multi-tenant nunca se desactiva ni se marca skip.
- SDKs de terceros solo dentro de su adaptador (`providers/`). Nada de llamadas directas.
- Esquema de BD solo por migración Alembic. Nunca `create_all` ni edición manual.
- Dependencias nuevas: proponer con justificación y esperar aprobación explícita.
- Billing y credenciales: todo diff requiere revisión humana antes de merge.
- Prompts del agente: cualquier cambio corre los evals (`make evals`) y se adjunta el
  resultado en el PR.
- Dinero siempre con Decimal. Jamás float.
- Commits: Conventional Commits + `[TASK-XXX]`. Un commit por task como mínimo.

## Glosario
Términos oficiales de Leo y su nombre en el repo (feature 017, `leo-sdd/features/017-configuracion-en-capas/`).
- **Plantilla de proceso**: **cómo** se vende. `productos`, `certificaciones` o `servicios`. Etapas,
  condiciones, herramientas y embudo en código (`process/templates/`); sus **reglas** en base de datos,
  versionadas (`catalog_versions` kind `proceso`, `catalog_rules`).
- **Rubro**: **qué** se sabe del negocio típico (`business_profiles.industry` → `rubros`): reglas, preguntas
  frecuentes, objeciones y situaciones sugeridas, versionadas (`catalog_versions` kind `rubro`). Sugiere
  una plantilla de proceso; el negocio puede cambiarla. "Mismo rubro" no es "misma plantilla".
- **Perfil del negocio**: lo que el negocio llena por formulario (`business_profiles`, `agent_configs`,
  `business_info`, `payment_methods_business`, `promotions`). Nunca texto libre de reglas.
- **Situación**: regla propia de un negocio, una tarjeta (`situations`): cuándo aplica, qué hace el
  agente y opcionales (archivos, derivar, ejemplo, etapas, "siempre"). Estados borrador, activa, inactiva.
- **Regla** (de plantilla o de rubro): una instrucción con id estable (`rule_key`). El negocio la
  **apaga** (`tenant_rule_switches`), nunca la edita.
- **Prioridad entre capas**: situación > perfil del negocio > rubro > plantilla de proceso.
- **Novedades**: reglas nuevas de una versión publicada de plantilla o rubro; llegan activas salvo las
  apagadas, y el revisor ve el aviso (`tenant_catalog_seen`).
- **Revisor**: permiso por miembro del negocio (`tenant_users.puede_revisar`), no un rol. Aprueba lo que
  el agente aprende. Hoy es Giannier en las empresas del holding.
- **Sugerencia**: algo que el agente propone aprender (situación, ejemplo o dato); nada se aplica sin el
  revisor (`suggestions`, Fase 3).
- **Ejemplo ganador**: conversación de una venta confirmada, anonimizada, aprobada como ejemplo (en lote).
- **Perfil de estilo**: cómo escribe el vendedor real, sacado de sus mensajes escritos (Fase 2).
- **Capacidad**: lo que el agente hace (`atender`, `vender`, `cotizar`, `agendar`, `captar`; `agent_configs.capacidades`).
  La plantilla de proceso y el objetivo **se derivan** de las capacidades (`process/capabilities.py`); la plantilla manual queda
  como avanzado. Cada capacidad enciende sus herramientas y su bloque del prompt (feature 019, `leo-sdd/features/019-*`).
- **Agenda**: calendario propio (`scheduling/`): servicios agendables (`products.agendable`), recursos, bloqueos y citas; la
  base impide dos citas vivas que se pisen en un recurso. Herramientas `consultar_horarios`, `agendar_cita`, `gestionar_cita`.
  Recordatorios apagados por defecto: necesitan una plantilla aprobada fuera de la ventana de 24 h.
- **Cotización**: una cotización pedida vive en el lead (`leads.quote_*`: solicitada, enviada) con lo que el agente juntó.
- **Calificación**: el asistente anota el interés (`calificar_cliente`) y el lead queda `caliente`, `tibio` o `frio`.
- **Modo piloto**: `agent_configs.piloto_pct`, el porcentaje de clientes (por persona) que atiende el asistente; el resto, el equipo.
- **Chat web**: canal `web` sin número de WhatsApp (página pública y widget); `REAL_CHANNELS` agrupa los canales reales.
- **Operador de plataforma (Zentek360)**: mantiene y publica plantillas y rubros, con evals obligatorios.
- `consultar_catalogo`: nombre viejo de la tool de catálogo (agente previo a la 004). En el registry por
  etapa es solo un **alias** de `buscar_productos` (no aparece en las tools que ve el modelo); el
  contrato usa `buscar_productos` y `ver_producto`. Ratificado por Giannier (Milestone B, decisión 6;
  `leo-sdd/features/004-motor-de-ventas/plan.md`, "Decisiones Posteriores").

## Cobro de la plataforma: apagado por ahora
- `BILLING_ENFORCED=false` (por defecto) mientras Giannier prueba Leo con sus propios negocios: la prueba
  no vence ni pausa, el cupo de mensajes se cuenta pero no se aplica, no hay límite de asientos ni de
  conocimiento, y un mismo teléfono puede abrir varios negocios. El panel muestra "Modo pruebas".
- Todo lo de cobro se sigue **desarrollando y probando** (los tests corren con el interruptor encendido),
  pero cualquier límite o cobro nuevo debe respetar `billing_enforced()` (`services/trial.py`). Se
  enciende el día que Leo empiece a vender.
- No confundir con los comprobantes de pago de los clientes de cada negocio (motor de ventas): eso es
  funcionalidad del producto y está activo.

## Dónde se corren las pruebas
- `make test` (y `make isolation`, `make lint`, `make evals`) **no se corre en el host de Windows**: todo
  corre dentro del contenedor (`docker compose`, `make up`) o en WSL2. Si una task pide "tests en verde",
  se miden ahí.

## Despliegue en el VPS
Quien opere el servidor (Giannier o un Claude en el VPS) sigue `deploy/RUNBOOK.md`, sección 10 incluida:
qué hace cada quien, qué comandos **nunca** correr (`down -v` y `prune --volumes` borran la base y con ella
los archivos) y cómo no exponer `deploy/.env.prod`. Los archivos (fotos, comprobantes, documentos) viven
en Postgres (`STORAGE_BACKEND=postgres`); la Constitution se actualizó en consecuencia.

## Definition of Done de una task
- Código + tests de la task en verde localmente (`make test`), lint y mypy limpios.
- Cobertura del módulo dentro del estándar (80% críticos / 70% resto).
- Criterios de "Verification" de la task cumplidos y demostrables.
- Sin TODOs silenciosos: lo pendiente se anota en la task siguiente o se pregunta.
- Si la task cambió comportamiento visible: actualizar el doc correspondiente en `leo-sdd/`.

## Milestones de revisión humana
No avances más allá de un milestone marcado en `tasks.md` sin confirmación explícita de
Giannier. Los milestones existen porque él prueba a mano con datos y números reales.

### Reglas que no están en la spec (desde el Milestone B)
- Si una regla no está en la spec, decide con la **lectura conservadora**, regístrala en
  `clarifications.md` de la feature y sigue.
- Detente a preguntar solo por lo que afecte **dinero, datos personales, contratos públicos o el costo
  del LLM**.
- En el reporte de cada milestone separa las decisiones en tres grupos: **bloqueantes**, **para
  ratificar** e **informativas**.

## Estructura esperada del repo
```
leo/
├── CLAUDE.md
├── leo-sdd/               # ← este paquete SDD completo
├── backend/
│   ├── app/
│   │   ├── api/           # routers FastAPI
│   │   ├── services/      # lógica de negocio
│   │   ├── repositories/  # acceso a datos (exigen tenant_id)
│   │   ├── providers/     # adaptadores: llm/, whatsapp/, payments/, email/, storage/
│   │   ├── agent/         # agent-core, tools/, prompts/
│   │   ├── models/        # SQLAlchemy
│   │   ├── workers/       # jobs ARQ
│   │   └── core/          # config, seguridad, deps
│   ├── alembic/
│   ├── evals/             # datasets dorados + runner
│   └── tests/             # unit/ integration/ isolation/
├── frontend/              # Next.js 15 (panel tenant + admin)
└── deploy/                # docker-compose dev/prod, Caddy, CI
```

## Comandos estándar (crearlos en TASK-001 y mantenerlos)
```
make up          # docker compose dev
make test        # pytest completo
make isolation   # solo suite multi-tenant
make evals       # evals del agente
make lint        # ruff + mypy
make migrate     # alembic upgrade head
```
