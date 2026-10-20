# Prueba con un número real de WhatsApp por túnel (TASK-459)

**Cuándo:** después de aprobar el Milestone B y antes de la Fase C. **Quién:** Giannier, con su número.
**Para qué:** Leo nunca se probó con un número real; es la parte con más riesgo de sorpresas. Además
deja los webhooks reales de YCloud que reemplazan los fixtures escritos a mano (TASK-456) y cierra el
milestone humano de TASK-209 (002).

No se despliega nada en el VPS: Leo corre en tu PC (WSL2) y un túnel HTTPS le da una URL pública para
que YCloud le entregue los mensajes.

---

## 1. Antes de empezar

- Leo levantado como en el Milestone B (`make up`, base en la revisión más reciente), con
  `LLM_PROFILE=openai` y tu `OPENAI_API_KEY` en `.env`.
- Tu cuenta de YCloud con el número de WhatsApp Business conectado, y a mano: la **API key** de YCloud y
  el **secreto de webhook** que YCloud muestra al crear el endpoint.
- Un segundo teléfono (el "cliente") que escriba al número del negocio.
- Una herramienta de túnel en tu PC (no es una dependencia del repo):
  - **Cloudflare Tunnel** (`cloudflared`, gratis, sin cuenta para una URL temporal), o
  - **ngrok** (cuenta gratuita).

## 2. Abrir el túnel

En otra terminal de WSL2, apuntando al backend (puerto 8000):

```bash
cloudflared tunnel --url http://localhost:8000
# o
ngrok http 8000
```

Copia la URL `https://...` que imprime. La **URL del webhook** es esa URL más `/webhooks/ycloud`, por
ejemplo `https://algo.trycloudflare.com/webhooks/ycloud`. Déjala abierta durante toda la prueba: si se
cierra, la URL cambia y hay que actualizar YCloud.

Comprueba que llega: `curl https://<tu-url>/api/v1/health` debe responder `ok`.

## 3. Configurar el secreto y conectar el número

1. Ya no hay `YCLOUD_WEBHOOK_SECRET` (decisión 2026-10-20, `002-canal-whatsapp/plan.md`): cada negocio
   conecta su propia cuenta de YCloud y Leo crea el webhook en ella.
2. Con el túnel (o el VPS) arriba y `API_PUBLIC_BASE_URL` apuntando a su URL pública, entra al panel del
   negocio → **Mi número**, pega la clave API de YCloud, el número y el nombre.
4. Hazte administrador de plataforma si aún no lo eres: `make admin EMAIL=<tu correo>`.
3. Con el negocio que vas a probar (Tienda Demo o Escuela Capacita), verifica tu teléfono de contacto
   y conecta el número como en el paso 2. No hay aprobación en `/admin`.
5. Activa el asistente del negocio (interruptor del panel) y deja el **Modo pruebas** como está.

## 4. Guion de prueba

Desde el teléfono "cliente", escribe al número del negocio. Anota la hora de cada mensaje y de cada
respuesta (para el tiempo de respuesta).

| # | Qué haces | Qué debe pasar |
|---|---|---|
| 1 | `Hola` | Responde en menos de 10 s, en una o dos burbujas, con el saludo del negocio |
| 2 | Toca un botón o una lista si el agente los manda | La opción se registra; en el panel de depuración del pedido se ve el camino |
| 3 | Arma un pedido como en el escenario T1 o el 4 | Igual que en el simulador: precios, promoción y envío correctos |
| 4 | Da tus datos | Pide solo lo que falta; tu número se guarda en formato +51 |
| 5 | Recibe los métodos de pago | Llega el texto de Yape (y el QR si lo cargaste) |
| 6 | Envía una foto de comprobante | El pedido aparece **Por confirmar** en `/pedidos` (sin la casilla de pruebas: es real) y te llega el correo de comprobante nuevo |
| 7 | Confirma desde Pedidos | Al teléfono cliente le llega "tu pago fue confirmado" |
| 8 | Envía una nota de voz | Responde con el texto de "solo leo texto" del negocio |
| 9 | Si puedes, entra por un anuncio de clic a WhatsApp | El hecho `entrada_anuncio` queda guardado (referral) |
| 10 | No escribas por 24 h y confirma otro pedido | El panel dice "ventana cerrada" y no se envía nada |

## 5. Medir y capturar

- **Tiempo de respuesta:** del paso 1 al 7, anota segundos entre tu mensaje y la respuesta. El objetivo es
  p95 menor a 10 s.
- **Webhooks reales:** cada entrega queda guardada cruda en `webhook_events`. Al terminar, pásame el
  resultado de esta consulta para reemplazar los fixtures (texto, botón, lista, imagen, audio, referral y
  estados de entrega):

  ```bash
  docker compose -f deploy/docker-compose.dev.yml exec -T postgres psql -U leo -d leo -c \
    "SELECT created_at, payload FROM webhook_events ORDER BY created_at DESC LIMIT 50;"
  ```

  Antes de pasármelo, revisa que no tenga datos de terceros: son tus dos teléfonos.

## 6. Qué me reportas

- Qué pasos salieron como dice la tabla y cuáles no (con captura si algo falla).
- Los tiempos de respuesta.
- La salida de la consulta de webhooks.

Con eso corrijo lo que falle, reemplazo los fixtures (TASK-456) y marco TASK-209 y TASK-459.
