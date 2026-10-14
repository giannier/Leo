# Aceptación de la 004 — Motor de ventas (TASK-449)

**Fecha:** 2026-10-14 · **Verifica:** Claude, con el modelo real (`gpt-5.4-mini`), por la instrucción de Giannier
del 2026-10-13 ("termina de construir todo el proyecto y al final conecto el número"). La aprobación humana de
los tres negocios y el merge siguen siendo de Giannier (TASK-449 es un milestone 🧑): esto es la evidencia para
esa revisión.

## Criterios de aceptación de la spec

| Criterio | Resultado | Evidencia |
|---|---|---|
| **Capacita**: las 6 conversaciones de v21 | ✅ en la última corrida de cada recorrido (variación de ±1 caso entre corridas, ver abajo) | Escenarios 1, 2, 3 y 6: [aceptacion-recorrido-automatico.md](aceptacion-recorrido-automatico.md) (12/12, dos corridas seguidas). Escenarios 4 y 5: [B-recorrido-automatico-fase-e.md](B-recorrido-automatico-fase-e.md) |
| **Tienda**: 3 productos con promoción, delivery, datos, cobro, comprobante y confirmación | ✅ | Pedido de 2 polos (2x1) + gorra: 95.00 − 35.00 + envío 8.00 = 68.00; comprobante; el operador confirma; CERRADO |
| **Zentek360**: cotización derivada con resumen y hosting de precio fijo vendido | ✅ | Deriva con motivo `cotizacion` y resumen; hosting S/ 350 llega a cobro, comprobante y confirmación |
| Ningún recurso dos veces, ningún turno con más de 3 salientes | ✅ | Comprobado turno a turno en los tres negocios y en las 24 conversaciones de los evals |
| Evals de flujos ≥ 90 % | ⚠️ Ver nota | 24/24 en dos corridas; 23/24 en otras tres, cada vez con un caso distinto |
| Suite completa, aislamiento, mypy, ruff | ✅ | 3 380 tests en verde; 601 de aislamiento; mypy y ruff limpios |
| Cobertura ≥ 80 % en módulos nuevos | ✅ | Total 95 %; motor de ventas 97,65 %; agente y tools 97 %; canales 96,5 %; cobro 94,2 % (`scripts/coverage_gate`) |
| Un usuario nuevo configura un tenant en ≤ 30 min | ⏳ Giannier | Requiere una persona que no participó del desarrollo |

**Nota sobre el 90 %.** Con 8 casos por plantilla, "90 % por plantilla" exige 8 de 8: un solo traspié del
modelo en una corrida reprueba la plantilla. En cinco corridas tras las correcciones el total fue 24/24 dos veces y
23/24 tres veces, con un caso distinto cada vez (paquete de 5, métodos de pago, cotización de una web). No es una
regresión fija sino la variación del modelo `economico`. Para ratificar: subir a 10 casos por plantilla (el 90 %
permite un traspié) o aceptar el criterio por corrida de 24 (≥ 90 % = 22/24).

## Lo que la aceptación encontró y se corrigió

Además de lo de los evals de flujos (C-24):

- **Promoción de cierre inaplicable.** El ESTADO mostraba el nombre, no el `promocion_id`; la herramienta exigía
  el id y respondía error. Ahora el ESTADO muestra el id y la herramienta acepta también el nombre exacto.
- **ANUNCIO y CALIFICAR retenían la información por el nombre.** Plantillas v2 (migración 0033).
- **Servicios de precio fijo:** "quiero contratar" ahora agrega en el mismo turno.
- **Varios productos en un mensaje:** la herramienta recuerda agregar los demás en el mismo turno.
- **Regla de plataforma 1:** antes de decir que algo no es del negocio, busca en el catálogo (una web para un
  restaurante se tomaba como "otro tema").
- **Paquete elegido antes:** si el cliente ya dijo qué paquete quiere, se agrega sin volver a mostrar los
  paquetes, y `elegir` sin paquete le dice al modelo que lo agregue primero (decía "ya incluye" sin haberlo
  registrado).
- **`GET /assistant/categories` daba 500** con productos (TASK-446) y la búsqueda vectorial podía dejar sin
  resultados a un negocio (TASK-445): corregidos.

## Playwright (TASK-448)

77 de 84 en verde en la corrida completa; después se corrigió el spec nuevo de derivación (10/10 en
`pedidos.spec.ts`). Los que fallan, con su causa:

- **Subida a la biblioteca** (2, escritorio y teléfono): este entorno no tiene MinIO; con `make up` sí.
- **Panel de operación en teléfono** (3, `admin.spec.ts` en Android): el clic cae sobre otra tarjeta mientras la
  lista se acomoda. Pasa en escritorio. La base de desarrollo acumula decenas de solicitudes de corridas
  anteriores; queda anotado para revisarlo con una base limpia.

## Decisiones

**Bloqueantes:** ninguna.

**Para ratificar (Giannier):**
1. Criterio del 90 % de los evals de flujos (nota de arriba).
2. Plantillas de proceso v2 (migración 0033): cuatro reglas reescritas (C-24 y esta página).
3. 017 C-14 (el agente nunca concede descuentos), contenido de los rubros v1, umbrales de similitud y topes
   de prompt (C-15, C-16), y qué modelo y cuántos créditos van en los niveles `medio` y `mejor` (TASK-445).
4. Seguimientos (C-13): vienen apagados por negocio; el seguimiento cuenta como respuesta del agente.

**Informativas:**
- El simulador no puede simular la entrada por anuncio; el escenario 3 se probó por el canal real.
- Los datos de entrega dichos antes de tener pedido no se guardan en el pedido (C-24).
- TASK-459 (número real) queda para el final, por decisión de Giannier.

## Cómo repetirlo

```
RUN_ACCEPTANCE=1  LLM_PROFILE=openai OPENAI_API_KEY=... uv run python -m pytest backend/tests/integration/test_zz_acceptance_live.py -s
RUN_MILESTONE_B=1 LLM_PROFILE=openai OPENAI_API_KEY=... uv run python -m pytest backend/tests/integration/test_zz_milestone_b_live.py -s
LLM_PROFILE=openai OPENAI_API_KEY=... make evals
```
