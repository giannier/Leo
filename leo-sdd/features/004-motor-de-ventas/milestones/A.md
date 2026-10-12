# Milestone A — Capacita en el simulador (sin pedidos)

**Task:** TASK-423 · **Quién prueba:** Giannier · **Dónde:** simulador del panel (`/sandbox`) con el **Modo depuración** activado
**Alcance:** escenarios 1 a 3 de Capacita (orgánica, calificación escrita, anuncio simulado). Los escenarios 4 a 6 (paquete de 5 con 2 temas, comprobante, "lo pensaré") dependen de pedidos y pagos y se prueban en el Milestone B (TASK-433).

Este milestone demuestra que el motor ya conduce la conversación por etapas (INICIO → CALIFICAR → IDENTIFICAR, y ANUNCIO / ASESORIA), con botones, listas y flyer, antes de construir los pedidos. Las herramientas de venta (`gestionar_pedido`, `registrar_datos_cliente`, `enviar_metodos_pago`, `derivar_a_humano`) siguen como stub `no_disponible`: ver la sección 6.

---

## 1. Requisitos previos

- Stack de desarrollo arriba (`make up`): `postgres`, `redis`, `minio`, `api` y `worker`. Panel en `http://localhost:3000`.
- Migraciones al día (`make migrate`): la base debe estar en la revisión `0021`.
- Un proveedor de LLM configurado. El stack de desarrollo corre con `LLM_PROFILE=openai`: **cada mensaje del simulador es una llamada real al modelo** (costo bajo, pero no es cero).
- Comandos `docker compose` ejecutados desde la raíz del repo. En adelante `PSQL` significa:

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo
```

- Dos imágenes de prueba (PNG, JPEG o WebP, menos de 5 MB cada una): una para `saludo` y una para `flyer_paquetes`. Sirve cualquier imagen; para la prueba real usa la del flyer de Capacita.

## 2. Preparar el tenant "Escuela Capacita"

Orden obligatorio: la plantilla se fija **antes** de abrir la primera conversación del simulador. Una conversación conserva la plantilla con la que empezó (`conversation_state.template_key`); si ya existía una, se corrige con "Reiniciar conversación".

### 2.1 Crear el tenant

1. Registra un usuario en `/register`, verifica el correo y entra al onboarding.
2. Nombre del negocio: **`Escuela Capacita`** (exacto: los comandos SQL de abajo lo usan para encontrar el tenant). Moneda `PEN`.
3. Completa la **descripción del negocio** y el **horario**. Desde esta feature el prompt los incluye (sección 7); si están vacíos, el asistente no los menciona.
4. Tono a gusto. Los niveles de comportamiento (ritmo, detalle) quedan en sus valores por defecto: no hay API para cambiarlos hasta TASK-435.
5. Omite el paso de catálogo del onboarding (se carga en 2.3) y el de WhatsApp (el simulador no lo necesita).
6. Deja el asistente **activo** (`agent_configs.is_active`). El simulador funciona aunque esté pausado, pero así se parece más al canal real.

### 2.2 Fijar la plantilla `certificaciones` (por SQL hasta TASK-435)

Todavía no existe la API de proceso (`PUT /api/v1/assistant/process`, TASK-435). Se fija directo en la base:

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo -c \
  "UPDATE agent_configs SET process_template_key = 'certificaciones', process_options = '{}'::jsonb WHERE tenant_id = (SELECT id FROM tenants WHERE name = 'Escuela Capacita');"
```

Debe responder `UPDATE 1`. Con `process_options = '{}'` el tenant usa los valores por defecto de la plantilla: calificación activa con dos caminos (`certificacion` = "Solo certificar", tipo venta; `programa` = "Programa completo", tipo asesoría), pago previo y datos `dni, nombres, apellidos, correo, telefono`.

Verifica:

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo -c \
  "SELECT t.name, a.process_template_key FROM agent_configs a JOIN tenants t ON t.id = a.tenant_id WHERE t.name = 'Escuela Capacita';"
```

La configuración efectiva se guarda en una caché de 60 s por proceso. Si ya habías usado el simulador con otra plantilla, espera un minuto o reinicia el `api` (`docker compose -f deploy/docker-compose.dev.yml restart api`).

### 2.3 Cargar productos y paquetes (CSV)

Archivo: [`A-catalogo-capacita.csv`](A-catalogo-capacita.csv) (en esta misma carpeta). Se carga por **Catálogo → Importar CSV** (`POST /tenant/products/import` y luego `/confirm`): revisa la vista previa y confirma. Debe decir **11 filas válidas, 0 inválidas**.

Contenido:

| Tipo | Filas | Notas |
|---|---|---|
| Certificaciones individuales | 8 (SIAF, Gestión pública, Contrataciones con el Estado, SEACE, Excel avanzado, Power BI, Gestión de proyectos, Derecho administrativo) | S/ 120.00 c/u, `es_digital = si`, 5 categorías (Sector Público, Contrataciones, Ofimática, Gestión, Derecho) |
| Paquetes | 3 (de 2, 5 y 10 certificaciones) | `paquete` = 2 / 5 / 10, `permite_libres = si` (el cliente puede pedir un tema que no está en el catálogo), `es_digital = si`; el de 5 va como `destacado` |

**Los precios son de prueba, salvo uno.** El único precio que sale de los documentos es "5 certificaciones x S/ 500" (spec, `data-model.md`). El precio unitario (S/ 120), el de 2 (S/ 200) y el de 10 (S/ 900) los puse yo para que haya ancla y escalera; **cámbialos por los reales de Capacita en el CSV antes de importar** si quieres probar con tus números.

Validación contra el importador (`backend/app/services/csv_import.py`):

- Cabeceras reconocidas por sus alias: `nombre`→`name`, `descripcion`→`description`, `precio`→`price`, `stock`, `categoria`→`category`, `modo_venta`, `es_digital`, `paquete`→`bundle_size`, `permite_libres`→`bundle_permite_libres`, `destacado`. Obligatorias: `nombre` y `precio`.
- Verificado ejecutando `parse_catalogue` sobre el archivo: **11 válidas, 0 inválidas**.
- Separador coma, UTF-8 sin BOM (el importador prueba UTF-8 antes que cp1252), decimales con punto. `paquete` entre 2 y 50. Las celdas vacías de `stock` y de las columnas de paquete son válidas. Los `si` / `no` son palabras aceptadas.
- Ningún nombre empieza con `=`, `+`, `-` o `@` (el importador antepone un apóstrofo a esos).
- Si lo abres y lo guardas con Excel, revisa que siga siendo CSV y que no cambie los decimales a coma.

### 2.4 Recursos `saludo` y `flyer_paquetes`

No hay API de Multimedia hasta TASK-436, así que para este milestone se siembran a mano: se sube el archivo a MinIO y se inserta la fila en `media_assets`. Las claves `saludo` y `flyer_paquetes` cumplen el patrón `^[a-z0-9_]{2,40}$`; `saludo` es una clave especial que la instrucción de INICIO busca por nombre.

1. Abre la consola de MinIO: `http://localhost:9001` (usuario y clave por defecto del compose: `leo-minio` / `leo-minio-secret`, o los de tu `.env`).
2. Bucket **`leo-storage`** (`S3_BUCKET`): sube las dos imágenes con estas rutas exactas:
   - `media/capacita-saludo.png`
   - `media/capacita-flyer-paquetes.png`
   Si tus archivos son JPEG o WebP, cambia la extensión de la ruta, `filename` y `mime_type` en el SQL.
3. Obtén el tamaño en bytes de cada archivo (`stat -c %s archivo.png` en bash, o Propiedades en Windows) y reemplaza `SIZE_SALUDO` y `SIZE_FLYER` en el SQL:

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo <<'SQL'
INSERT INTO media_assets
  (tenant_id, clave, tipo, titulo, cuando_usar, caption, storage_key, filename, mime_type, size_bytes, product_id)
SELECT t.id, 'saludo', 'imagen', 'Saludo de bienvenida',
       'Primer mensaje de la conversación, junto con el saludo.',
       'Bienvenido a Escuela Capacita',
       'media/capacita-saludo.png', 'saludo.png', 'image/png', SIZE_SALUDO, NULL
FROM tenants t WHERE t.name = 'Escuela Capacita';

INSERT INTO media_assets
  (tenant_id, clave, tipo, titulo, cuando_usar, caption, storage_key, filename, mime_type, size_bytes, product_id)
SELECT t.id, 'flyer_paquetes', 'imagen', 'Flyer de paquetes',
       'Al mostrar los paquetes de certificaciones o cuando el cliente llegue por el anuncio de los paquetes.',
       NULL,
       'media/capacita-flyer-paquetes.png', 'flyer-paquetes.png', 'image/png', SIZE_FLYER,
       (SELECT p.id FROM products p WHERE p.tenant_id = t.id AND p.name = 'Paquete 5 certificaciones')
FROM tenants t WHERE t.name = 'Escuela Capacita';
SQL
```

Cada `INSERT` debe responder `INSERT 0 1`. El flyer queda vinculado al "Paquete 5 certificaciones" (así `buscar_productos` lo ofrece en la lista de recursos de ese producto). Si importaste el CSV **después**, el vínculo sale `NULL`: importa primero el catálogo.

Verifica:

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo -c \
  "SELECT clave, tipo, storage_key, size_bytes, product_id IS NOT NULL AS vinculado, is_active FROM media_assets WHERE tenant_id = (SELECT id FROM tenants WHERE name = 'Escuela Capacita');"
```

En el simulador, el saludo y el flyer deben verse como imagen. Si aparece un archivo sin imagen o `url: null`, la ruta de MinIO no coincide con `storage_key`. En el simulador no se usa `provider_media_id` (nada sandbox llega al proveedor).

### 2.5 Abrir el simulador

`/sandbox` → marca **Modo depuración**. El panel muestra, por cada turno, la etapa antes → después, la columna del embudo, las herramientas habilitadas y llamadas (con su `status`), los hechos y los tokens. "Reiniciar conversación" borra el estado y los mensajes de esa conversación; úsalo entre escenarios.

---

## 3. Escenarios

Convenciones de la tabla de cada paso. **Etapa** es la que se ve en el panel como `etapa_antes → etapa_despues` de ese turno; **Embudo** es la columna al final del turno.

Los textos que escribe el modelo variarán. Lo que se evalúa es el **comportamiento** (etapa, herramientas, hechos, mensajes salientes), no la redacción. Cada turno debe tener **3 mensajes salientes o menos** (en el debug: `outbound_count`).

### Escenario 1 — Orgánica, con botón

Reinicia la conversación antes de empezar.

| # | Escribes | Etapa | Embudo | Debe ocurrir |
|---|---|---|---|---|
| 1 | `Hola` | INICIO → CALIFICAR | NUEVO | Sale el recurso `saludo` (imagen) y un texto corto que saluda, se presenta como el asistente de Escuela Capacita y **pregunta el nombre**, y se detiene. Una sola pregunta. Hechos: `saludo_enviado = true`, `recursos_enviados = [saludo]`. No hay pregunta de calificación todavía. |
| 2 | `Me llamo Ana` | CALIFICAR → CALIFICAR | NUEVO | Llama a `mostrar_opciones(tipo=caminos)`: aparecen **2 botones** ("Solo certificar", "Programa completo") con la pregunta de calificación personalizada. El texto del modelo es corto o vacío. Hechos: `opciones_mostradas = [caminos]`. |
| 3 | Tocas **Solo certificar** | CALIFICAR → IDENTIFICAR | ATENDIDO | **Antes** de llamar al modelo el sistema fija `camino = certificacion` (se ve en `hechos` y en el evento de etapa; el id del botón es `camino:certificacion`). El modelo pregunta qué tema necesita certificar. No repite la pregunta de calificación. |
| 4 | `Necesito certificarme en SIAF` | IDENTIFICAR → IDENTIFICAR | ATENDIDO | Llama a `buscar_productos`; confirma que **sí** se certifica ("Tenemos Certificación individual SIAF"), menciona el **precio unitario S/ 120.00 antes** del precio de un paquete, llama a `mostrar_opciones(tipo=productos, filtro=paquetes)` (los 3 paquetes: son 3, salen como botones) y a `enviar_recurso("flyer_paquetes")`. Hechos: `vio_productos = true`, `recursos_enviados = [saludo, flyer_paquetes]`, `opciones_mostradas` incluye `productos:paquetes`. Turno con flyer + opciones + texto = 3 mensajes o menos. |
| 5 | Tocas **Paquete 5 certificaciones** | IDENTIFICAR (queda) | ATENDIDO | Llega al modelo la línea "El cliente eligió: Paquete 5 certificaciones (producto_id=…)". Se esperan precio S/ 500.00 y el pedido de los temas, **pero no se crea pedido** (`gestionar_pedido` es stub): el modelo no debe decir que lo agregó. Aquí termina el recorrido útil de este milestone. |
| 6 | `Mándame el flyer otra vez` | IDENTIFICAR | ATENDIDO | `enviar_recurso` responde `omitido` (ya enviado): **no sale un segundo flyer** y el modelo no lo menciona como nuevo. |

Qué mirar en el panel de depuración: la etapa del turno 3 cambia **sin que el modelo la haya decidido**; en las herramientas habilitadas de CALIFICAR solo están `mostrar_opciones`, `elegir_camino` y las globales; en IDENTIFICAR aparecen `buscar_productos`, `ver_producto`, `mostrar_opciones`, `gestionar_pedido` (stub) y `elegir_camino`.

### Escenario 2 — Calificación respondida escribiendo

Reinicia la conversación. Mismos turnos 1 y 2 del escenario 1 (`Hola`, `Me llamo Ana`), pero en el turno 3 **no toques el botón**.

| # | Escribes | Etapa | Embudo | Debe ocurrir |
|---|---|---|---|---|
| 3 | `Ya tengo la experiencia, solo necesito el certificado para postular a un concurso` | CALIFICAR → IDENTIFICAR | ATENDIDO | El modelo llama a `elegir_camino("certificacion")` (status `hecho` en el debug) y en el mismo mensaje pregunta qué tema necesita certificar. Hecho `camino = certificacion`. **No** vuelve a mostrar los botones. |
| 4 | `Contrataciones con el Estado y OSCE` | IDENTIFICAR | ATENDIDO | Mismo comportamiento del paso 4 del escenario 1 (confirma que sí, ancla de precio, paquetes, flyer). Un tema múltiple se confirma junto, en una lista. |

Variantes (reinicia entre una y otra):

- **2b · Respuesta ambigua.** En el turno 3 escribe `no sé, ¿qué me recomiendas?`. Esperado: sigue en CALIFICAR, **no** llama a `elegir_camino` (no adivina) y vuelve a preguntar con otras palabras o con los botones. Hecho `camino` vacío.
- **2c · Camino asesoría.** En el turno 3 escribe `Quiero estudiar el programa completo`. Esperado: `elegir_camino("programa")` → etapa ASESORIA, embudo DERIVADO. El asistente envía la malla si existe (no existe: no debe inventarla), pregunta qué programa y disponibilidad. **No hay derivación real** (`derivar_a_humano` es stub, sección 6): puede decir que un asesor le escribirá, pero la conversación no se pausa ni genera aviso. No debe describir programas, precios ni fechas.

### Escenario 3 — Entrada por anuncio (simulado)

El simulador no envía el `referral` de Meta, así que el anuncio se simula **escribiendo el hecho `entrada_anuncio`** en la conversación de prueba. Es el mismo hecho que guarda el pipeline cuando llega un `referral` por YCloud (el parseo ya está probado en TASK-418/421).

Reinicia la conversación.

| # | Acción | Etapa | Embudo | Debe ocurrir |
|---|---|---|---|---|
| 1 | Escribes `Hola` | INICIO → CALIFICAR | NUEVO | Igual al escenario 1 (saludo, nombre, una sola pregunta). **Aunque venga de un anuncio, la oferta va en el siguiente turno** (instrucción de INICIO de `certificaciones`). El anuncio todavía no existe. |
| 2 | **Inyectas el anuncio** con el SQL de abajo | — | — | Verifica con el debug del paso 3 o con la consulta de abajo. |
| 3 | Escribes `Soy Ana` | CALIFICAR → ANUNCIO → IDENTIFICAR | NUEVO → ATENDIDO | La etapa calculada al empezar el turno es **ANUNCIO** (anuncio vigente de menos de 7 días, sin camino, sin productos vistos); tiene prioridad sobre CALIFICAR. **No hace la pregunta de calificación** ni el catálogo desde cero: va directo a lo anunciado (`buscar_productos`), muestra los paquetes, envía `flyer_paquetes` (recurso del paquete destacado) y llama a `elegir_camino("certificacion")` en el mismo turno. Al final del turno: etapa IDENTIFICAR, `camino = certificacion`, `entrada_anuncio` visible en los hechos. |
| 4 | `Sí, lo quiero` | IDENTIFICAR | ATENDIDO | Sigue en IDENTIFICAR (el anuncio no se "reinicia"). `gestionar_pedido` es stub: no se crea pedido. |

SQL del paso 2. Busca la conversación del simulador y escribe el hecho:

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo <<'SQL'
WITH c AS (
  SELECT id FROM conversations
  WHERE channel = 'sandbox' AND tenant_id = (SELECT id FROM tenants WHERE name = 'Escuela Capacita')
  ORDER BY created_at DESC LIMIT 1
)
UPDATE conversation_state s
SET facts = s.facts || jsonb_build_object('entrada_anuncio', jsonb_build_object(
      'ad_id', 'sim-ad-001',
      'source_url', 'https://fb.me/sim',
      'headline', '5 certificaciones x S/ 500',
      'body', 'Certifica tu experiencia con 5 temas a tu elección',
      'recibido_en', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')))
FROM c
WHERE s.conversation_id = c.id AND s.is_sandbox;

SELECT s.stage_key, s.facts->'entrada_anuncio' AS anuncio
FROM conversation_state s
WHERE s.conversation_id = (SELECT id FROM conversations WHERE channel = 'sandbox'
  AND tenant_id = (SELECT id FROM tenants WHERE name = 'Escuela Capacita') ORDER BY created_at DESC LIMIT 1);
SQL
```

El `UPDATE` debe responder `UPDATE 1` (si responde `UPDATE 0`, todavía no enviaste el `Hola` del paso 1, porque la fila de estado se crea en el primer turno). El anuncio vence a los 7 días: si repites el escenario, vuelve a inyectarlo.

---

## 4. Checklist de aprobación (Giannier)

Marca cada punto tras recorrer los escenarios. Con un solo FALLA en los puntos de **Motor** o **Mensajes**, el milestone no se aprueba.

**Preparación**
- [ ] El CSV importa con 11 filas válidas y 0 inválidas; el catálogo muestra los paquetes con `paquete` 2/5/10.
- [ ] El UPDATE de la plantilla responde `UPDATE 1` y la primera conversación arranca en la plantilla `certificaciones`.
- [ ] `saludo` y `flyer_paquetes` aparecen como imagen en el simulador (no como error).

**Motor (etapas y hechos)**
- [ ] E1: INICIO → CALIFICAR → IDENTIFICAR en el orden esperado y con las columnas NUEVO → ATENDIDO.
- [ ] E1: tocar el botón fija `camino` antes del modelo (se ve en hechos) y no se repite la pregunta de calificación.
- [ ] E2: la respuesta escrita lleva a `elegir_camino` (status `hecho`) y a IDENTIFICAR; la ambigua (2b) no adivina.
- [ ] E2c: el camino "programa" lleva a ASESORIA / DERIVADO sin inventar programa, precio ni fecha.
- [ ] E3: con el anuncio inyectado, la etapa es ANUNCIO, no hay pregunta de calificación y termina en IDENTIFICAR con `camino` fijado.
- [ ] Cada cambio de etapa del turno es consistente entre el panel de depuración y los hechos.

**Mensajes**
- [ ] El saludo se pide **una sola vez**: nombre y nada más en el primer turno.
- [ ] Ningún turno supera 3 mensajes salientes.
- [ ] El flyer y el saludo **no se reenvían** (el turno 6 del E1 lo prueba).
- [ ] Los botones muestran como máximo 3 opciones y las listas 10 filas; los títulos no salen cortados a mitad de palabra.
- [ ] El texto del modelo no repite lo que ya dicen los botones.
- [ ] Precios tal cual del catálogo (S/ 120.00, S/ 200.00, S/ 500.00, S/ 900.00 o los que hayas puesto); nunca calcula ni redondea; menciona el precio unitario antes de los paquetes.
- [ ] Dice que **sí** se certifica el tema aunque no esté en el catálogo (prueba con un tema inventado, por ejemplo `Gestión de museos`) y no deriva por ese motivo.
- [ ] No menciona herramientas, etapas ni "el sistema".

**Prompt (contexto del negocio)**
- [ ] El asistente responde bien una pregunta sobre el negocio que sale de la descripción o el horario (ver sección 7).
- [ ] Si le preguntas la fecha o el día, contesta la correcta (ver sección 7).

**Aprobación**
- [ ] **Giannier aprueba el Milestone A.** Hasta que lo marques aquí y lo confirmes en el chat, TASK-423 sigue abierta en `tasks.md` y no se avanza a la Fase B.

Anota debajo cualquier falla con el escenario, el turno y una captura del panel de depuración:

```text
(vacío)
```

---

## 5. Si algo no se parece a lo esperado

| Síntoma | Causa probable |
|---|---|
| El asistente no se comporta como "certificaciones" (pregunta por productos genéricos, no hay calificación) | La conversación se creó antes del UPDATE de plantilla, o la caché de 60 s. Reinicia la conversación y/o el `api`. |
| El saludo sale sin imagen | `media_assets` sin fila `saludo`, `is_active = false`, o archivo ausente en MinIO (`url: null`). |
| El E3 sigue en CALIFICAR | El SQL dio `UPDATE 0`, el `recibido_en` tiene más de 7 días, o `vio_productos` ya era verdadero (reinicia y repite). |
| Vuelve a pedir el nombre en CALIFICAR / ANUNCIO | Ver sección 6, `registrar_datos_cliente`. |
| Respuesta degradada ("no pude responder") | Falló el proveedor de LLM: revisa `trace.degraded`, la clave del modelo y los logs del `api`. El circuit breaker no existe todavía. |

---

## 6. Qué NO está construido todavía (a propósito)

Nada de esto debe aprobarse ni rechazarse en este milestone; es el alcance de las fases siguientes.

| Tema | Estado | Efecto en la prueba |
|---|---|---|
| **Pedidos** (`gestionar_pedido`, `OrderService`, pricing, promociones) | Stub `no_disponible` (TASK-424 a 426) | No se crean pedidos ni se aplican promociones. Elegir un paquete no avanza a PEDIDO / DATOS / COBRO. Las selecciones libres de los paquetes y el cupo del `catalog_guard` (lista blanca) llegan con TASK-426. Es el Milestone B. |
| **Datos del cliente** (`registrar_datos_cliente`) | Stub (TASK-427) | El nombre dicho en el chat no queda como "confirmado" (`custom_data.nombres`): puede que el modelo vuelva a pedirlo en CALIFICAR o ANUNCIO. Se anota como observación, no como falla del motor. |
| **Pagos y comprobantes** (`enviar_metodos_pago`, lectura de comprobantes, Pedidos del panel) | No construido (TASK-428 a 432) | No hay COBRO ni VERIFICACION. |
| **Lectura de imágenes** | No construido (TASK-429; el análisis general, feature 006) | Una imagen subida al simulador llega al modelo como "[El cliente envió una imagen]"; no se guarda ni se lee. |
| **Derivación a humano** (`derivar_a_humano`, pausa, aviso al tenant) | Stub (TASK-443) | ASESORIA no deriva de verdad; no hay pausa ni notificación. |
| **Circuit breaker, memoria de 20 mensajes y niveles de inteligencia** (C-02) | TASK-445 | Si el proveedor de LLM falla, se ve la respuesta degradada de siempre, sin corte de 2 minutos. El "nivel de inteligencia" queda en el valor configurado. |
| **Seguimiento automático** | TASK-444 | No sale ningún mensaje por inactividad. |
| **API de configuración** (comportamiento, proceso, información del negocio, FAQ, promociones, multimedia: TASK-434 a 436) y el panel "Asistente" | No construido | Por eso la plantilla y los recursos se fijan por SQL y MinIO. No hay sección Entregas ni métodos de pago configurables. |
| **Retiro del prompt viejo** (`consultar_catalogo`) y evals del motor nuevo | TASK-447 | Los evals existentes (`make evals`) **no se corrieron**: cambió el prompt que ve el modelo (sección 7). Es un pendiente de PR, no de este milestone. |

---

## 7. Decisiones abiertas que afectan este milestone

Todas están **pendientes de ratificar por Giannier** (detalle en `conciliacion.md §8`). Si alguna no te convence, dilo al aprobar el milestone y se ajusta antes de la Fase B.

1. **Texto de DERIVADO (provisional).** `plantillas.md` dice que en DERIVADO "no hay turno de modelo", pero el test de plantillas exige un texto en todas las etapas. Se dejó uno mínimo ("pausada para que la atienda una persona... No respondas") que degrada con seguridad si alguna vez se usara. En este milestone no se llega a DERIVADO real (la derivación es stub). Conciliación §8.5.
2. **El prompt ahora incluye la descripción del negocio, el horario y la fecha de hoy.** No están en `plantillas.md`: se incluyeron porque el prompt viejo los tenía y perderlos habría sido una regresión. La descripción va con un máximo de 300 caracteres y el ESTADO suma la línea `Hoy: lunes 12 de octubre de 2026, 10:00`. "Cliente nuevo" sigue sin hecho que lo indique. Conciliación §8.12 y §8.17.
3. **Categorías como `categoria:{slug}`.** Las categorías no tienen tabla ni id (`products.category` es texto libre). Cuando el cliente toca una categoría, el id es un slug (`categoria:gestion-publica`) y el modelo recibe "El cliente eligió: Gestión pública (categoria_id=gestion-publica)" y busca por el título. Si prefieres una tabla de categorías con id propio, cambia `_category_rows` y la migración. Conciliación §8.16.
4. **`consultar_catalogo` como alias, no renombre.** El registry por etapa ofrece `buscar_productos` y `ver_producto`; el agente de la 003 conserva su tool vieja. Conciliación §8.13.
5. **`mostrar_opciones`:** cualquier `filtro` distinto de `paquetes` se lee como nombre de categoría; las promociones de cierre no se listan (solo las automáticas); reenviar las mismas opciones no devuelve `omitido`. Conciliación §8.16.
6. **Migración 0021** (`messages.payload` y los tipos `interactive` y `video`) no estaba en `data-model.md` y la 007 planea su propio `ADD COLUMN payload`: habrá que dejar uno solo. Conciliación §8.17.
7. **Evals pendientes** de correr con el prompt nuevo (`make evals`), y el modelo de pruebas del e2e usa el LLM real del stack. Conciliación §8.17 y §8.18.
8. **Contacto sandbox sin filtrar** en listados de contactos (hoy no hay listado; llega con la 007). Conciliación §8.18.
9. Decisiones de datos que no tocan este milestone pero ya están abiertas: contacto fusionado entre teléfono y BSUID (§8.4); delivery sin recojo obliga a elegir `tipo_entrega` (§8.7); promociones de cierre sin filtrar por pedido hasta `pricing` (§8.7 y §8.12).

---

## 8. Después de aprobar

1. Giannier confirma por el chat: "Milestone A aprobado".
2. Recién entonces se marca `[x]` en TASK-423 de `tasks.md` y se avanza a la Fase B (TASK-424 en adelante).
3. Los recursos y el tenant "Escuela Capacita" se conservan: el Milestone B (TASK-433) los reutiliza para los escenarios 4 y 5.
