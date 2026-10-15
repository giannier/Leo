# Milestone 006: Conocimiento avanzado

**Tasks:** TASK-601 a TASK-604 · **Quién verifica:** Claude, con pruebas automáticas, el modelo real y
Playwright (instrucción de Giannier del 2026-10-14). **Fecha:** 2026-10-14.

El milestone humano de la TASK-604 ("probar con la web real de un cliente de Zentek360") **queda para
cuando se conecte el número**: desde el entorno de desarrollo no hay salida a webs públicas (el proxy
rechaza las conexiones y no hay DNS).

## Veredicto

**Aprobado por pruebas automáticas**, con dos puntos para ratificar: el costo del OCR (C-105) y un caso
de los evals de flujos de la 004 que falla de forma intermitente desde antes de esta feature (abajo).

## Historias y requerimientos

| Qué | Evidencia | Resultado |
|---|---|---|
| HU-01 OCR de escaneados | `test_knowledge_ocr.py` con PDF armados de escaneos JPEG reales (página sin capa de texto): las páginas legibles se indexan y las ilegibles se reportan por número; un documento mixto solo manda al modelo las páginas escaneadas; tope de 30 páginas; un error del modelo en una página no pierde las demás. **Modelo real:** transcribe la carta del fixture ("Doña Rosa", horarios, distritos, "S/ 6", "48 horas", "Yape") y marca ilegible la página borrosa, con unos 1 100 tokens de entrada por página | ✅ |
| HU-02 Ingesta web | `test_web_reading.py`: mismo sitio con y sin `www.`, `robots.txt` (si no se puede leer, no se lee nada), `noindex`, nada que no sea HTML, tope de páginas, pie repetido guardado una vez, web armada con JavaScript leída vacía con mensaje claro. **SSRF:** loopback, redes privadas, link-local (metadata), `localhost`, IPv6 local, puertos y esquemas no estándar, y redirección hacia adentro, rechazados antes de conectar. `test_knowledge_v2.py`: alta por API, lectura en el worker, "Actualizar desde mi web" solo a mano | ✅ (web real pendiente) |
| HU-03 Re-indexación | El texto viejo responde hasta que el nuevo está listo (un solo cambio transaccional). Si el reindexado falla, se queda la versión anterior y el panel lo dice. Editar o reprocesar mientras se procesa responde 409 | ✅ |
| HU-04 Recall@5 | `evals/test_knowledge_recall.py` con embeddings reales (`text-embedding-3-small`): panadería **0,88** (recall@1 0,81), instituto **1,00** (recall@1 0,80), sobre el umbral de 0,8. Corpus chico (unos 10 fragmentos por negocio): por eso también se exige recall@1 ≥ 0,7 (C-108) | ✅ |
| HU-05 Citas | Las fuentes usadas quedan en la traza y se ven en el simulador y en la bandeja ("Respondió usando: …") | ✅ |
| RF-001 tipo `web` | Migración 0035 (sube y baja) | ✅ |
| RF-002 OCR cuenta contra el plan | El texto leído suma a `char_count` como cualquier fuente | ✅ |
| RF-003 Deduplicación | Hash por fuente (embebido una vez) y una copia por búsqueda entre fuentes, la de mayor prioridad (C-101) | ✅ |
| RF-004 Chunking | Ya era por estructura con solape; tamaño sin cambios, por costo (C-104) | ✅ |
| C-003 Prioridad | Configurable en el panel; el fragmento de la fuente más confiable va primero y la tool dice que vale el primero | ✅ |

Aislamiento: los endpoints nuevos (detalle, editar, reprocesar, fragmentos) responden 404 con la fuente de
otro negocio y no la tocan, y `retrieve` nunca devuelve un fragmento de otro negocio
(`tests/isolation/test_knowledge_isolation.py`).

## Corridas

- **Backend:** 3 462 pasan, 3 omitidas (en vivo); hubo una falla de entorno, ya corregida (abajo). Ruff y
  mypy limpios.
- **Frontend:** `tsc` y `eslint` limpios. Playwright `e2e/conocimiento.spec.ts`, en escritorio y en Android,
  con el worker real: agregar una política, esperar a "Listo", cambiar la prioridad, editar el texto y
  verlo reindexado en "Ver qué lee el asistente", rechazar una dirección que no es web, eliminar. La
  bandeja vuelve a pasar con las fuentes en la burbuja.
- **Evals del agente** (cambió el texto que la tool le da al modelo): conversaciones doradas en verde;
  flujos de venta **23/24**: productos 8/8, servicios 8/8, certificaciones 7/8.

### El caso intermitente de certificaciones

"Paquete de 5 con 2 temas": cuando el cliente pide el paquete de 5 **antes** de decir los temas, a veces el
agente arma un paquete de 2. Falló en dos corridas seguidas con la 006. Para descartar una regresión se
corrió sobre el commit anterior a la 006 (`d32c065`, sin la 006): pasó una vez y falló la otra con la misma
conducta. No usa la tool de conocimiento. Es la variación que ya registra la aceptación de la 004 ("23/24 en
otras tres, cada vez con un caso distinto"). **Propuesta:** que `elegir_camino` guarde el tamaño de paquete
que el cliente nombra en la calificación, para que no dependa de que el modelo lo recuerde. Es un cambio de
la 004 (proceso y prompt), así que espera la ratificación del criterio del 90 %.

### La falla de entorno

`test_layers.py::test_the_rubro_faq_is_found…` falló porque un worker de desarrollo levantado para las e2e
ya había embebido los rubros publicados en la base compartida. La prueba ahora parte de cero dentro de su
transacción.

## Decisiones

**Bloqueantes:** ninguna.

**Para ratificar:**
- C-105: OCR con el modelo de visión, pagado por la plataforma, con tope de 30 páginas por documento
  (unos 33 000 tokens de entrada como máximo por subida).
- El caso intermitente de certificaciones y su propuesta (arriba).

**Informativas:** C-101 a C-104 y C-106 a C-108 en [clarifications.md](../clarifications.md).
