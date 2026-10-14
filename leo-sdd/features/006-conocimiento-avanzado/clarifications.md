# Clarificaciones — 006 Conocimiento avanzado

Decisiones tomadas al construir la feature (2026-10-14), con la lectura conservadora cuando la spec no lo
decía (CLAUDE.md, "Reglas que no están en la spec"). Las que tocan costo del LLM van marcadas **para
ratificar**.

## C-101: Deduplicación por fuente, no entre fuentes (RF-003)
El hash (`content_hash`, SHA-256 del texto sin mayúsculas ni espacios repetidos) es único **por fuente**:
un bloque que el mismo documento repite se embebe una vez. Entre fuentes no se borra nada: si dos fuentes
tienen el mismo párrafo y se borra una, la otra tiene que seguir sabiéndolo. La búsqueda muestra una sola
copia (la de la fuente de mayor prioridad).

## C-102: Editar una fuente
- Se editan como texto solo `faq`, `policy` y `text`. Un PDF se reemplaza subiendo otro; una web se vuelve
  a leer con "Actualizar desde mi web".
- El texto viejo sigue respondiendo hasta que el nuevo está indexado: el cambio de chunks es una sola
  transacción (`replace_for_source`). Si el reindexado falla, el agente sigue con la versión anterior y el
  panel lo dice ("El asistente sigue usando la versión anterior").
- Mientras una fuente se procesa, editarla o reprocesarla responde 409: no se compite con el worker.
- Al plan solo se le cobra la diferencia de caracteres.
- `POST /{id}/resync` sirve para todo tipo: vuelve a leer la web o reintenta un documento que falló.

## C-103: Re-chunk de lo existente
`make reindex` ahora también toma las fuentes con chunks anteriores a la 006 (sin `content_hash`) y las
reprocesa por el camino normal del worker. Es manual, como el resto del comando.

## C-104: Tamaño de chunk
Se mantiene en 1000 caracteres con 150 de solape (unos 280 tokens en español), algo por debajo de los
300 a 500 tokens del RF-004. Subirlo aumenta los tokens que la tool de conocimiento pone en cada turno, y
eso es costo del LLM. Se mide con el recall@5 (TASK-604) antes de proponer otro tamaño.

## C-105: OCR página por página, sin dependencias nuevas (HU-01) — costo para ratificar
- Una página sin capa de texto (menos de 20 caracteres) se manda **sola**, como PDF de una página, al
  modelo de visión (`vision_model`, el mismo de los comprobantes). El modelo ve la página como imagen,
  así que no hace falta rasterizar: `pypdf` ya separa las páginas y no entra ninguna librería nueva.
- La respuesta es JSON con esquema (`legible`, `texto`): transcribe, no resume. Lo leído se indexa y cuenta
  contra los caracteres del plan como cualquier texto (RF-002).
- Una página que el modelo no lee (borrosa, en blanco, error o timeout) se reporta por número y las demás
  se indexan igual. Si no se lee ninguna, la fuente falla con un mensaje que dice qué hacer.
- Un documento mixto solo manda al modelo las páginas escaneadas.
- **Límite de 30 páginas por documento, siempre**, aunque el cobro esté apagado: no es un límite de plan
  sino un tope de gasto del modelo. Las páginas de más se reportan como "sin leer por límite".
- **Costo medido con el modelo real** (`gpt-5.4-mini`, el fixture de la carta escaneada): unos 1 100
  tokens de entrada y 75 a 150 de salida por página. Un documento de 30 páginas escaneadas son unos 33 000
  tokens de entrada y hasta 4 500 de salida, una sola vez por subida. Los tokens quedan en el reporte de la
  fuente. **Para ratificar**: el tope de 30 páginas y que el OCR lo pague la plataforma (como los
  embeddings).
- Sin modelo que lea documentos (perfil offline o `VISION_ENABLED=false`), un escaneado falla con un
  mensaje claro, como antes.

## C-106: Lectura de la web del negocio (HU-02)
- Desde la dirección que escribe el dueño se recorre en anchura **el mismo sitio** (el dominio con o sin
  `www.`), solo páginas HTML, hasta 20, una a la vez. Se respeta `robots.txt` con el agente `LeoBot`: si no
  existe (4xx) se lee todo, y **si no se puede leer (5xx o error de red) no se lee nada**. Una página con
  `noindex` se omite.
- Se quitan scripts, estilos, menús de navegación, botones y formularios. Una línea que se repite en la
  mitad o más de las páginas (pie, menú), con al menos 3 páginas, se guarda una sola vez. Cada página entra
  como una sección con su título y su dirección.
- Solo HTML estático (C-001): una web armada con JavaScript se lee vacía y la fuente falla diciendo que se
  pegue el texto o se suba un PDF.
- Prioridad por defecto 50, debajo de lo que el negocio escribió (100) y de sus PDF (90) (C-003).
- El texto leído queda en la fuente (para mostrarlo y regenerar chunks) y cuenta contra los caracteres del
  plan como cualquier fuente. Volver a leerla es solo manual (C-002).
- **Seguridad (SSRF).** La dirección la escribe un cliente, así que el lector solo llega a direcciones
  públicas: resuelve el dominio y rechaza loopback, redes privadas, link-local (incluido el metadata de la
  nube), reservadas y multicast, **en cada redirección** (máximo 5). Solo http y https en sus puertos por
  defecto, 10 s por pedido y 2 MB por página. El user agent dice quién lee y para qué. Riesgo residual
  conocido: un dominio que cambie de dirección entre la verificación y la conexión (DNS rebinding).
- **Verificación:** las reglas del lector y del recorrido se prueban sin red (transporte simulado). La
  prueba con la web real de un cliente de Zentek360 es el milestone humano de la TASK-604: desde el entorno
  de desarrollo no hay salida a webs públicas.
