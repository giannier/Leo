# Constitution — Leo (Agente de IA, AaaS) — v2

> Estado: **Aprobada** por Giannier (jul 2026). Todo documento y línea de código del proyecto debe respetarla.

## Descripción del Sistema
Leo es una plataforma AaaS (Agents as a Service) multi-tenant que permite a negocios
conectar su número de WhatsApp mediante la API oficial de Meta, configurar su catálogo
de productos/servicios y activar un agente de IA que atiende, vende y hace seguimiento
de forma autónoma — con supervisión humana cuando se necesita. Reemplaza el modelo
artesanal de un flujo n8n por cliente: en Leo, un cliente nuevo es solo configuración.

Usuarios: el **cliente/tenant** (dueño del negocio: configura su agente, supervisa su
bandeja, ve sus métricas), sus **operadores** (empleados que atienden la bandeja) y el
**admin de plataforma** (Giannier: tenants, planes, tools, monitoreo global).

## Principios Fundamentales
- **Configuración sobre código:** Onboarding de un cliente = registros en BD (tenant,
  número, catálogo, tools activas, prompt base, plan). Un rubro nuevo nunca requiere código.
- **Aislamiento multi-tenant estricto:** Toda tabla de negocio lleva `tenant_id` y toda
  query lo filtra en la capa de repositorio. Verificado por tests automáticos.
- **Proveedores intercambiables:** WhatsApp (YCloud → Meta Cloud API), LLM (multi-modelo)
  y pagos se consumen solo vía interfaces propias (`WhatsAppProvider`, `LLMProvider`,
  `PaymentProvider`). Prohibido llamar SDKs de terceros fuera de su adaptador.
- **Agente núcleo + tools modulares:** Un solo motor de agente; capacidades como tools
  registradas y activables por tenant. Nueva tool = nuevo módulo, núcleo intacto.
- **El humano siempre puede tomar el control:** Toda conversación es visible en la bandeja
  del tenant; al intervenir un humano, el agente se pausa instantáneamente en esa
  conversación y solo se reactiva de forma explícita o por tiempo configurable.
- **El agente no inventa:** Precios, stock y condiciones salen exclusivamente del catálogo
  y la base de conocimiento del tenant. Si no sabe o detecta frustración, escala a humano.
  Nunca confirma un pago sin validación.
- **Nunca fallar en silencio:** Límites de plan alcanzados, webhooks caídos o errores de
  proveedor generan alertas visibles (al tenant y/o al admin) y degradación con gracia,
  jamás conversaciones perdidas sin aviso.
- **Compliance WhatsApp como requisito, no opción:** Ventana de 24h respetada, mensajes
  proactivos solo con plantillas aprobadas y opt-in registrado, opt-out honrado al
  instante, monitoreo del quality rating de cada número.
- **Simplicidad operativa (equipo de 1):** Monolito modular, un despliegue, infraestructura
  mínima operable por una persona. Sin microservicios ni Kubernetes.

## Stack Tecnológico
- **Lenguaje:** Python 3.12 (type hints obligatorios)
- **Framework backend:** FastAPI (async para webhooks concurrentes)
- **Base de datos:** PostgreSQL 16 + pgvector (datos + embeddings en una sola BD)
- **ORM/Migraciones:** SQLAlchemy 2.x + Alembic
- **Cache y colas:** Redis + workers async (todo mensaje entrante se procesa en cola,
  con reintentos y dead-letter; el webhook responde 200 de inmediato)
- **LLM:** Adaptador multi-proveedor; modelo configurable por tenant/tarea/plan.
  Inicial: modelo económico con visión y tool use; modelo superior para planes premium
- **Storage de archivos:** S3-compatible (MinIO en el VPS) para PDFs, imágenes, comprobantes
- **Frontend:** Next.js 15 + React + Tailwind CSS
- **Pagos del SaaS:** Adaptador `PaymentProvider` (inicial: Mercado Pago/Culqi; Stripe después)
- **Testing:** pytest + pytest-asyncio; evals de agente con dataset propio
- **Observabilidad:** logs estructurados (JSON), trazas de agente por conversación,
  Sentry (errores), Uptime Kuma (monitoreo), backups automáticos diarios de PostgreSQL
- **Hosting/Deploy:** VPS Contabo, Docker Compose, Caddy (HTTPS automático), GitHub Actions

## Módulos del Sistema
**MVP (v1):**
- `01-tenants-auth`: registro, autenticación (JWT + refresh), roles (admin, tenant, operador), planes
- `02-channels`: abstracción WhatsApp, conexión de números (YCloud), webhooks, estado y quality del número
- `03-agent-core`: orquestación LLM, loop de tools, memoria conversacional, guardrails, trazas
- `04-tools`: catálogo de tools — enviar media (imagen/PDF/sticker/audio), consultar catálogo
  y stock, RAG, validación de comprobantes por visión, escalar a humano
- `05-catalog`: productos/servicios, precios, stock, info de negocio (horarios, delivery, pagos)
- `06-knowledge`: ingesta de documentos (PDF, imágenes, texto) y RAG con pgvector
- `07-conversations`: historial, estados, ventana 24h, contactos y opt-in/opt-out
- `08-inbox`: bandeja en vivo del tenant, takeover humano instantáneo, notas, reactivación del agente
- `09-billing`: planes, suscripciones, medición de consumo (mensajes/tokens), límites con alertas, pagos
- `10-dashboard`: panel del tenant (configuración, métricas básicas) + panel admin de plataforma

**Post-MVP (v2):**
- `11-campaigns`: plantillas de WhatsApp, campañas/broadcasts segmentados con opt-in, follow-ups
  automáticos de conversaciones frías
- `12-crm`: pipeline de leads (etapas configurables), calificación automática por el agente
- `13-analytics`: métricas avanzadas (conversión, ventas atribuidas, CSAT, tiempos de respuesta)
- `14-scheduling`: agendamiento de citas como tool (rubros de servicios: clínicas, barberías, institutos)
- `15-integrations`: webhooks salientes y API pública para conectar CRMs/ERPs de clientes

## Estándares de Calidad
- **Cobertura de tests:** 80% mínimo en módulos críticos (`agent-core`, `tools`, `channels`,
  `billing`); 70% en el resto de lógica de negocio
- **Evals del agente:** dataset de conversaciones doradas por rubro (restaurante, instituto,
  retail) ejecutado en CI ante cualquier cambio de prompt, tools o modelo; sin regresiones
- **Tests de aislamiento multi-tenant:** suite específica que intenta acceso cruzado entre
  tenants; obligatoria en CI
- **Contract tests** de los adaptadores de proveedor (mocks de YCloud/Meta/LLM/pagos)
- **Linter/Formatter:** Ruff; **Tipado:** mypy en CI; **Commits:** Conventional Commits
- **CI/CD:** nada llega a producción sin pasar lint, mypy, tests y evals

## Seguridad
- Aislamiento por `tenant_id` en capa de repositorio, verificado por tests
- Credenciales de proveedores cifradas en reposo; secretos fuera del repo (env/vault)
- Webhooks entrantes verificados por firma (HMAC) y con idempotencia (no procesar duplicados)
- Rate limiting por tenant en API y en envío de mensajes
- JWT + refresh tokens, HTTPS en todo, CORS restringido
- Comprobantes de pago e imágenes en storage privado, accesibles solo por su tenant
- Datos personales bajo Ley 29733 (Perú): minimización, exportación y eliminación por tenant
- Backups diarios cifrados con restauración probada (no solo backups: restore test mensual)

## Restricciones para la IA
- No agregar dependencias sin aprobación explícita
- No modificar esquema de BD sin migración Alembic revisable
- No tocar billing, credenciales ni verificación de webhooks sin revisión humana
- No romper las interfaces `WhatsAppProvider` / `LLMProvider` / `PaymentProvider`
- No cambiar prompts del agente en producción sin correr los evals
