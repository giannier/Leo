# Referencia: Escuela Capacita v21 (n8n)

Código real de los nodos del agente v21, que ya corre en producción. Sirve para portar a Python, no para copiarlo tal cual.

| Archivo | Qué es | Se usa en |
|---|---|---|
| `calc21.js` | Code node *Calcular etapa*: orden de etapas y mapa de embudo | TASK-410 |
| `fw21.js` | Code node *Formato WhatsApp1*: 1–2 burbujas, sin LLM | TASK-416 |
| `ce21.js`, `ec21.js` | *contexto entrada1* y *estado conversacion1*: arman el bloque ESTADO e interpolan las instrucciones de la etapa | TASK-414 |
| `etapas.json`, `etapas_capacita.sql` | Instrucciones por etapa de Capacita | TASK-409 (plantilla `certificaciones`) |
| `harness21.js` | 18 escenarios de etapa + 7 del formateador. `node harness21.js` → "Todo OK" | TASK-410, TASK-416 (traducir a pytest) |

Diferencias con Leo: en n8n los hechos vivían en claves de Redis (`sent:{chat}:*`). En Leo son hechos guardados más derivados (C-03). CARRITO pasa a llamarse PEDIDO y PROGRAMA pasa a ASESORIA.
