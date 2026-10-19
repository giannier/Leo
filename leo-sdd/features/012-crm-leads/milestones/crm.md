# Milestone 012 — CRM de leads (TASK-1201..1206)

Fecha: 2026-10-18. Verificado por Claude con pruebas automáticas (sin llamadas al modelo real: el CRM no
toca el prompt ni las tools del agente, C-102). Giannier lo prueba a mano con el número real.

## Qué hay
- **Tablero** (`/crm`): columnas Nuevo → Interesado → Por pagar → Ganado → Perdido, más hasta tres etapas
  propias. Cada tarjeta: nombre, producto de interés, monto, días en la etapa y a quién está asignada.
- **Se mueve solo** con el proceso de venta (pidió productos → Nuevo; armó pedido → Interesado; se le
  enviaron los medios de pago → Por pagar; pedido confirmado → Ganado; borrador vencido sin respuesta →
  Perdido "No respondió"). Solo hacia adelante; si una persona lo movió, solo la evidencia lo mueve.
- **Corrección a mano**: arrastrar la tarjeta (computadora) o "Mover a…" en cada tarjeta (celular y
  teclado). Pasar a Perdido pide el motivo.
- **Detalle del lead**: historial (quién lo movió y por qué), notas, asignar, "Abrir conversación" (lleva a
  la Bandeja con esa conversación abierta) y "No es lead".
- **Hoy**: leads sin actividad hace 3 días o más, para llamar o escribir.
- **Indicadores**: valor del embudo, ganados y perdidos (30 días), conversión. Un operador no ve montos.
- **Configurar embudo**: renombrar, agregar etapas propias, apagar Interesado/Por pagar, motivos de
  pérdida. **Descargar CSV**. Búsqueda por nombre, teléfono o producto; filtro "Asignados a mí".
- En vivo: el tablero se refresca con los eventos de la bandeja.

## Evidencia
- Backend: `tests/unit/test_crm_rules.py`, `tests/integration/test_crm_leads.py`,
  `tests/integration/test_crm_api.py`, `tests/isolation/test_crm_isolation.py`; suite completa en verde.
- e2e `frontend/e2e/crm.spec.ts` en escritorio y Android: tablero, mover con "Mover a", Hoy, nota,
  asignar, abrir conversación, perder con motivo, filtro "a mí", etapa nueva y arrastre (escritorio).
  También en verde `bandeja`, `asistente`, `catalogo` y `navigation`.
- Capturas: `crm-chromium.png` (tras arrastrar a Carla a Ganado), `crm-android.png`.

## Decisiones
**Bloqueantes:** ninguna.

**Para ratificar:**
- C-102: el lead se mueve desde el proceso de venta, sin tool `actualizar_lead` (sin costo de LLM ni evals).
- C-103: precedencia, la persona manda; ganado/perdido es definitivo y lo que sigue abre un lead nuevo.
- C-104: arrastre nativo del navegador en vez de dnd-kit (sin dependencias nuevas).
- C-106: montos con Decimal y en la moneda del pedido; el operador no ve montos.
- C-109: anonimización; falta un "borrar datos del contacto" en Leo, y cuando exista debe borrar notas
  y producto de interés.

**Informativas:**
- C-105: campaña de origen y etiquetas esperan a la 011; la tarjeta muestra el anuncio de entrada o "directo".
- C-107: el simulador no crea leads.
- C-108: métricas en vivo, sin copia en `metrics_daily`.
- En el celular el CRM está en el menú lateral, no en la barra inferior (que ya tiene sus cinco accesos).

## Milestone humano pendiente
Una semana de uso real con el piloto: revisar si se crean leads de más (curiosos) o de menos.
