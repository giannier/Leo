# Milestone C — Configuración en capas (Fase C de la 004 = Fase 1 de la 017)

**Task:** TASK-442 · **Quién verifica:** Claude, con el modelo real (instrucción de Giannier del 2026-10-13:
"termina de construir todo el proyecto y al final conecto el número"; los milestones intermedios se
verifican con pruebas automáticas contra el modelo real y Playwright, y la evidencia queda aquí).
**Fecha:** 2026-10-14 · **Modelo:** perfil `openai` (`gpt-5.4-mini`, embeddings `text-embedding-3-small`).

## Veredicto

**Aprobado.** El recorrido automático pasó **14 de 14** comprobaciones en dos corridas seguidas, después de
una corrección (el trato de usted, abajo). Las pantallas pasan en Playwright, salvo la subida de archivos,
que necesita MinIO. Los evals de catálogo y del agente están en verde.

## Qué se probó

El script `backend/tests/integration/test_zz_milestone_c_live.py` (se corre a mano con `RUN_MILESTONE_C=1`)
recorre la API como lo haría el negocio y llama al modelo real. La transcripción completa de la segunda
corrida está en [C-recorrido-automatico.md](C-recorrido-automatico.md).

| # | Criterio de la Fase 1 (017) | Resultado |
|---|---|---|
| C1 | Un instituto nuevo recibe la plantilla `certificaciones` sugerida por su rubro; perfil (usted, pocos emojis, saludo), FAQ y Yape con titular se guardan; el resumen del asistente avanza (41 %, siguiente paso "primera situación") | ✅ |
| C2 | El instituto ya no habla como una tienda: dice "certificado", no "producto", "carrito" ni "stock" (la queja que originó la 017) | ✅ |
| C2 | Trata de usted, como dice el perfil | ✅ tras la corrección |
| C3 | Una regla del rubro apagada sale del prompt (vista previa) y la depuración del simulador la lista en `reglas_apagadas` | ✅ |
| C4 | Una situación en borrador no entra sola; probada en el simulador entra y se cumple (pide RUC y razón social); activada, entra sola por similitud (distancia 0.39, umbral 0.55) | ✅ |
| C5 | Una versión nueva del rubro `instituto` no se publica sin evals (409); los 10 evals pasan con el modelo real; publicada, el negocio ve la novedad y al marcarla vista desaparece | ✅ |
| C6 | Un turno con las cuatro capas y una situación usa unos 2 600 tokens de entrada (tope fijado: 6 000) | ✅ |

### Pantallas (Playwright, `frontend/e2e/asistente.spec.ts`)

Hay 5 pruebas y 4 pasan:

- información del negocio llega al prompt;
- apagar una regla la saca de la vista previa;
- situación en borrador → simulador → activa;
- el wizard ofrece las situaciones del rubro.

La quinta, subir una imagen a la biblioteca, responde 503 en este entorno porque no hay MinIO. Con
`make up` sí lo hay; se mide ahí.

## Corrección hecha durante el milestone

- **Mezclaba tú y usted.** En la primera corrida el agente dijo "¿quieres… que ya tienes?" y en la misma
  respuesta "le sirve… que ya tiene". Los ejemplos de las reglas de proceso están escritos en tú y arrastraban
  al modelo. La línea de ESTILO ahora dice: "Trata al cliente de usted en TODA la respuesta (usted, le,
  su, quiere, tiene), nunca de tú, aunque los ejemplos de abajo estén en tú." Con eso, dos corridas en verde.
  Como es un cambio de prompt, se corrieron de nuevo los evals: `make evals` 2/2 y evals de catálogo 9/9
  (3 plantillas y 6 rubros), con `gpt-5.4-mini`.

## Decisiones

### Bloqueantes

Ninguna.

### Para ratificar (Giannier)

1. **017 C-14, política de descuentos:** el agente nunca concede descuentos por su cuenta; solo informa
   promociones vigentes. Si el negocio pone "puede negociar hasta X %", hoy eso solo cambia el texto de
   la política, no habilita al agente a bajar precios.
2. **Contenido de los rubros v1** (`backend/alembic/data/rubros_v1.json`): lo redacté yo a partir de la
   spec. Conviene que lo lea alguien que conozca cada tipo de negocio antes del piloto.
3. **017 C-15 y C-16:** umbrales de similitud (situaciones 0.55, conocimiento del rubro) y topes de cada
   sección del prompt. Afectan el costo por mensaje. Hoy es de unos 2 600 tokens de entrada por turno.

### Informativas

- Con una empresa recién creada, el agente intenta `enviar_recurso("saludo")` aunque el recurso no exista.
  La tool responde `no_disponible` y el turno sigue bien, pero gasta una vuelta. Pasa a la Fase D
  (regla condicionada a que el recurso exista).
- Sin conocimiento cargado, ante "¿para qué me sirve el certificado?" el agente ofrece derivar en vez de
  inventar. Es lo correcto; con el conocimiento del negocio cargado responde con él.
- Suite completa: 3298 pruebas en verde. En una corrida anterior falló una vez
  `TestVectorSearchIsScoped` (búsqueda por similitud del aislamiento). No volvió a fallar en tres corridas
  (dos de la suite de aislamiento y una completa). Queda anotado como intermitente; si reaparece, revisar
  el índice HNSW con filtro por tenant (recall, no fuga: la prueba exige que aparezca el chunk propio).
- El clic en Android del panel de operación (`/admin`) ya tenía un problema de diseño en teléfono antes
  de esta fase; sigue anotado.

## Cómo repetirlo

```
RUN_MILESTONE_C=1 LLM_PROFILE=openai OPENAI_API_KEY=... \
  uv run python -m pytest backend/tests/integration/test_zz_milestone_c_live.py -s
```

El reporte se escribe en `$MILESTONE_C_REPORT` (por defecto, el directorio temporal). La base se revierte al
terminar: no deja datos.
