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
