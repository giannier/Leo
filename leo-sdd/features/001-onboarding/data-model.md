# Data Model — Onboarding de Tenant + Conexión de Número

**Feature ID:** 001
Convenciones: PK `id UUID DEFAULT gen_random_uuid()`; `created_at/updated_at TIMESTAMPTZ DEFAULT NOW()`; toda tabla de negocio lleva `tenant_id` NOT NULL con índice; soft delete solo donde se indica. Enums como `TEXT` + `CHECK` (más simples de migrar que enums nativos).

## Entidades

### plans
```sql
CREATE TABLE plans (
  id                  TEXT PRIMARY KEY,            -- 'trial' | 'emprende' | 'crece' | 'pro'
  name                TEXT NOT NULL,
  max_seats           INT  NOT NULL,               -- trial: 2
  max_ai_messages     INT  NOT NULL,               -- trial: 250 (por período)
  max_rag_chars       BIGINT NOT NULL,             -- trial: 500_000
  max_knowledge_files INT  NOT NULL,               -- trial: 20
  trial_days          INT,                         -- solo para 'trial': 14
  price_usd_month     NUMERIC(8,2),                -- referencia; billing en Feature 009
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### users
```sql
CREATE TABLE users (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email              CITEXT NOT NULL UNIQUE,
  password_hash      TEXT NOT NULL,
  full_name          TEXT NOT NULL,
  phone_e164         TEXT,                          -- teléfono de contacto
  phone_verified_at  TIMESTAMPTZ,
  email_verified_at  TIMESTAMPTZ,
  is_platform_admin  BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### tenants
```sql
CREATE TABLE tenants (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name               TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'trial'
                     CHECK (status IN ('trial','active','paused','archived')),
  plan_id            TEXT NOT NULL REFERENCES plans(id) DEFAULT 'trial',
  trial_ends_at      TIMESTAMPTZ,                   -- registro + 14 días
  ai_messages_used   INT NOT NULL DEFAULT 0,        -- solo canal 'whatsapp'
  paused_at          TIMESTAMPTZ,
  archived_at        TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- Anti-abuso (Clarification 005): un teléfono verificado, un solo trial vivo.
-- Se implementa como índice único parcial sobre el owner en tenant_users + users.phone
-- vía vista materializada de chequeo en servicio (validación de aplicación) +
-- constraint auxiliar:
CREATE UNIQUE INDEX uq_trial_owner_phone ON tenants (plan_id, (
  SELECT u.phone_e164 FROM tenant_users tu JOIN users u ON u.id = tu.user_id
  WHERE tu.tenant_id = tenants.id AND tu.role='owner' LIMIT 1
)) WHERE status = 'trial';
-- NOTA IMPLEMENTACIÓN: Postgres no permite subqueries en índices; este intento se
-- reemplaza por validación en el servicio de registro + tabla trial_phone_locks:
CREATE TABLE trial_phone_locks (
  phone_e164 TEXT PRIMARY KEY,
  tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### tenant_users
```sql
CREATE TABLE tenant_users (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES tenants(id),
  user_id    UUID NOT NULL REFERENCES users(id),
  role       TEXT NOT NULL CHECK (role IN ('owner','tenant_admin','operator')),
  status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, user_id)
);
CREATE INDEX idx_tenant_users_tenant ON tenant_users(tenant_id);
```

### invitations
```sql
CREATE TABLE invitations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id),
  email       CITEXT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('tenant_admin','operator')),
  token_hash  TEXT NOT NULL UNIQUE,
  expires_at  TIMESTAMPTZ NOT NULL,                 -- +7 días
  accepted_at TIMESTAMPTZ,
  revoked_at  TIMESTAMPTZ,
  invited_by  UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_invitations_tenant ON invitations(tenant_id);
```

### otp_codes / email_tokens / refresh_tokens
```sql
CREATE TABLE otp_codes (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES users(id),
  purpose    TEXT NOT NULL CHECK (purpose IN ('phone_verify')),
  code_hash  TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,                  -- 10 min
  consumed_at TIMESTAMPTZ,
  attempts   INT NOT NULL DEFAULT 0,                -- máx 5
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE email_tokens (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES users(id),
  purpose    TEXT NOT NULL CHECK (purpose IN ('verify_email','reset_password')),
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TIMESTAMPTZ NOT NULL,                  -- 24h
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE refresh_tokens (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id),
  family_id   UUID NOT NULL,                        -- cadena de rotación (migración 0004)
  token_hash  TEXT NOT NULL UNIQUE,
  expires_at  TIMESTAMPTZ NOT NULL,                 -- 30 días, rotativo
  revoked_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id);
CREATE INDEX ix_refresh_tokens_family_id ON refresh_tokens(family_id);
```

**`family_id` (añadido en TASK-007, migración 0004).** Cada uso del refresh token
lo revoca y emite un sucesor, así que un inicio de sesión deja una cadena de filas:
una viva y el resto revocadas. `family_id` se acuña en el login y se copia a cada
sucesor, de modo que la cadena es identificable.

Existe para hacer **detectable el robo**. Un token robado es indistinguible del
original; la señal no es el token sino el *uso duplicado*: quien rote en segundo
lugar presenta una fila ya revocada. En ese punto no hay forma de distinguir a la
víctima del ladrón, así que se revoca la familia completa y ambos vuelven a pasar
por la contraseña. Sin `family_id` la alternativa sería no revocar nada (el robo
pasa inadvertido) o revocar todas las sesiones del usuario, incluidas las de
dispositivos que nunca estuvieron involucrados.

### business_profiles
```sql
CREATE TABLE business_profiles (
  tenant_id     UUID PRIMARY KEY REFERENCES tenants(id),
  business_name TEXT,
  industry      TEXT CHECK (industry IN
                ('restaurante','instituto','retail','servicios','salud','otros')),
  description   TEXT,                               -- "cuéntale a Leo tu negocio"
  website       TEXT,
  social_links  JSONB NOT NULL DEFAULT '{}',
  currency      TEXT NOT NULL DEFAULT 'PEN',
  timezone      TEXT NOT NULL DEFAULT 'America/Lima',
  opening_hours JSONB NOT NULL DEFAULT '{}',        -- {mon:[["09:00","18:00"]],...}
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### agent_configs
```sql
CREATE TABLE agent_configs (
  tenant_id        UUID PRIMARY KEY REFERENCES tenants(id),
  agent_name       TEXT NOT NULL DEFAULT 'Leo',
  tone             TEXT NOT NULL DEFAULT 'cercano'
                   CHECK (tone IN ('profesional','cercano','juvenil')),
  objective        TEXT NOT NULL DEFAULT 'ambos'
                   CHECK (objective IN ('vender','atender','ambos')),
  restrictions     TEXT,                            -- libre: "no ofrecer descuentos"
  escalation_rules JSONB NOT NULL DEFAULT
                   '["cliente_molesto","pide_humano","fuera_de_catalogo"]',
  farewell_on_pause TEXT,                           -- despedida al agotar trial (Clarif. 004)
  is_active        BOOLEAN NOT NULL DEFAULT FALSE,  -- switch global ON/OFF
  activated_first_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### products
```sql
CREATE TABLE products (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id),
  name        TEXT NOT NULL,
  description TEXT,
  price       NUMERIC(12,2) NOT NULL CHECK (price >= 0),
  stock       INT,                                  -- NULL = no controla stock
  category    TEXT,
  image_key   TEXT,                                 -- key en MinIO
  file_key    TEXT,                                 -- PDF asociado (menú, malla)
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
  deleted_at  TIMESTAMPTZ                           -- soft delete
);
CREATE INDEX idx_products_tenant ON products(tenant_id) WHERE deleted_at IS NULL;
```

### payment_methods_business
```sql
CREATE TABLE payment_methods_business (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id),
  method       TEXT NOT NULL CHECK (method IN
               ('yape','plin','transferencia','tarjeta','efectivo','link')),
  instructions TEXT NOT NULL,                       -- "Yape al 9XXXXXXXX a nombre de..."
  validate_receipts BOOLEAN NOT NULL DEFAULT FALSE, -- activa tool de visión (Feature 004)
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, method)
);
```

### knowledge_sources / knowledge_chunks
```sql
CREATE TABLE knowledge_sources (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id),
  type        TEXT NOT NULL CHECK (type IN ('faq','policy','pdf','text')),
  title       TEXT NOT NULL,
  content     TEXT,                                 -- faq/policy/text
  file_key    TEXT,                                 -- pdf en MinIO
  char_count  INT NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','indexing','indexed','failed')),
  error_msg   TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
  deleted_at  TIMESTAMPTZ
);
CREATE INDEX idx_ksources_tenant ON knowledge_sources(tenant_id) WHERE deleted_at IS NULL;

CREATE TABLE knowledge_chunks (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES tenants(id),
  source_id  UUID NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  embedding  VECTOR(1536),                          -- dimensión según LLMProvider.embed
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_kchunks_tenant ON knowledge_chunks(tenant_id);
CREATE INDEX idx_kchunks_embedding ON knowledge_chunks
  USING hnsw (embedding vector_cosine_ops);
```

### whatsapp_numbers / connection_requests
```sql
CREATE TABLE whatsapp_numbers (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES tenants(id),
  phone_e164     TEXT NOT NULL UNIQUE,              -- unicidad global (RF-008)
  display_name   TEXT NOT NULL,
  provider       TEXT NOT NULL DEFAULT 'ycloud' CHECK (provider IN ('ycloud','meta')),
  credentials_enc BYTEA,                            -- Fernet: IDs/tokens del proveedor (RF-009)
  status         TEXT NOT NULL DEFAULT 'connected'
                 CHECK (status IN ('connected','disconnected')),
  quality_rating TEXT,                              -- se poblará en Feature 002
  connected_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE connection_requests (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           UUID NOT NULL REFERENCES tenants(id),
  phone_e164          TEXT NOT NULL,
  display_name        TEXT NOT NULL,
  profile_photo_key   TEXT,
  business_description TEXT,
  status              TEXT NOT NULL DEFAULT 'requested'
                      CHECK (status IN ('requested','in_progress','connected','rejected')),
  rejection_reason    TEXT,
  admin_notes         TEXT,
  previous_request_id UUID REFERENCES connection_requests(id),  -- reintento (Clarif. 012)
  processed_by        UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_open_request_phone ON connection_requests(phone_e164)
  WHERE status IN ('requested','in_progress');
CREATE INDEX idx_conn_req_status ON connection_requests(status);
```

### conversations / messages / agent_traces (compartidas con Features 002/007)
```sql
CREATE TABLE conversations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id),
  channel     TEXT NOT NULL CHECK (channel IN ('sandbox','whatsapp')),
  contact_id  UUID,                                 -- NULL en sandbox; FK en Feature 007
  started_by  UUID REFERENCES users(id),            -- usuario del panel (sandbox)
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_conversations_tenant ON conversations(tenant_id, channel);

CREATE TABLE messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  direction       TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
  author          TEXT NOT NULL CHECK (author IN ('customer','agent','human_operator','system')),
  content         TEXT NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);

CREATE TABLE agent_traces (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  message_id      UUID NOT NULL REFERENCES messages(id),
  model           TEXT NOT NULL,
  tools_called    JSONB NOT NULL DEFAULT '[]',      -- [{tool, args_resumen, ok, cached}]
  rag_sources     JSONB NOT NULL DEFAULT '[]',      -- [{source_id, chunk_id, score}]
  input_tokens    INT NOT NULL DEFAULT 0,
  output_tokens   INT NOT NULL DEFAULT 0,
  latency_ms      INT,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_traces_tenant ON agent_traces(tenant_id, created_at);
```

### audit_logs
```sql
CREATE TABLE audit_logs (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_id   UUID NOT NULL REFERENCES users(id),
  tenant_id  UUID REFERENCES tenants(id),
  action     TEXT NOT NULL,   -- 'connection_request.connected','trial.extended', etc.
  detail     JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_tenant ON audit_logs(tenant_id, created_at);
```

## Diagrama de Relaciones (texto)
```
plans 1──N tenants 1──N tenant_users N──1 users 1──N refresh_tokens/otp_codes/email_tokens
tenants 1──1 business_profiles / agent_configs
tenants 1──N products / payment_methods_business / knowledge_sources / invitations
knowledge_sources 1──N knowledge_chunks
tenants 1──1 whatsapp_numbers   tenants 1──N connection_requests (auto-ref previous_request_id)
tenants 1──N conversations 1──N messages 1──1 agent_traces
users(admin) 1──N audit_logs
```

## Notas de Migración
- Migración 0001: extensiones (`pgcrypto` opcional para gen_random_uuid en PG16 ya nativo, `vector`, `citext`) + `plans` con seed de los 4 planes.
- Orden: plans → users → tenants → tenant_users → resto (respetar FKs).
- La nota dentro de `tenants` sobre el índice imposible es intencional: documenta por qué existe `trial_phone_locks`; no intentar crear ese índice.
- `conversations/messages/agent_traces` nacen aquí pero son compartidas: las Features 002/007 solo agregan columnas vía nuevas migraciones (nunca renombran las existentes).
