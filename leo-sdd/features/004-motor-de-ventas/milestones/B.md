# Milestone B — Pedido completo (del catálogo al pago confirmado)

**Task:** TASK-433 · **Quién prueba:** Giannier · **Dónde:** simulador (`/sandbox`, con **Modo depuración**) y la pantalla **Pedidos** (`/pedidos`) del panel
**Alcance:** Capacita, escenarios 4 y 5 (paquete de 5 con 2 temas; comprobante), más una **tienda** ("Tienda Demo", plantilla `productos`) con 3 productos, una promoción 2×1, delivery, comprobante, contra entrega y la confirmación o el rechazo del operador. El escenario 6 de Capacita ("lo pensaré") va como extra opcional (sección 3.3): `tasks.md` pide solo 4 y 5 para este milestone, y la spec lo cuenta en la aceptación final (TASK-449).

Este milestone demuestra el ciclo completo de una venta: el cliente arma el pedido con el agente, deja sus datos, recibe los métodos de pago, manda la foto del comprobante, una persona del negocio lo revisa en **Pedidos** y confirma o rechaza, y el cliente recibe el aviso. El sistema **nunca confirma un pago por su cuenta**: solo lee la imagen y marca lo que se ve raro (C-08).

---

## 1. Requisitos previos

- Stack de desarrollo arriba (`make up`): `postgres`, `redis`, `minio`, `api` y `worker`. Panel en `http://localhost:3000`. Comandos `docker compose` desde la raíz del repo.
- Migraciones al día (`make migrate`): la base debe estar en la revisión `0022`.
- El **Milestone A aprobado** y el tenant `Escuela Capacita` tal como quedó (catálogo de 11 productos, plantilla `certificaciones`, recursos `saludo` y `flyer_paquetes`). Este milestone lo reutiliza.
- **Cada mensaje del simulador y cada comprobante son llamadas reales a un modelo de pago.** El stack corre con `LLM_PROFILE=openai`. No se corrieron los evals (`make evals`) con estos cambios.
- **Para ver la foto del comprobante en el panel** (hallazgo de preparación): el enlace firmado lo genera el `api` con `S3_ENDPOINT_URL=http://minio:9000`, un nombre que solo existe dentro de la red de Docker. Desde tu navegador no resuelve y la pantalla mostrará "No pudimos cargar la imagen". Solución local: agrega esta línea al archivo `hosts` de Windows (`C:\Windows\System32\drivers\etc\hosts`, como administrador) y recarga el detalle del pedido:

  ```text
  127.0.0.1 minio
  ```

  Los datos que el sistema leyó del comprobante se ven igual con o sin la imagen. Ver sección 7, punto 1.
- Tres imágenes de prueba (PNG, JPEG o WebP, menos de 5 MB):
  1. **`comprobante-ok`**: captura de un pago de Yape o Plin armada por ti (puede ser inventada), con monto, fecha, **código de operación** y nombre de quien recibe.
  2. **`comprobante-monto`**: la misma captura con un monto distinto al del pedido (por ejemplo `S/ 450.00` cuando el pedido es de `S/ 500.00`).
  3. **`no-comprobante`**: cualquier foto que no sea un pago (un paisaje, un producto).

### Qué esperar de la lectura de imágenes (leer antes de empezar)

- **Visión real.** Con el perfil `openai` el comprobante lo lee el modelo de chat del perfil (puerto `VisionProvider`, TASK-429): una llamada real por imagen, con tope de 8 s. Extrae monto, moneda, fecha, código, entidad, destinatario y una confianza de 0 a 1. Si el modelo configurado no acepta imágenes, la llamada falla y el comprobante se guarda **`sin_datos`** (el operador lo ve con la marca "No se pudo leer"). Es el caso del perfil gratuito de NVIDIA (conciliación §8.24).
- **Falso o real, el sistema lee igual.** No consulta al banco ni distingue una captura editada de una auténtica: lee lo que dice la imagen. Por eso la decisión es del operador. Probar con capturas inventadas es válido para este milestone; no lo es para medir la calidad de la lectura con comprobantes reales (los umbrales, como `0.70` de "lectura poco segura", son una constante provisional).
- **Una foto que no es comprobante** no se guarda ni crea fila: el modelo recibe "la imagen NO es un comprobante" y vuelve a pedirla.
- **`codigo_repetido`** compara el código dentro del negocio y solo entre pedidos del mismo tipo: un comprobante del simulador solo se compara con otros del simulador.

## 2. Preparar los tenants

### 2.1 Capacita: lo que falta

Falta solo un **método de pago**. Si ya lo cargaste en el paso 5 del onboarding, omite el `INSERT` (devolverá `INSERT 0 0`):

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo <<'SQL'
INSERT INTO payment_methods_business (tenant_id, method, instructions)
SELECT t.id, 'yape', 'Yape al 987654321 a nombre de Escuela Capacita. Cuando pagues, envía la foto del comprobante por aquí.'
FROM tenants t
WHERE t.name = 'Escuela Capacita'
  AND NOT EXISTS (SELECT 1 FROM payment_methods_business m WHERE m.tenant_id = t.id AND m.method = 'yape');

SELECT t.name, m.method, m.is_active FROM payment_methods_business m JOIN tenants t ON t.id = m.tenant_id WHERE t.name = 'Escuela Capacita';
SQL
```

Capacita cobra siempre **por adelantado** (la plantilla `certificaciones` fija `modalidad_pago = previo`), sus productos son digitales y no tiene sección Entregas: **no se pide dirección**.

### 2.2 Tienda Demo

1. Registra otro usuario en `/register` (otro correo), verifica y entra al onboarding. Nombre del negocio: **`Tienda Demo`** (exacto: los SQL lo usan). Moneda `PEN`. Rubro `retail`. Pon una descripción y un horario. Omite el catálogo del onboarding (se carga en 2.2.2) y WhatsApp.
2. **Catálogo.** Importa [`B-catalogo-tienda.csv`](B-catalogo-tienda.csv) en **Catálogo → Importar CSV**; la vista previa debe decir **3 filas válidas, 0 inválidas**:

   | Producto | Precio | Notas |
   |---|---|---|
   | Polo básico | S/ 35.00 | Destacado; lleva la promoción 2×1 |
   | Gorra urbana | S/ 25.00 | |
   | Mochila ejecutiva | S/ 120.00 | |

   Los tres son físicos (`es_digital = no`), así que el pedido **requiere entrega**. Los precios son de prueba. Validado con `parse_catalogue`: 3 válidas, 0 inválidas. Si el importador falla con `ModuleNotFoundError: openpyxl`, la imagen del `api` está desactualizada: reconstruye (`make up`) o carga los tres productos a mano en el paso 3 del onboarding.
3. **Plantilla.** La predeterminada ya es `productos`. Verifica:

   ```bash
   docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo -c \
     "SELECT t.name, a.process_template_key, a.process_options FROM agent_configs a JOIN tenants t ON t.id = a.tenant_id WHERE t.name = 'Tienda Demo';"
   ```

   Debe mostrar `productos` y `{}` (pago previo). Sin API de proceso hasta TASK-435, el pago contra entrega se activa por SQL en el escenario T3.
4. **Entregas, promoción y método de pago** (no hay API hasta TASK-434; por SQL). Corre esto **después** de importar el catálogo (la promoción necesita el id del polo):

   ```bash
   docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo <<'SQL'
   INSERT INTO business_info (tenant_id, section, data)
   SELECT id, 'entregas', '{
     "hace_delivery": true,
     "zonas": [
       {"nombre": "Miraflores", "costo": "8.00", "tiempo": "24 h"},
       {"nombre": "San Isidro", "costo": "8.00", "tiempo": "24 h"},
       {"nombre": "Callao", "costo": "12.00", "tiempo": "24 a 48 h"}
     ],
     "envio_gratis_desde": "150.00",
     "recojo_en_tienda": false,
     "notas": "Entregas de lunes a sábado."
   }'::jsonb
   FROM tenants WHERE name = 'Tienda Demo'
   ON CONFLICT (tenant_id, section) DO UPDATE SET data = EXCLUDED.data, updated_at = now();

   INSERT INTO promotions (tenant_id, nombre, descripcion, uso, tipo, n, m, aplica_a, product_ids)
   SELECT t.id, '2x1 en polos', 'Llevas 2 polos y pagas 1', 'automatica', 'nxm', 2, 1, 'productos',
          ARRAY(SELECT p.id FROM products p WHERE p.tenant_id = t.id AND p.name = 'Polo básico')
   FROM tenants t WHERE t.name = 'Tienda Demo';

   INSERT INTO payment_methods_business (tenant_id, method, instructions)
   SELECT id, 'yape', 'Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí.'
   FROM tenants WHERE name = 'Tienda Demo';

   SELECT (SELECT count(*) FROM promotions WHERE tenant_id = t.id) AS promociones,
          (SELECT count(*) FROM business_info WHERE tenant_id = t.id AND section = 'entregas') AS entregas,
          (SELECT count(*) FROM payment_methods_business WHERE tenant_id = t.id) AS metodos_pago
   FROM tenants t WHERE t.name = 'Tienda Demo';
   SQL
   ```

   Debe mostrar `1 | 1 | 1`. El bloque no es repetible: una segunda corrida duplica la promoción y el método de pago.
   Delivery sin recojo: el cliente **debe** elegir `delivery`, y luego zona y dirección (conciliación §8.7).

   La caché de configuración efectiva dura 60 s por proceso: si ya habías usado el simulador, espera un minuto o reinicia el `api`.

### 2.3 Cómo se ve un pedido de prueba

Los pedidos del simulador están **ocultos** en `/pedidos` salvo que marques **Incluir pedidos de prueba**. No cuentan en las tarjetas de resumen, no avisan a nadie por correo (C-19) y "Reiniciar conversación" los borra junto con sus comprobantes. Marca esa casilla antes de empezar.

---

## 3. Escenarios

Convenciones como en el Milestone A: **Etapa** y **Embudo** son los del panel de depuración al final del turno. Lo que se evalúa es el **comportamiento** (etapa, herramientas, hechos, pedido, mensajes), no la redacción. Cada turno debe tener **3 mensajes salientes o menos**. Reinicia la conversación antes de cada escenario.

El agente **nunca** debe: decir que el pago está confirmado, calcular un total por su cuenta, ni decir que agregó algo si `gestionar_pedido` no devolvió `hecho`.

### 3.1 Capacita (pago previo, producto digital)

#### Escenario 4 — Paquete de 5 con 2 temas

Llega a IDENTIFICAR como en el escenario 1 del Milestone A (`Hola` → `Me llamo Ana` → botón **Solo certificar** → `Necesito certificarme en SIAF`).

| # | Escribes | Etapa | Embudo | Debe ocurrir |
|---|---|---|---|---|
| 1 | Tocas **Paquete 5 certificaciones** y escribes `Quiero ese, con SIAF y Excel avanzado` | IDENTIFICAR → PEDIDO | ATENDIDO → NEGOCIACION | `gestionar_pedido(agregar)` con el paquete y las dos selecciones. El pedido tiene 1 línea de S/ 500.00 con 2 de 5 temas; el modelo dice que **faltan 3** y pregunta cuáles. No pasa a pedir datos. |
| 2 | `SEACE, Power BI y Gestión de museos` | PEDIDO → DATOS | NEGOCIACION | Los tres se agregan (`elegir`). "Gestión de museos" **no está en el catálogo** y se acepta (el paquete permite libres). Pedido completo: 5 de 5. El modelo **no** dice "no lo tenemos". En el debug la etapa pasó a DATOS sin que el modelo la decidiera. |
| 3 | `Mi DNI es 12345678 y me llamo Ana` | DATOS | NEGOCIACION | `registrar_datos_cliente` guarda lo válido. Pide **solo lo que falta** (`apellidos`, `correo`, `telefono`) en un mensaje. No envía métodos de pago todavía. El valor del DNI **no** aparece en `args_resumen` del debug (sale enmascarado, `****5678`). |
| 4 | `Quispe Rojas, ana@example.com, 987654321` | DATOS → COBRO | NEGOCIACION | Datos completos. No pide dirección (todo es digital). El teléfono queda como `+51987654321`. |
| 5 | (el modelo, en el mismo turno o el siguiente) | COBRO | CUENTAS_ENVIADAS | Llama a `enviar_metodos_pago`: sale el texto de Yape con las instrucciones tal cual las escribiste y pide la **foto del comprobante**. Hecho `metodos_pago_enviados`. Total mencionado: S/ 500.00. |
| 6 | `¿Cuáles eran las cuentas?` | COBRO | CUENTAS_ENVIADAS | `omitido`: **no** reenvía los métodos; le dice que revise el mensaje anterior. |

En **Pedidos** (con la casilla de pruebas) el pedido ya aparece como `Borrador`; pasa a `Por confirmar` recién cuando llega el comprobante (escenario 5), y solo entonces cuenta como pendiente de revisión.

#### Escenario 5 — Comprobante y confirmación

Continúa en el mismo chat (estás en COBRO con los métodos enviados).

| # | Acción | Etapa | Embudo | Debe ocurrir |
|---|---|---|---|---|
| 1 | Pulsa **Enviar imagen** y sube `no-comprobante` | COBRO | CUENTAS_ENVIADAS | El agente dice que eso no parece un comprobante y lo pide de nuevo. **No** hay pedido nuevo en `/pedidos` ni cambio de etapa. |
| 2 | Sube `comprobante-monto` (S/ 450.00) | COBRO → VERIFICACION | EN_VERIFICACION | El pedido pasa a `Por confirmar`. El agente dice que está **en revisión** y que se le avisará; no dice "confirmado" ni pide el comprobante otra vez. |
| 3 | Abre **Pedidos** → casilla de pruebas → el pedido | — | — | Fila con "Con comprobante" y la marca **El monto no coincide**. En el detalle: la observación resaltada con "El pedido es de S/ 500.00", los datos leídos (monto S/ 450.00, código, entidad, confianza), la imagen (ver sección 1 si no carga), productos con las 5 selecciones, datos del cliente completos (el teléfono sin enmascarar) y el aviso "Pedido del modo prueba". |
| 4 | **Rechaza** con motivo `El monto no coincide con el total (S/ 500.00)` | VERIFICACION (rechazado) | EN_VERIFICACION | Respuesta "Pedido rechazado. Aviso enviado al cliente." El pedido vuelve a `Borrador`, con el motivo. En ≤ 3 s el simulador dibuja el aviso del sistema: *"Ana, revisamos tu comprobante y hay un detalle: El monto no coincide…"* (texto por defecto de `pago_rechazado`). |
| 5 | Sube `comprobante-ok` (S/ 500.00) | VERIFICACION → VERIFICACION (por verificar) | EN_VERIFICACION | Se crea un comprobante **nuevo**; el pedido vuelve a `Por confirmar`; ya no hay marca de monto. En el detalle aparecen los dos comprobantes (el anterior como `Rechazado`). |
| 6 | En Pedidos pulsa **Confirmar**, escribe `Te enviamos tus certificados en 48 h` y **Confirmar pedido** | VERIFICACION → CERRADO | GANADO | "Pedido confirmado. Aviso enviado al cliente." Estado `Confirmado`, con la alerta verde del siguiente paso. En ≤ 3 s el simulador muestra *"¡Ana, tu pago fue confirmado! ✅ Pedido #N. Te enviamos tus certificados en 48 h"*. |
| 7 | Escribe `¿Cuándo me llegan?` | CERRADO | GANADO | Responde con lo que sabe; **no** pide comprobante ni reenvía métodos. |
| 8 | En Pedidos: **Marcar en curso**, luego **Marcar completado** | CERRADO | GANADO | El estado cambia en cada paso; terminado ya no hay botones. No sale ningún mensaje al cliente (estos cambios no avisan). |

Variantes cortas (reinicia entre una y otra):

- **5b · Código repetido.** Con el pedido ya confirmado y **sin reiniciar** (reiniciar borra los comprobantes), escribe `Quiero también el paquete de 2`, completa el pedido con sus temas y sube `comprobante-ok` otra vez. Esperado: el pedido nuevo trae la marca **Código repetido**, porque el código ya apareció en un comprobante del simulador.
- **5c · Doble clic.** Con un pedido por confirmar, abre el detalle en dos pestañas y confirma en ambas. La segunda debe responder "Otra persona ya revisó este pedido" y refrescar el estado.

### 3.2 Tienda Demo (pago previo y contra entrega, delivery, 2×1)

Antes del T1 comprueba que el simulador usa la plantilla de la tienda (sección 2.2.3).

#### Escenario T1 — Dos polos, delivery, comprobante, confirmación

| # | Escribes | Etapa | Embudo | Debe ocurrir |
|---|---|---|---|---|
| 1 | `Hola` | INICIO → IDENTIFICAR | NUEVO → ATENDIDO | Saluda, **sin** pregunta de calificación (la plantilla no tiene caminos) y sin botones de camino. |
| 2 | `Quiero 2 polos` | IDENTIFICAR → DATOS | ATENDIDO → NEGOCIACION | `buscar_productos` informa la promoción 2×1; `gestionar_pedido(agregar, cantidad 2)`. Pedido: 2 × Polo básico, **subtotal S/ 70.00, descuento S/ 35.00 (2x1 en polos), total S/ 35.00** (aún sin envío). El modelo cita esos montos tal cual. |
| 3 | `Soy Ana Quispe, mi número es 987654321` | DATOS | NEGOCIACION | Guarda nombre y teléfono y pide lo que falta de la entrega: `delivery`, zona y dirección. Lista las zonas válidas (Miraflores, San Isidro, Callao). No pide DNI ni correo (la plantilla solo pide nombres y teléfono). |
| 4 | `Delivery en Miraflores, Av. Larco 123` | DATOS → COBRO | NEGOCIACION | `registrar_datos_cliente` con `tipo_entrega = delivery`, `zona_envio = Miraflores`, `direccion`. El pedido recalcula el envío: **subtotal 70.00, descuento 35.00, envío 8.00, total 43.00**. |
| 5 | (mismo turno o siguiente) | COBRO | CUENTAS_ENVIADAS | `enviar_metodos_pago` con el total de **S/ 43.00**. |
| 6 | Sube `comprobante-ok` | COBRO → VERIFICACION | EN_VERIFICACION | Pedido `Por confirmar`. Como el comprobante no dice S/ 43.00 (es una captura de prueba), aparece **El monto no coincide**: es lo esperado y sirve para ver que la marca no rechaza sola. |
| 7 | En Pedidos: abre el pedido | — | — | Detalle con descuento y envío en los totales, "Delivery · Miraflores", la dirección y la observación. |
| 8 | **Confirmar** con siguiente paso `Te lo entregamos mañana entre 2 y 6 pm` | VERIFICACION → CERRADO | GANADO | Mismo comportamiento que el escenario 5, paso 6. |

Variante **T1b · Envío gratis.** Reinicia y pide `2 mochilas` (S/ 240.00): al elegir zona el envío debe ser **S/ 0.00** (gratis desde S/ 150.00, calculado sobre los bienes después del descuento).

#### Escenario T2 — Rechazo y corrección

Repite T1 hasta el paso 6 y, en Pedidos, **rechaza** con `Falta el comprobante completo, envíalo de nuevo`.

| # | Acción | Etapa | Embudo | Debe ocurrir |
|---|---|---|---|---|
| 1 | Rechazar en Pedidos | VERIFICACION (rechazado) | EN_VERIFICACION | Pedido en `Borrador` con "Fue rechazado" y el motivo. El cliente recibe el aviso con ese motivo. En el debug, las herramientas propias de la etapa son `enviar_metodos_pago` y `gestionar_pedido` (más las globales). |
| 2 | Escribes `Pásame de nuevo las cuentas` | VERIFICACION | EN_VERIFICACION | `enviar_metodos_pago` **sí** reenvía (una vez por rechazo, C-21); una segunda petición sería `omitido`. |
| 3 | Escribes `Mejor agrégame una gorra` | VERIFICACION → COBRO | NEGOCIACION | El cambio del pedido apaga el rechazo vigente: el cliente **sale** de VERIFICACION sin mandar otra foto y vuelve a COBRO. Total nuevo = 43.00 + 25.00 = **S/ 68.00**, calculado por `pricing`. |
| 4 | Sube `comprobante-ok` y confirma en Pedidos | COBRO → VERIFICACION → CERRADO | EN_VERIFICACION → GANADO | Cierra igual que T1. |

#### Escenario T3 — Contra entrega

Pasa la tienda a pago contra entrega (por SQL hasta TASK-435), espera 60 s o reinicia el `api`, y **reinicia la conversación**:

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo -c \
  "UPDATE agent_configs SET process_options = '{\"modalidad_pago\": \"contra_entrega\"}'::jsonb WHERE tenant_id = (SELECT id FROM tenants WHERE name = 'Tienda Demo');"
```

Debe responder `UPDATE 1`. Para volver a pago previo: `process_options = '{}'`.

| # | Escribes | Etapa | Embudo | Debe ocurrir |
|---|---|---|---|---|
| 1 | `Hola, quiero una mochila y una gorra` | IDENTIFICAR → DATOS | ATENDIDO → NEGOCIACION | Pedido: Mochila S/ 120.00 + Gorra S/ 25.00 = S/ 145.00 (sin promoción). |
| 2 | `Ana Quispe, 987654321, delivery al Callao, Jr. Unión 45` | DATOS → COBRO | NEGOCIACION | Envío Callao S/ 12.00; **total S/ 157.00** (145 < 150, no hay envío gratis). |
| 3 | (el modelo) | COBRO | NEGOCIACION | **No** manda cuentas ni pide comprobante (en contra entrega no hay): resume productos, total y entrega y pregunta si confirma. Si llamara a `enviar_metodos_pago`, anótalo como falla. |
| 4 | `Sí, confirmo` | COBRO → VERIFICACION | EN_VERIFICACION | `gestionar_pedido(confirmar)` (status `hecho`); el pedido queda `Por confirmar`. Dice que el negocio lo revisará. |
| 5 | En Pedidos: abre el pedido | — | — | Mensaje "Este pedido se paga contra entrega: no lleva comprobante"; forma de pago "Contra entrega". |
| 6 | **Confirmar** | VERIFICACION → CERRADO | GANADO | "Pedido confirmado. Aviso enviado al cliente." El cliente recibe el aviso de pago/pedido confirmado. |

Variante **T3b · Rechazo contra entrega.** Repite hasta el paso 4 y **rechaza** con `No llegamos a esa zona esta semana`. Esperado: pedido en `Borrador` **sin** "rechazo vigente"; el cliente recibe el mensaje `pedido_rechazado` (*"Ana, revisamos tu pedido #N y hay un detalle: …"*); la etapa **vuelve a COBRO** (no a VERIFICACION) y el motivo aparece en el estado del debug.

Prueba opcional del texto del dueño (el panel no lo edita hasta TASK-435): cambia el mensaje y repite una confirmación.

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo -c \
  "UPDATE agent_configs SET system_messages = jsonb_build_object('pago_confirmado', '{nombre_cliente}, listo: tu pedido #{numero} está confirmado. {siguiente_paso}') WHERE tenant_id = (SELECT id FROM tenants WHERE name = 'Tienda Demo');"
```

### 3.3 Capacita, escenario 6 (opcional) — "Lo pensaré"

Crea una promoción de cierre y repite el escenario 4 hasta el paso 2; en lugar de dar los datos escribe `Lo voy a pensar`.

```bash
docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo -c \
  "INSERT INTO promotions (tenant_id, nombre, descripcion, uso, tipo, valor, aplica_a, vigencia_horas) SELECT id, 'Cupón de cierre 10 %', 'Descuento si decide hoy', 'cierre', 'porcentaje', 10, 'todo', 24 FROM tenants WHERE name = 'Escuela Capacita';"
```

Esperado: el agente ofrece **una sola vez** el descuento y llama a `gestionar_pedido(aplicar_promocion)`; el pedido pasa a **S/ 450.00** (500 − 10 %) y `promos_ofrecidas` queda en los hechos. Si el cliente dice "lo pensaré" otra vez, no la ofrece de nuevo ni alarga el plazo. Bórrala al terminar (`DELETE FROM promotions WHERE nombre = 'Cupón de cierre 10 %'`).

---

## 4. Checklist de aprobación (Giannier)

Con un solo FALLA en **Pedido**, **Pago y comprobante** o **Pedidos (panel)**, el milestone no se aprueba.

**Preparación**
- [ ] El CSV de la tienda importa con 3 filas válidas; las entregas, la promoción 2×1 y el método de pago existen (`1 | 1 | 1`).
- [ ] Capacita tiene su método Yape; la tienda usa la plantilla `productos`.
- [ ] La foto del comprobante se ve en el detalle (con `minio` en `hosts`) o el hallazgo de la sección 7.1 queda anotado.

**Pedido**
- [ ] Capacita E4: el paquete de 5 con 2 temas queda en PEDIDO con "faltan 3"; con 5 pasa solo a DATOS; el tema libre se acepta.
- [ ] T1: 2 polos dan subtotal 70.00, descuento 35.00 y total 35.00; con Miraflores el envío es 8.00 y el total 43.00.
- [ ] T1b: con 2 mochilas el envío es 0.00. T3: 145.00 + Callao 12.00 = 157.00.
- [ ] El agente nunca calcula, redondea ni cambia un monto; los montos que dice coinciden con el pedido del panel.
- [ ] Los datos se piden juntos y solo lo que falta; en Capacita no se pide dirección; en la tienda sí (delivery, zona, dirección).
- [ ] Los datos personales no aparecen en claro en el debug (DNI, correo, teléfono enmascarados).

**Pago y comprobante**
- [ ] Los métodos de pago se envían **una vez** por pedido (E4 paso 6) y de nuevo **una vez** tras un rechazo (T2 paso 2).
- [ ] Una foto que no es comprobante no crea comprobante ni cambia la etapa (E5 paso 1).
- [ ] Un comprobante con otro monto queda **marcado, no rechazado**; el agente nunca dice que el pago está confirmado.
- [ ] En contra entrega no se piden cuentas ni comprobante; `confirmar` deja el pedido por confirmar (T3).
- [ ] Rechazar con comprobante deja VERIFICACION (rechazado); cambiar el pedido saca al cliente de ahí (T2 paso 3); rechazar contra entrega vuelve a COBRO (T3b).

**Pedidos (panel)**
- [ ] El menú **Pedidos** aparece para cualquier miembro (también un operador) y los pedidos de prueba solo salen con la casilla.
- [ ] La lista filtra por estado y por búsqueda; el teléfono sale enmascarado y el nombre abreviado; el detalle los muestra completos.
- [ ] El detalle muestra ítems con selecciones, promoción, envío, datos del cliente, comprobante con datos leídos y **observaciones resaltadas**, e historial.
- [ ] **Confirmar** (con "siguiente paso") y **Rechazar** (motivo de 5 a 300 caracteres) cambian el estado y muestran "Aviso enviado al cliente".
- [ ] El cliente recibe en el simulador el aviso correcto en cada caso (confirmado, pago rechazado, pedido rechazado) y la etapa pasa a CERRADO / GANADO tras confirmar.
- [ ] Confirmar dos veces no duplica nada (5c); **En curso** y **Completado** funcionan en orden.

**Aprobación**
- [ ] **Giannier aprueba el Milestone B.** Hasta que lo marques aquí y lo confirmes en el chat, TASK-433 sigue abierta en `tasks.md` y no se avanza a la Fase C (TASK-434).

Anota debajo cualquier falla con el escenario, el turno y una captura del debug o de Pedidos:

```text
(vacío)
```

---

## 5. Si algo no se parece a lo esperado

| Síntoma | Causa probable |
|---|---|
| La tienda no pregunta por zona o no aplica el 2×1 | `business_info` / `promotions` mal cargados (revisa el `SELECT` final de 2.2.4), o la caché de 60 s. La promoción debe estar creada **después** del catálogo. |
| Capacita pide dirección, o la tienda no la pide | El producto está mal marcado: `es_digital` (Capacita sí, tienda no) o falta la sección `entregas`. |
| "No pudimos cargar la imagen" en el detalle | Falta `127.0.0.1 minio` en `hosts` (sección 1), o el enlace venció (10 min): pulsa "Reintentar". |
| El comprobante sale **No se pudo leer** (`sin_datos`) con una captura clara | El modelo del perfil no acepta imágenes, el tiempo de 8 s se agotó o el proveedor falló. Los logs del `api` lo dicen. No es un fallo de la pantalla. |
| Subes un comprobante y no pasa nada | Etapa distinta de COBRO / VERIFICACION rechazado (el pipeline solo mira imágenes ahí), o el pedido es contra entrega o ya está por confirmar. |
| El cliente no recibe el aviso del operador | El simulador lo dibuja al sondear (cada 3 s); si no sale, revisa que sigas en la misma conversación. Si Pedidos respondió `no_enviado`, ver sección 7.2. |
| Pedidos no muestra el pedido | La casilla de pruebas está apagada, o hay un filtro de estado o fecha activo ("Quitar filtros"). |
| Una tarjeta "Por revisar" dice 0 y hay pedidos pendientes | Las tarjetas siguen el periodo elegido (Hoy / 7 días / 30 días) y nunca cuentan pedidos del simulador (sección 7.3). |

---

## 6. Qué NO está construido todavía (a propósito)

| Tema | Estado | Efecto en la prueba |
|---|---|---|
| **Avisos al negocio** (comprobante nuevo, contra entrega, ventana cerrada, derivación) | Se crean en la base y por correo al dueño (TASK-431), pero **el panel no los muestra** y el simulador no los genera (C-19) | No hay campana ni lista de avisos que revisar; solo con un pedido real de WhatsApp se vería el correo. Lo cubren los tests. |
| **Aviso por WhatsApp real y ventana de 24 h** | Cubierto por tests de API; no se prueba aquí | En el simulador el aviso siempre es "enviado". El caso `ventana_cerrada` requiere un número real de WhatsApp. |
| **Derivación a humano** (`derivar_a_humano`, pausa) | Stub (TASK-443) | Cotización y asesoría no derivan de verdad. |
| **Seguimiento y descarte de borradores** (7 días sin actividad) | TASK-444 | Un borrador rechazado se queda así; nadie escribe por inactividad. |
| **Circuit breaker, memoria y niveles de inteligencia** | TASK-445 | Si el proveedor falla, se ve la respuesta degradada. |
| **API y panel de configuración** (información del negocio, entregas, promociones, proceso, mensajes del sistema, multimedia) | TASK-434 a 441 | Entregas, promociones, contra entrega y textos de aviso se cargan por SQL. |
| **Edición del pedido por el operador, envío de mensajes libres desde el panel, exportar** | No planeado en esta feature | Pedidos solo confirma, rechaza y cambia estado. |
| **Historial de estados propio** | Desvío de §8.25: se arma desde auditoría y comprobantes | Los cambios que haga el sistema (descarte automático) no aparecerán hasta existir su job. |
| **Evals del motor nuevo** | TASK-447 | No se corrieron con estos cambios; hay un pendiente de PR. |
| **Dashboard con ventas** | Feature 010 | Las tarjetas de Pedidos son solo un resumen del periodo. |

---

## 7. Decisiones abiertas que afectan este milestone

Todas están **pendientes de ratificar por Giannier** (detalle en `conciliacion.md §8.19 a §8.26`). Si alguna no te convence, dilo al aprobar.

1. **Imagen del comprobante en desarrollo.** El enlace firmado usa el endpoint interno de MinIO (`minio:9000`). Hace falta un `S3_PUBLIC_ENDPOINT` (o equivalente) para firmar con un host alcanzable por el navegador; hoy se resuelve con `hosts`. En producción depende de cómo se publique el almacenamiento. *Hallazgo de TASK-432.*
2. **`aviso_cliente: no_enviado`.** El contrato solo conoce `enviado` y `ventana_cerrada`; se añadió `no_enviado` para cuando falla el proveedor o no hay número. La pantalla lo muestra como "No pudimos enviar el aviso; avísale tú". §8.25.
3. **"Por revisar" sigue el periodo.** Un pedido por confirmar de hace 8 días no cuenta en "Hoy" (la lista, en cambio, no se limita por periodo). ¿Debe ser "pendientes de siempre"? §8.25.
4. **Quién puede decidir.** Cualquier miembro activo (también `operator`) confirma o rechaza; no hay un permiso aparte para pagos. §8.25.
5. **Correos solo al dueño** (no a `tenant_admin`); `llm_caido` máximo uno por hora. §8.26. Y la **migración 0022** amplió `tenant_notifications` en vez de crear otra tabla. §8.26.
6. **Visión.** Modelo de chat del perfil (no hay un modelo de visión aparte); umbral de "lectura poco segura" de `0.70` y timeout de 8 s son constantes mías; qué modelo corresponde a cada nivel llega con TASK-445. §8.24.
7. **Pricing (§8.19):** el monto mínimo cuenta sobre las líneas a las que aplica la promoción; N×M por línea; el envío gratis se evalúa después del descuento; delivery sin zona vale 0 hasta elegirla.
8. **Un pedido `por_confirmar` no se puede modificar** ni abrir otro mientras el negocio revisa (`OrderLocked`). §8.20.
9. **`MAX_TOOL_ROUNDS = 2`:** buscar y agregar gastan las dos vueltas; registrar datos en cadena no cabe. Subirlo cambia costo y latencia. §8.21.
10. **Datos personales:** el DNI que escribe el cliente queda en `messages.content` y vuelve al modelo en el historial; solo se enmascara en trazas y debug. Un teléfono de 9 dígitos que empieza en 9 se toma como móvil peruano (`+51`). §8.22.
11. **Decisiones de pantalla (TASK-432):** ruta `/pedidos` y `/pedidos/{id}`; la lista oculta los pedidos de prueba por defecto; "Pedidos" ocupa un lugar de la barra inferior del teléfono y "Mi número" pasa a estar solo en el menú lateral; el campo de búsqueda y los filtros se aplican tras una pausa de 300 ms.
12. **Escenario 6 de Capacita:** `tasks.md` pide 4 y 5 en este milestone; se dejó como extra (3.3). Confirma si debe ser obligatorio aquí o en la aceptación (TASK-449).

---

## 8. Después de aprobar

1. Giannier confirma por el chat: "Milestone B aprobado".
2. Recién entonces se marca `[x]` en TASK-433 de `tasks.md` y se avanza a la Fase C (TASK-434 en adelante).
3. Los tenants `Escuela Capacita` y `Tienda Demo` se conservan: el Milestone C (TASK-442) y la aceptación (TASK-449) los reutilizan. Si quieres limpiar, "Reiniciar conversación" borra los pedidos y comprobantes de prueba de esa conversación.
