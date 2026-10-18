# Milestone 018 — Experiencia simple (evidencia automática)

Verificado por Claude con pruebas automáticas y Playwright (sin modelo real: `LLM_PROFILE=offline`, para
no gastar crédito; ningún prompt cambió). Giannier lo prueba a mano al conectar el número.

## Capturas
- `catalogo-chromium.png`, `catalogo-android.png`: lista del catálogo con foto, precio, etiquetas y acciones.
- `editor-android.png`: editor del producto con su foto principal.
- `asistente-chromium.png`: menú lateral del asistente con el estado de cada sección.
- `respondio-android.png`: "¿Respondió bien?" con motivo, por qué respondió así y atajos.

## Pruebas
- Backend: 3549 en verde + el test del recordatorio corregido (aislamiento de datos e2e); lint y mypy limpios.
- e2e nuevos: `catalogo.spec.ts`, `asistente.spec.ts` (menú y objetivo/derivación), `respondio-bien.spec.ts`,
  en escritorio y Android.
- Suite e2e completa: resultado al pie cuando termine.

## Decisiones
- **Bloqueantes:** ninguna.
- **Para ratificar:** C-02 (no copiar el texto libre de Ventor), C-03 (oferta y variantes fuera, tocan
  dinero), C-04 (fotos = recursos multimedia), C-05 (la valoración sobrevive a reiniciar el chat), C-06
  ("¿Respondió bien?" sin IA).
- **Informativas:** C-01 (alcance acordado). Para subir fotos en local hace falta el almacenamiento S3 del
  `docker compose` (minio); sin él, la subida falla con "No pudimos guardar la foto".
