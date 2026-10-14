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
