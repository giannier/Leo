# API Contracts — Onboarding de Tenant + Conexión de Número

**Feature ID:** 001
**Base URL:** `/api/v1`
**Auth:** Bearer JWT access token (`Authorization: Bearer {token}`); refresh en cookie httpOnly.
**Convenciones:** errores como `{ "error": "codigo_estable", "message": "texto humano", "fields": {...}? }`. Todos los endpoints de `/tenant/*` resuelven el tenant desde el JWT (claim `tenant_id`) y aplican rol. Paginación: `page`, `limit` (default 20, max 100).

---

## Auth — `/auth`

### POST /auth/register
Crea usuario + tenant `trial` (transacción). Roles: público.
```json
// Request
{ "full_name": "string", "email": "string", "password": "string(min 10)",
  "phone_e164": "+519XXXXXXXX", "country": "PE", "business_name": "string" }
// 201
{ "user_id": "uuid", "tenant_id": "uuid", "verification_email_sent": true }
```
Errores: `409 email_taken`, `409 phone_in_active_trial` (Clarification 005), `400 validation_error`.

### POST /auth/verify-email        `{ "token": "string" }` → `204`
### POST /auth/resend-verification `{ "email": "string" }` → `204` (siempre 204, no filtra existencia)
### POST /auth/login
```json
{ "email": "string", "password": "string" }
// 200 → set-cookie refresh
{ "access_token": "jwt", "user": { "id":"uuid","full_name":"...","email":"..." },
  "memberships": [ { "tenant_id":"uuid","tenant_name":"...","role":"owner" } ] }
```
Errores: `401 invalid_credentials`, `403 email_not_verified`. Rate limit 5/min/IP.
### POST /auth/refresh → rota refresh, devuelve nuevo access. `401 invalid_refresh`.
### POST /auth/logout → revoca refresh. `204`.
### POST /auth/phone/request-otp → envía OTP al `phone_e164` del usuario. `204`. Rate limit 3/h.
### POST /auth/phone/verify-otp   `{ "code": "123456" }` → `204`. Errores: `400 invalid_or_expired_otp` (máx 5 intentos).

---

## Tenant: estado y checklist — `/tenant`

### GET /tenant
Roles: cualquier miembro.
```json
{ "id":"uuid","name":"...","status":"trial","plan":"trial",
  "trial": { "ends_at":"ISO8601","ai_messages_used":37,"ai_messages_limit":250,"days_left":9 } }
```

### GET /tenant/activation-checklist
Roles: owner, tenant_admin. Cálculo en tiempo real (Clarification 009).
```json
{ "items": [
    { "key":"email_verified","done":true },
    { "key":"business_profile_complete","done":true },
    { "key":"agent_config_complete","done":true },
    { "key":"has_active_product","done":false },
    { "key":"has_payment_method","done":false,"required":true },
    { "key":"phone_verified","done":false } ],
  "sandbox_ready": false, "activation_ready": false }
```

### POST /tenant/agent/activate  → `204` | `409 checklist_incomplete` | `409 number_not_connected` | `409 tenant_not_active`
`tenant_not_active` se agrego en TASK-019: un tenant `paused` o `archived` no puede activar.
Aceptarlo dejaria el panel mostrando un agente activo que no responde a nadie, porque es la
pausa la que lo detiene.
### POST /tenant/agent/deactivate → `204`
Roles: owner, tenant_admin.

---

## Wizard — `/tenant/*` (roles: owner, tenant_admin)

### GET|PUT /tenant/business-profile
PUT body = campos de `business_profiles`; respuesta incluye `updated_at` (aviso de concurrencia, Clarification 008).

### GET|PUT /tenant/agent-config
PUT body = campos de `agent_configs` (sin `is_active`, que va por `/agent/activate`).

### Productos
- `GET /tenant/products` (query: `search`, `category`, `is_active`) → paginado.
- `POST /tenant/products` → `201` producto.
- `GET|PUT|DELETE /tenant/products/:id` (DELETE = soft). `404 not_found` si es de otro tenant (nunca 403: no filtrar existencia).
- `POST /tenant/products/import` (multipart CSV/XLSX) → `200 { "preview_id":"uuid","valid_rows":48,"invalid_rows":[{"row":3,"reason":"precio_invalido"}] }`
- `POST /tenant/products/import/:preview_id/confirm` → `201 { "created":48 }`. Preview expira en 1h.

### Conocimiento
- `GET /tenant/knowledge` → lista de sources con `status` y `char_count` + `usage: { chars_used, chars_limit, files_used, files_limit }`.
- `POST /tenant/knowledge` (JSON para faq/policy/text; multipart para pdf) → `202 { "id":"uuid","status":"pending" }` (indexa en worker).
- `DELETE /tenant/knowledge/:id` → `204` (borra chunks en cascada).
Errores: `413 rag_limit_exceeded` con uso actual (Clarification 011), `422 pdf_without_text`.

### Métodos de pago del negocio
- `GET /tenant/payment-methods` · `POST /tenant/payment-methods` · `PUT|DELETE /tenant/payment-methods/:id`. `409 method_already_exists`.

---

## Sandbox — `/tenant/sandbox` (roles: owner, tenant_admin, operator)

### POST /tenant/sandbox/conversations → `201 { "conversation_id":"uuid" }` | `409 sandbox_not_ready`
### POST /tenant/sandbox/conversations/:id/messages
```json
{ "content": "hola, ¿tienen menú del día?" }
// 200 (sin streaming en v1)
{ "message_id":"uuid",
  "reply": { "message_id":"uuid","content":"¡Hola! Sí..." },
  "trace": { "model":"...","tools_called":[{"tool":"consultar_catalogo","ok":true}],
             "rag_sources":[{"source_id":"uuid","score":0.87}],
             "input_tokens":812,"output_tokens":96,"latency_ms":2140 } }
```
Errores: `429 rate_limited` (30/min), `409 sandbox_not_ready`.
### GET /tenant/sandbox/conversations/:id/messages → historial paginado.

---

## Conexión de número — `/tenant/connection-requests` (roles: owner, tenant_admin)

### POST /tenant/connection-requests
Precondición: `phone_verified`. Body: `{ "phone_e164","display_name","business_description","profile_photo_key"? }`
→ `201` solicitud | `409 phone_already_connected` | `409 open_request_exists` | `403 phone_not_verified`.
### GET /tenant/connection-requests → historial con estados.
### GET /tenant/whatsapp-number → número conectado o `404 no_number`.

---

## Equipo — `/tenant/team` (roles: owner, tenant_admin; DELETE de owner: solo owner)

- `GET /tenant/team` → miembros + invitaciones pendientes.
- `POST /tenant/team/invitations` `{ "email","role" }` → `201` | `409 seat_limit_reached` | `409 already_member`.
- `POST /tenant/team/invitations/:id/resend` → `204`.
- `DELETE /tenant/team/invitations/:id` → `204` (revoca).
- `POST /auth/invitations/accept` (público) `{ "token","full_name"?,"password"? }` → `200` login. `410 invitation_expired`.
- `PUT /tenant/team/members/:id` `{ "role" | "status" }` → `200`. Al `disabled`: revoca refresh tokens.

---

## Admin de plataforma — `/platform-admin` (rol: `is_platform_admin`)

- `GET /platform-admin/connection-requests?status=requested` → cola de trabajo.
- `PUT /platform-admin/connection-requests/:id`
```json
{ "status":"in_progress" }
// o al completar:
{ "status":"connected", "credentials": { "provider":"ycloud", "...": "ids del número" } }
// o
{ "status":"rejected", "rejection_reason":"El número tiene una WABA activa en otra plataforma" }
```
→ `200`. Al `connected`: crea `whatsapp_numbers` con credenciales cifradas + notifica al tenant + audit log.
- `GET /platform-admin/tenants` (búsqueda/estado) · `GET /platform-admin/tenants/:id` (detalle + uso).
- `POST /platform-admin/tenants/:id/extend-trial` `{ "days":7,"reason":"demora de conexión" }` → `200` (audit log; Clarification 012).

---

## Jobs programados (no HTTP, workers ARQ)
- `trial_notifier` (diario): emails día −3/−1/0.
- `trial_enforcer` (cada hora): `trial` vencido o cuota agotada → `paused` (+ cierre con gracia de conversación activa).
- `tenant_archiver` (diario): `paused` > 30 días → `archived`.
- `wizard_reminder` (diario): checklist incompleto + 24h sin actividad → email único.
- `knowledge_indexer` (on-demand): source `pending` → chunk + embed → `indexed|failed`.
