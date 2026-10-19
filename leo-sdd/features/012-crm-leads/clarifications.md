# Clarificaciones — 012 Mini-CRM de leads

## C-101: Ratificación y alcance (Giannier, 2026-10-18)
"Continúa con el CRM, necesito todo el proyecto terminado". Se construye antes que la 011 (campañas), así
que lo que la spec toma de la 011 (señal `purchase_interest`, campaña de origen, etiquetas) se adapta o
espera a la 011.
**Registrado por:** Claude — ratificado en el chat

## C-102: El lead se mueve con el proceso de venta, sin herramienta nueva para el agente
La spec pedía la tool `actualizar_lead`. Leo ya calcula la etapa de cada conversación y guarda cada
cambio en un solo lugar (`StateRepo.save`, con su causa). El lead se sincroniza ahí, de forma
determinista: sin cambiar el prompt, sin evals con el modelo real y sin costo de LLM por mensaje
(lo que Giannier pidió cuidar). Mapeo de la etapa del proceso a la etapa del CRM:

| Etapa del proceso | Etapa del CRM |
|---|---|
| INICIO, CALIFICAR, ANUNCIO | (sin lead: saludo o calificación) |
| IDENTIFICAR, solo si el cliente ya vio productos (`vio_productos`) | nuevo |
| PEDIDO, DATOS, ASESORIA | interesado (monto estimado = total del pedido) |
| COBRO, VERIFICACION | por_pagar (monto estimado) |
| CERRADO (pedido confirmado) | ganado (monto real = total del pedido confirmado) |
| `pedido_descartado` (el borrador venció sin respuesta) | perdido, motivo "dejó de responder" |

Así "hola, ¿horario?" no crea un lead (C-002 de la spec): hace falta interés en productos.
**Registrado por:** Claude — por ratificar

## C-103: Precedencia: la persona manda
- Lo automático solo **avanza** por nuevo → interesado → por_pagar → ganado; nunca retrocede.
- Si una persona movió el lead, lo automático ya no lo toca salvo con evidencia: un pedido (por_pagar) o
  un pedido confirmado (ganado), y solo hacia adelante.
- Un lead ganado o perdido es definitivo: actividad nueva de ese contacto crea un lead nuevo enlazado al
  anterior (RF-002). "No es lead" lo descarta sin contarlo como perdido.
**Registrado por:** Claude — por ratificar

## C-104: Sin dependencias nuevas
La spec proponía dnd-kit para arrastrar tarjetas. Sin dependencias nuevas (Giannier, 2026-10-15): se
arrastra con el drag & drop nativo del navegador en computadora, y cada tarjeta tiene "Mover a…", que
funciona en el celular (donde arrastrar no anda bien) y con teclado.
**Registrado por:** Claude — por ratificar

## C-105: Lo que espera a la 011
Campaña de origen y filtro por etiqueta. Mientras tanto la tarjeta muestra el **origen** que Leo ya
conoce: el anuncio por el que llegó (`entrada_anuncio`) o "directo".
**Registrado por:** Claude — informativa

## C-106: Montos y roles
Montos con Decimal y en la moneda del pedido. Un operador no ve montos (igual que en Resultados, 010
RF-004). Configurar etapas y motivos es de quien administra la configuración; mover, asignar, anotar y
descartar lo puede hacer cualquier miembro.
**Registrado por:** Claude — por ratificar

## C-107: Las conversaciones del simulador no crean leads
Igual que el resto de los datos de prueba (`is_sandbox`), quedan fuera del CRM.
**Registrado por:** Claude — informativa

## C-108: Métricas en vivo, sin copia en metrics_daily
Las métricas del CRM (activos por etapa, nuevos, ganados, perdidos por motivo, cuántos llegaron a cada
etapa, conversión, días promedio para ganar y valor del embudo) se calculan al pedirlas desde `leads` y
`lead_events`, que tienen índices por tenant. Copiarlas en `metrics_daily` exigiría una migración y otro
cálculo nocturno sin ganancia con los volúmenes del piloto. Si Resultados (010) o la 013 las necesitan por
día, se agregan columnas al rollup entonces.
**Registrado por:** Claude — informativa

## C-109: Anonimización (RF-008)
El lead no guarda nombre ni teléfono: los lee del contacto. Al anonimizar un contacto, sus tarjetas, el CSV
y el historial dejan de mostrar sus datos, y montos y eventos quedan para métricas. Hoy Leo no tiene un
"borrar datos de un contacto" por pedido del titular (F007 no lo trae); cuando exista, debe borrar también
las notas de sus conversaciones y el `product_interest` del lead.
**Registrado por:** Claude — por ratificar
