# Data Model — Motor de ventas (004 v2)

Migraciones `0014`–`0020`. El SQL es la **intención**: Claude Code lo traduce a modelos SQLAlchemy y
migraciones Alembic siguiendo las convenciones del repo (tipo de PK, nombres de índices, `TimestampMixin`,
`TenantScoped`, etc.). Donde el repo ya resuelve algo distinto, manda el repo y se anota en `conciliacion.md` (TASK-401).

Reglas comunes a todas las tablas nuevas:
- `tenant_id NOT NULL` con FK a `tenants` y `ON DELETE CASCADE`.
- Todas entran en `backend/tests/isolation` (lectura, escritura y listado cruzados entre tenants).
- `created_at` / `updated_at` con `timestamptz`.

---

## 0014 — Estado de la conversación

```sql
CREATE TABLE conversation_state (
    conversation_id   uuid PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    template_key      text NOT NULL,                 -- productos | certificaciones | servicios
    template_version  integer NOT NULL,
    stage_key         text NOT NULL,                 -- ver plantillas.md
    funnel_code       text NOT NULL,                 -- NUEVO | ATENDIDO | NEGOCIACION | CUENTAS_ENVIADAS
                                                     -- EN_VERIFICACION | GANADO | DERIVADO | PERDIDO
    facts             jsonb NOT NULL DEFAULT '{}',   -- solo hechos GUARDADOS (C-03)
    is_sandbox        boolean NOT NULL DEFAULT false,
    version           integer NOT NULL DEFAULT 1,    -- concurrencia optimista
    stage_entered_at  timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_funnel_code CHECK (funnel_code IN ('NUEVO','ATENDIDO','NEGOCIACION','CUENTAS_ENVIADAS',
                                                     'EN_VERIFICACION','GANADO','DERIVADO','PERDIDO'))
);
CREATE INDEX ix_conv_state_tenant_stage  ON conversation_state (tenant_id, stage_key);
CREATE INDEX ix_conv_state_tenant_funnel ON conversation_state (tenant_id, funnel_code);

CREATE TABLE conversation_stage_events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id  uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    from_stage       text,
    to_stage         text NOT NULL,
    from_funnel      text,
    to_funnel        text NOT NULL,
    reason           text NOT NULL,   -- turno | herramienta:<nombre> | comprobante | operador:confirmar
                                      -- operador:rechazar | operador:estado | operador:reanudar
                                      -- pedido_descartado | seguimiento | pausa_vencida | reinicio_sandbox
    facts_snapshot   jsonb,           -- hechos (guardados + derivados) SIN datos personales
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_stage_events_conv   ON conversation_stage_events (conversation_id, created_at);
CREATE INDEX ix_stage_events_tenant ON conversation_stage_events (tenant_id, created_at);
```

Solo se inserta un evento cuando cambia `stage_key` o `funnel_code` (igual que `registrar_evento` en `control`).
Todo lo que cambia hechos fuera de un turno (confirmar, rechazar, cambiar estado, descartar borradores,
reanudar bot, vencer pausa) recalcula la etapa con `StateRepo.save` y deja su `reason`.

### Hechos guardados (`conversation_state.facts`)

```jsonc
{
  "saludo_enviado": true,
  "entrada_anuncio": { "ad_id": "120211…", "source_url": "https://fb.me/…", "headline": "5 certificaciones x S/ 500",
                       "body": "…", "recibido_en": "2026-10-10T15:02:00Z" },
  "camino": "certificacion",                         // clave de calificación o null
  "vio_productos": true,                             // buscar/ver producto con resultados o lista de productos
  "recursos_enviados": ["flyer_paquetes", "saludo"],  // claves de media_assets
  "opciones_mostradas": ["caminos", "categorias", "productos:cat-12"],
  "metodos_pago_enviados": { "order_id": "…", "en": "2026-10-10T16:10:00Z" },   // por pedido
  "derivado": { "motivo": "pide_humano", "resumen": "…", "hasta": "2026-10-10T21:00:00Z" },
  "asesoria_derivada_en": "2026-10-10T15:20:00Z",    // no se borra al vencer la pausa
  "seguimiento": { "intentos": 1, "ultimo_en": "2026-10-10T19:00:00Z" },
  "promos_ofrecidas": { "<promotion_id>": "2026-10-11T19:00:00Z" }   // vence
}
```

### Hechos derivados (se calculan cada turno, no se guardan)

```jsonc
{
  "nombre_cliente": "Ana",                 // custom_data.nombres; si no hay, nombre de perfil marcado "sin confirmar"
  "nombre_confirmado": true,
  "pedido": { "id": "…", "numero": 42, "estado": "borrador", "completo": false,
              "faltan_selecciones": 3, "total": "500.00", "moneda": "PEN",
              "items": [{ "nombre": "Paquete 5 certificaciones", "cantidad": 1, "selecciones": ["SIAF", "Gestión pública"] }] },
  "datos_completos": false,
  "datos_faltantes": ["dni", "correo"],     // incluye tipo_entrega / direccion / zona_envio si aplica entrega
  "requiere_entrega": false,
  "pago_estado": null,                      // null | por_verificar | rechazado | confirmado
  "modalidad_pago": "previo",               // copia de orders.modalidad_pago
  "metodos_pago_enviados": false,           // true si se enviaron para el pedido activo y después del último rechazo
  "motivo_rechazo": null,                   // orders.motivo_rechazo del pedido activo (comprobante o contra entrega)
  "comprobante": { "observaciones": ["monto_distinto"] },
  "promos_cierre": [{ "id": "…", "nombre": "Cupón 20 %", "vigencia_horas": 24, "ofrecida_vence": null }]
}
```

---

## 0015 — Información del negocio, FAQ y promociones

```sql
CREATE TABLE business_info (
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    section     text NOT NULL,
    data        jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, section),
    CONSTRAINT ck_business_info_section CHECK (section IN
        ('locales','entregas','tipos_cliente','devoluciones','contacto'))
);

CREATE TABLE faqs (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    pregunta             text NOT NULL CHECK (char_length(pregunta) BETWEEN 3 AND 300),
    respuesta            text NOT NULL CHECK (char_length(respuesta) BETWEEN 1 AND 2000),
    orden                integer NOT NULL DEFAULT 0,
    is_active            boolean NOT NULL DEFAULT true,
    knowledge_source_id  uuid REFERENCES knowledge_sources(id) ON DELETE SET NULL,  -- indexada para consultar_conocimiento
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_faqs_tenant ON faqs (tenant_id, is_active, orden);

CREATE TABLE promotions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    nombre          text NOT NULL,
    descripcion     text,                          -- lo que el asistente puede decir
    uso             text NOT NULL DEFAULT 'automatica' CHECK (uso IN ('automatica','cierre')),
    tipo            text NOT NULL CHECK (tipo IN ('porcentaje','monto_fijo','nxm','precio_cantidad')),
    valor           numeric(12,2),                 -- % o monto; para precio_cantidad = precio total del grupo
    n               integer,                       -- nxm: lleva n
    m               integer,                       -- nxm: paga m   | precio_cantidad: cantidad del grupo
    aplica_a        text NOT NULL DEFAULT 'todo' CHECK (aplica_a IN ('todo','productos','categorias')),
    product_ids     uuid[] NOT NULL DEFAULT '{}',
    categorias      text[] NOT NULL DEFAULT '{}',
    monto_minimo    numeric(12,2),
    vigencia_horas  integer,                       -- solo uso=cierre: horas desde que se ofrece
    para_seguimiento boolean NOT NULL DEFAULT false, -- puede ofrecerse en el último seguimiento
    inicia_en       timestamptz,
    termina_en      timestamptz,
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    -- Ojo: un CHECK con NULL pasa; por eso cada regla exige NOT NULL explícito.
    CONSTRAINT ck_promo_nxm CHECK (tipo <> 'nxm' OR (n IS NOT NULL AND m IS NOT NULL AND n > m AND m >= 1)),
    CONSTRAINT ck_promo_porcentaje CHECK (tipo <> 'porcentaje' OR (valor IS NOT NULL AND valor > 0 AND valor <= 100)),
    CONSTRAINT ck_promo_monto CHECK (tipo <> 'monto_fijo' OR (valor IS NOT NULL AND valor > 0)),
    CONSTRAINT ck_promo_precio_cantidad CHECK (tipo <> 'precio_cantidad' OR (valor IS NOT NULL AND valor > 0 AND m IS NOT NULL AND m >= 2)),
    CONSTRAINT ck_promo_cierre CHECK (uso <> 'cierre' OR (vigencia_horas IS NOT NULL AND vigencia_horas BETWEEN 1 AND 168))
);
CREATE INDEX ix_promotions_tenant_active ON promotions (tenant_id, is_active);

-- QR o imagen de cuentas por método de pago (opcional)
ALTER TABLE payment_methods_business ADD COLUMN media_asset_id uuid NULL;  -- FK se agrega en 0017
```

### Esquemas de `business_info.data` (Pydantic, validados en la API)

```python
class Local(BaseModel):
    nombre: str; direccion: str; referencia: str | None; maps_url: HttpUrl | None
    horarios: list[Horario]          # reutiliza el tipo de horarios de business_profiles

class Locales(BaseModel):            # section = 'locales'
    atiende_presencial: bool; locales: list[Local] = Field(max_length=20)

class Zona(BaseModel):
    nombre: str; costo: Decimal; tiempo: str          # "24 a 48 h"
class Entregas(BaseModel):           # section = 'entregas' — ÚNICA fuente de la entrega (no hay opción en la plantilla)
    hace_delivery: bool; zonas: list[Zona] = Field(max_length=50)   # zonas vacías = costo "a coordinar" (envío 0)
    envio_gratis_desde: Decimal | None; recojo_en_tienda: bool; notas: str | None = Field(max_length=500)
    # Validación: si recojo_en_tienda=True, Locales debe tener al menos un local.

class TipoCliente(BaseModel):
    nombre: str; como_reconocerlo: str; trato: str   # "Mayorista: pide más de 12 unidades → precio por mayor"
class TiposCliente(BaseModel):       # section = 'tipos_cliente'
    tipos: list[TipoCliente] = Field(max_length=6)

class Devoluciones(BaseModel):       # section = 'devoluciones'
    acepta_cambios: bool; acepta_devoluciones: bool; plazo_dias: int | None
    condiciones: str | None = Field(max_length=800)

class Contacto(BaseModel):           # section = 'contacto'
    telefono_humano: str | None; correo: EmailStr | None; web: HttpUrl | None
    redes: dict[Literal['instagram','facebook','tiktok','youtube','linkedin'], HttpUrl] = {}
```

---

## 0016 — Comportamiento y proceso de venta

```sql
-- Comportamiento (se reutilizan agent_name, tone, restrictions = "reglas adicionales", is_active)
ALTER TABLE agent_configs
    ADD COLUMN ritmo                  text    NOT NULL DEFAULT 'equilibrado'
        CHECK (ritmo IN ('directo','equilibrado','paso_a_paso')),
    ADD COLUMN detalle                text    NOT NULL DEFAULT 'moderado'
        CHECK (detalle IN ('breve','moderado','extenso')),
    ADD COLUMN seguimiento_activo     boolean NOT NULL DEFAULT false,
    ADD COLUMN seguimiento_horas      integer NOT NULL DEFAULT 4  CHECK (seguimiento_horas BETWEEN 1 AND 20),
    ADD COLUMN seguimiento_max        integer NOT NULL DEFAULT 2  CHECK (seguimiento_max BETWEEN 1 AND 3),
    ADD COLUMN nivel_inteligencia     text    NOT NULL DEFAULT 'economico'
        CHECK (nivel_inteligencia IN ('economico','medio','mejor')),
    ADD COLUMN derivacion_pausa_horas integer NOT NULL DEFAULT 6  CHECK (derivacion_pausa_horas BETWEEN 1 AND 72),
    ADD COLUMN process_template_key   text    NOT NULL DEFAULT 'productos',
    ADD COLUMN process_options        jsonb   NOT NULL DEFAULT '{}',
    ADD COLUMN system_messages        jsonb   NOT NULL DEFAULT '{}';  -- textos de plantillas.md §7 editados

-- Plan: qué niveles de inteligencia permite
ALTER TABLE plans ADD COLUMN allowed_intelligence text[] NOT NULL DEFAULT '{economico}';

CREATE TABLE process_stage_overrides (
    tenant_id                  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    template_key               text NOT NULL,
    stage_key                  text NOT NULL,
    instrucciones              text CHECK (char_length(instrucciones) <= 4000),
    instrucciones_seguimiento  text CHECK (char_length(instrucciones_seguimiento) <= 1000),
    base_version               integer NOT NULL,    -- versión de la plantilla cuando se editó (C-05)
    updated_by                 uuid REFERENCES users(id) ON DELETE SET NULL,
    updated_at                 timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, template_key, stage_key)
);
```

### `agent_configs.process_options` (validado contra el esquema de opciones de cada plantilla)

```jsonc
{
  "calificacion": {
    "activa": true,
    "pregunta": "¿Quieres solo certificar la experiencia que ya tienes, o llevar un programa completo?",
    "caminos": [
      { "clave": "certificacion", "titulo": "Solo certificar",   "tipo": "venta",    "descripcion": "Ya tiene la experiencia" },
      { "clave": "programa",      "titulo": "Programa completo", "tipo": "asesoria", "descripcion": "Quiere estudiar" }
    ]
  },
  "modalidad_pago": "previo",                               // previo | contra_entrega (solo plantilla productos)
  "datos_requeridos": ["dni", "nombres", "apellidos", "correo", "telefono"],
  "link_pago_tarjeta": "https://tienda.capacita.pe",       // opcional
  "variables": { "temas_destacados": "inglés o PMBOK" }    // placeholders libres usables en instrucciones
}
```

Límites: máximo 3 caminos (botones de WhatsApp), título de camino de 20 caracteres o menos, máximo 10
variables. `datos_requeridos` sale del catálogo fijo: `nombres`, `apellidos`, `dni`, `ruc`, `correo`,
`telefono`, `direccion`, `distrito`, `referencia`, `empresa`, `fecha_nacimiento`. `tipo_entrega`, `direccion`
y `zona_envio` se agregan solos cuando el pedido requiere entrega (plantillas.md §1).

---

## 0017 — Biblioteca multimedia

```sql
CREATE TABLE media_assets (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    clave                      text NOT NULL CHECK (clave ~ '^[a-z0-9_]{2,40}$'),
    tipo                       text NOT NULL CHECK (tipo IN ('imagen','documento','video','audio')),
    titulo                     text NOT NULL,
    cuando_usar                text NOT NULL CHECK (char_length(cuando_usar) BETWEEN 5 AND 300),
    caption                    text CHECK (char_length(caption) <= 1024),
    storage_key                text NOT NULL,
    filename                   text NOT NULL,
    mime_type                  text NOT NULL,
    size_bytes                 integer NOT NULL,
    product_id                 uuid REFERENCES products(id) ON DELETE SET NULL,
    promotion_id               uuid REFERENCES promotions(id) ON DELETE SET NULL,
    provider_media_id          text,
    provider_media_expires_at  timestamptz,
    is_active                  boolean NOT NULL DEFAULT true,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, clave)
);
CREATE INDEX ix_media_assets_product ON media_assets (tenant_id, product_id) WHERE product_id IS NOT NULL;

ALTER TABLE payment_methods_business
    ADD CONSTRAINT fk_pm_media FOREIGN KEY (media_asset_id) REFERENCES media_assets(id) ON DELETE SET NULL;
```

Límites de WhatsApp aplicados al subir: imagen JPG/PNG ≤ 5 MB, video MP4 ≤ 16 MB, audio ≤ 16 MB,
documento PDF ≤ 100 MB. Claves con significado especial: `saludo` (la instrucción de INICIO le pide al
modelo enviarla si existe) y `metodos_pago` (la adjunta `enviar_metodos_pago` si ningún método tiene QR).

---

## 0018 — Productos aptos para venta

```sql
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS category   text,
    ADD COLUMN modo_venta               text    NOT NULL DEFAULT 'precio_fijo'
        CHECK (modo_venta IN ('precio_fijo','cotizacion')),
    ADD COLUMN es_digital               boolean NOT NULL DEFAULT false,   -- no pide dirección
    ADD COLUMN bundle_size              integer CHECK (bundle_size IS NULL OR bundle_size BETWEEN 2 AND 50),
    ADD COLUMN bundle_permite_libres    boolean NOT NULL DEFAULT false,   -- Capacita: "certificamos cualquier tema"
    ADD COLUMN destacado                boolean NOT NULL DEFAULT false;   -- el asistente lo empuja primero
```

Un **paquete** es un producto con `bundle_size`. Sus "selecciones" son los N ítems que lo completan:
productos del catálogo o, si `bundle_permite_libres`, nombres libres que dijo el cliente.

---

## 0019 — Pedidos y comprobantes

```sql
CREATE TABLE tenant_sequences (
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name       text NOT NULL,
    value      integer NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, name)
);
-- Número de pedido (sirve también para el primer pedido del tenant):
-- INSERT INTO tenant_sequences (tenant_id, name, value) VALUES (:t, 'orders', 1)
-- ON CONFLICT (tenant_id, name) DO UPDATE SET value = tenant_sequences.value + 1
-- RETURNING value;

CREATE TABLE orders (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id    uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    contact_id         uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    numero             integer NOT NULL,
    estado             text NOT NULL DEFAULT 'borrador' CHECK (estado IN
                         ('borrador','por_confirmar','confirmado','en_curso','completado','descartado')),
    modalidad_pago     text NOT NULL DEFAULT 'previo' CHECK (modalidad_pago IN ('previo','contra_entrega')),
    moneda             char(3) NOT NULL,
    subtotal           numeric(12,2) NOT NULL DEFAULT 0,
    descuento          numeric(12,2) NOT NULL DEFAULT 0,
    envio              numeric(12,2) NOT NULL DEFAULT 0,
    total              numeric(12,2) NOT NULL DEFAULT 0,
    promotion_id       uuid REFERENCES promotions(id) ON DELETE SET NULL,
    promotion_snapshot jsonb,                 -- nombre, tipo, valor al momento de aplicar
    tipo_entrega       text CHECK (tipo_entrega IN ('delivery','recojo')),   -- null si no requiere entrega
    zona_envio         text,
    datos_cliente      jsonb,                 -- copia al pasar a por_confirmar
    motivo_rechazo     text,
    rechazo_vigente    boolean NOT NULL DEFAULT false,  -- true tras rechazar un comprobante; se apaga al cambiar ítems o datos
    siguiente_paso     text,                  -- lo escribe el operador al confirmar (va en el aviso al cliente)
    is_sandbox         boolean NOT NULL DEFAULT false,
    confirmed_by       uuid REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at       timestamptz,
    last_activity_at   timestamptz NOT NULL DEFAULT now(),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, numero)
);
CREATE INDEX ix_orders_tenant_estado ON orders (tenant_id, is_sandbox, estado, created_at DESC);
CREATE INDEX ix_orders_conversation  ON orders (conversation_id, created_at DESC);

CREATE TABLE order_items (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    order_id           uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id         uuid REFERENCES products(id) ON DELETE SET NULL,
    nombre             text NOT NULL,          -- copia del nombre al agregar
    precio_unitario    numeric(12,2) NOT NULL, -- copia del precio al agregar
    cantidad           integer NOT NULL CHECK (cantidad BETWEEN 1 AND 999),
    bundle_size        integer,
    bundle_selections  jsonb NOT NULL DEFAULT '[]',   -- [{ "nombre": "SIAF", "product_id": null }]
    subtotal           numeric(12,2) NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_order_items_order ON order_items (order_id);

CREATE TABLE payment_proofs (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    order_id           uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    conversation_id    uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    message_id         uuid REFERENCES messages(id) ON DELETE SET NULL,
    storage_key        text NOT NULL,
    estado             text NOT NULL DEFAULT 'por_verificar'
                         CHECK (estado IN ('por_verificar','aprobado','rechazado')),
    monto              numeric(12,2),
    moneda             char(3),
    fecha_operacion    date,
    codigo_operacion   text,
    entidad            text,                 -- Yape, Plin, BCP, Interbank, BBVA…
    destinatario       text,
    confianza          numeric(3,2),         -- 0.00–1.00
    observaciones      text[] NOT NULL DEFAULT '{}',   -- monto_distinto | codigo_repetido | baja_confianza | sin_datos
    extraccion         jsonb,                -- salida cruda de visión (sin imagen)
    motivo_rechazo     text,
    reviewed_by        uuid REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at        timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_proofs_order  ON payment_proofs (order_id, created_at DESC);
CREATE INDEX ix_proofs_codigo ON payment_proofs (tenant_id, codigo_operacion) WHERE codigo_operacion IS NOT NULL;
```

### Transiciones de `orders.estado`

```
borrador ──(comprobante recibido | cliente confirma contra entrega)──▶ por_confirmar
por_confirmar ──(operador confirma)──▶ confirmado ──▶ en_curso ──▶ completado
por_confirmar ──(operador rechaza)──▶ borrador        (motivo_rechazo; con comprobante: comprobante rechazado
                                                       y rechazo_vigente=true; contra entrega: rechazo_vigente=false)
borrador ──(cambio de ítems o de datos del cliente)──▶ borrador con rechazo_vigente=false
borrador ──(7 días sin actividad | operador descarta)──▶ descartado
cualquier estado salvo completado ──(operador descarta)──▶ descartado
```

Cualquier otra transición la rechaza `OrderService` con `InvalidTransition`.

`pago_estado` (hecho derivado) se calcula con el pedido activo y su último comprobante:

| Pedido | Último comprobante | `pago_estado` |
|---|---|---|
| `confirmado`, `en_curso`, `completado` | — | `confirmado` |
| `por_confirmar` | cualquiera o ninguno | `por_verificar` |
| `borrador` con `rechazo_vigente = true` | `rechazado` | `rechazado` |
| otro | — | `null` |

Pedido activo = el último pedido de la conversación que no está `descartado`. Los pedidos sandbox se
excluyen de `codigo_repetido`, del resumen de Pedidos y de los correos (C-19).

---

## 0020 — Contactos (BSUID y datos del cliente) y trazas

```sql
-- Verificar primero qué existe (TASK-408; los nombres de campos de YCloud se confirman en TASK-418).
-- Opt-out: YA existe (contacts.opted_out_at + agent_configs.opt_out_keywords, migraciones 0008–0011); se reutiliza.
-- Intención:
ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS external_user_id text,           -- BSUID de Meta
    ADD COLUMN IF NOT EXISTS custom_data      jsonb NOT NULL DEFAULT '{}';
ALTER TABLE contacts ALTER COLUMN phone DROP NOT NULL;         -- si hoy es NOT NULL
CREATE UNIQUE INDEX IF NOT EXISTS ux_contacts_tenant_bsuid
    ON contacts (tenant_id, external_user_id) WHERE external_user_id IS NOT NULL;
-- Al menos uno de los dos identifica al contacto:
ALTER TABLE contacts ADD CONSTRAINT ck_contacts_identity
    CHECK (phone IS NOT NULL OR external_user_id IS NOT NULL);

ALTER TABLE agent_traces
    ADD COLUMN stage_key       text,
    ADD COLUMN funnel_code     text,
    ADD COLUMN tools_enabled   text[],
    ADD COLUMN outbound_count  smallint,
    ADD COLUMN model_tier      text;
```

`contacts.custom_data` guarda los datos de `datos_requeridos` (`{"dni": "…", "correo": "…"}`). Se valida
por campo: DNI de 8 dígitos, RUC de 11, correo y teléfono E.164. En trazas y logs se enmascara (C-20).

## 0021 – 0027 — Agregadas al implementar (Fases A y B, Milestone B)

No estaban en este documento; cada una se justifica en `conciliacion.md` y en su migración.

| Migración | Cambio | Origen |
|---|---|---|
| 0021 | `messages.payload jsonb` y los tipos `interactive` y `video` | TASK-422 (la 007 la reutiliza, decisión 2) |
| 0022 | `tenant_notifications` con `id`, `payload` y los tipos que se repiten | TASK-431 (decisión 3) |
| 0023 | `payment_proofs.estado` suma `anulado`; `orders.devolucion_pendiente boolean` | Revisión E-2 |
| 0024 | `contacts.posible_duplicado_de uuid` (FK a `contacts`, `SET NULL`) | Decisión 12 |
| 0025 | `orders.notas jsonb` (`[{en, texto, origen}]`) | Decisión 8 |
| 0026 | `product_categories (tenant_id, nombre, orden, activa)` y `products.category_id`, con backfill | Decisión 13 |
| 0027 | Datos: los planes de pago permiten `economico, medio, mejor`; `trial` solo `economico` | Decisión 4 |

`payment_proofs.observaciones` admite además `monto_negativo`, `destinatario_distinto`,
`destinatario_no_verificado` y `fecha_anterior_al_pedido` (revisión D-1 y D-2). Una lista vacía significa
que todas las comprobaciones corrieron y pasaron.
