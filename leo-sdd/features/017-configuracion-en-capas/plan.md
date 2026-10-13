# Plan — Configuración en capas y aprendizaje (017)

**Spec:** `spec.md` · **Decisiones:** `clarifications.md` · **Mapeo con el repo:** `mapeo.md`

## Resumen técnico

La Fase 1 se construye sobre el motor de la 004 y **es** su Fase C: no hay un pipeline nuevo, cambia de
dónde salen las instrucciones. Hoy `ProcessConfigService.effective()` arma el texto de cada etapa desde la
plantilla en código (o el texto que el negocio editó) y le suma `restrictions`. Después de la Fase 1 lo
arma así:

```
reglas de la plantilla de proceso (BD, versión publicada) − las que el negocio apagó
reglas del rubro                    (BD, versión publicada) − las que el negocio apagó
perfil del negocio                  (formularios)
situaciones elegidas para este mensaje (selector, C-10)
```

y el prompt las presenta en ese orden con la regla de prioridad (C-03, C-11). Las Fases 2 a 4 agregan
fuentes de **sugerencias** que terminan siendo situaciones, ejemplos o datos de conocimiento, siempre
después de la aprobación de un revisor.

## Constitution check
- **Aislamiento:** toda tabla por negocio lleva `tenant_id` y repositorio con filtro; cada una tiene su
  test en `tests/isolation`. Las tablas de catálogo (plantilla y rubro) son de plataforma, de solo lectura
  para los negocios, y solo las escribe el operador de plataforma (auditoría de plataforma).
- **Adaptadores:** embeddings por el `LLMProvider` existente; la transcripción (Fase 2) es un puerto nuevo
  en `providers/llm/` como el de visión, por `httpx`, **sin dependencia nueva**.
- **Esquema:** solo migraciones Alembic (desde 0028).
- **Prompt:** cambia → evals al cerrar la Fase 1 y en cada publicación de catálogo.
- **Datos personales:** Fase 2 bajo C-06 (se ratifica al arrancar).
- **Cobro:** nada aquí depende de plan mientras `BILLING_ENFORCED` esté apagado.

## Fase 1 · Capas y situaciones manuales

### Modelo de datos

**Catálogo de plataforma (versionado)**

```
catalog_versions        id, kind ('proceso' | 'rubro'), key ('productos' | 'instituto' | ...),
                        version int, estado ('borrador' | 'publicada' | 'retirada'),
                        evals jsonb (resultado del run), publicada_en, publicada_por, created_at
                        UNIQUE (kind, key, version)
catalog_rules           id, version_id → catalog_versions, rule_key text (estable entre versiones),
                        etapa text NULL (solo reglas de proceso), texto, orden
                        UNIQUE (version_id, rule_key)
catalog_faqs            id, version_id, pregunta, respuesta, orden               (solo rubro)
catalog_objections      id, version_id, objecion, manejo, orden                  (solo rubro)
catalog_suggested_situations
                        id, version_id, titulo, cuando_aplica, que_hace, campos jsonb (lo que el
                        negocio completa, p. ej. "monto mínimo"), orden          (solo rubro)
rubros                  key (industry), nombre, plantilla_sugerida ('productos' | ...)
```

- El primer contenido sale del código: los textos de etapa de `process/templates/*` se parten en reglas con
  `rule_key` (`COBRO.no_inventar_cuentas`, ...) en una migración de datos; las tres plantillas quedan como
  `proceso v1 publicada`. Los seis rubros arrancan con una versión `v1` corta escrita por Claude y revisada
  por Giannier (reglas, 5 FAQ, 5 objeciones, 3 a 5 situaciones sugeridas).
- Una versión `publicada` no se modifica: un cambio es una versión nueva.

**Por negocio**

```
tenant_rule_switches    tenant_id, kind, rule_key, apagada bool, cambiado_por, updated_at
                        PK (tenant_id, kind, rule_key)
tenant_catalog_seen     tenant_id, kind, key, version_vista            (aviso de novedades)
situations              id, tenant_id, titulo, cuando_aplica, que_hace,
                        media_asset_ids uuid[], derivar bool, ejemplo_respuesta text NULL,
                        etapas text[] NULL (NULL = todas), siempre bool,
                        estado ('borrador' | 'activa' | 'inactiva'),
                        origen ('manual' | 'sugerida_rubro' | 'migrada' | 'aprendida'),
                        embedding vector(1024), created_by, timestamps
agent_configs           + tratamiento ('tu' | 'usted'), emojis ('no' | 'pocos' | 'si'),
                          saludo text ≤ 120, politica_descuentos ('no_ofrece' | 'solo_promociones' |
                          'negociable'), descuento_maximo_pct numeric NULL, max_situaciones int = 5
                        − restrictions (se migra a una situación y la columna se elimina)
payment_methods_business + titular text NULL                   (D-1 de la 004: destinatario)
tenant_users            + puede_revisar bool (owner: true)
business_profiles       + rubro → rubros.key (hoy `industry`; se reutiliza la columna)
process_stage_overrides  se elimina (C-01)
```

### Resolución de capas (`process/layers.py`)
`LayerResolver(session, tenant_id).resolve(stage)` → `EffectiveRules`:
1. versión publicada de la plantilla de proceso del negocio y de su rubro (caché de 60 s junto a
   `ProcessConfigCache`, invalidada al publicar o al cambiar un interruptor);
2. menos los `rule_key` apagados del negocio;
3. reglas de proceso de la etapa actual + todas las del rubro + FAQ y objeciones del rubro.

Las FAQ y objeciones del rubro entran como **conocimiento** (se indexan como `knowledge_source` de tipo
`rubro`, de plataforma, compartidas por los negocios del rubro y filtradas por la consulta), no en el
prompt fijo, para no gastar presupuesto de tokens.

### Selector de situaciones (`agent/situations.py`, C-10)
- Al guardar o cambiar el "cuándo aplica" se calcula su embedding (worker; el panel no espera).
- En el paso 8 del pipeline: una llamada de embedding con el texto del cliente del turno; consulta
  pgvector sobre `situations` del negocio con `estado = activa` (más las `borrador` pedidas por el
  simulador), filtro por etapa (`etapas IS NULL OR stage = ANY(etapas)`), umbral de similitud y
  `LIMIT max_situaciones − #siempre`; se suman las `siempre`.
- Si el embedding falla, entran solo las `siempre` (el turno no se cae).
- La traza guarda los ids elegidos y su similitud (`agent_traces.situaciones`).

### Prompt (`agent/prompts/stage.py`)
Secciones nuevas en el orden de autoridad, de menor a mayor:
`PROCESO` (reglas de la etapa) → `RUBRO` (reglas) → `NEGOCIO` (perfil, ya existe) → `SITUACIONES DEL
NEGOCIO` (las elegidas, con su "qué hace", archivos por clave y ejemplo) y una línea fija: "Si algo choca,
manda la situación, luego el perfil del negocio, luego el rubro, luego el proceso". `ESTILO` deja de
llevar "reglas adicionales del dueño" y suma tratamiento, emojis y saludo. Topes nuevos en `SECTION_CAPS`
y su test de presupuesto con 5 situaciones largas. Snapshots regenerados.

### Simulador
`POST .../sandbox/messages` acepta `situaciones_prueba: [id]` (borradores del negocio) que entran como
activas solo en ese turno; el panel de depuración muestra reglas apagadas, situaciones elegidas y su
puntaje.

### API (cambios a `contracts/api.md` de la 004)
- `GET /assistant/process` devuelve reglas por etapa con `apagada`; **se retiran** `PUT` y `DELETE
  /assistant/process/stages/{stage_key}`.
- `PUT /assistant/rules/{kind}/{rule_key}` `{apagada}` (admin o revisor).
- `GET /assistant/rubro` (reglas, FAQ, objeciones, situaciones sugeridas y novedades) y
  `POST /assistant/rubro/novedades/vistas`.
- `GET | POST /assistant/situations`, `GET | PUT | DELETE /assistant/situations/{id}`,
  `POST /assistant/situations/{id}/activar|desactivar`,
  `POST /assistant/situations/desde-sugerida/{id}` (copia una sugerida del rubro con los campos completos).
- `PUT /tenant/team/{membership_id}` acepta `puede_revisar`.
- Plataforma: `GET | POST /admin/catalog/{kind}/{key}/versions`, `PUT …/versions/{v}` (solo borrador),
  `POST …/versions/{v}/evals` (encola el run), `POST …/versions/{v}/publicar` (409 si los evals no
  pasaron).

### Evals de catálogo
`backend/evals/catalog/`: casos por plantilla y por rubro (una regla de proceso se cumple; una regla
apagada deja de aplicarse; una situación activa gana sobre el rubro; una situación de otra etapa no entra).
El run de publicación los corre contra la versión en borrador con el modelo `economico`. Sin
`OPENAI_API_KEY` el run falla con el motivo, nunca "pasa".

### Frontend
- **Proceso de venta:** etapas en línea de tiempo con sus reglas e interruptores; se retira el editor de
  texto. Aviso de novedades arriba.
- **Situaciones:** lista de tarjetas (filtros por estado y origen), formulario de tarjeta, "Probar en el
  simulador" y activar.
- **Wizard (001):** paso 2 cambia "restricciones" por tratamiento, emojis y saludo; el rubro sugiere la
  plantilla; paso final con 3 a 5 situaciones sugeridas.
- **Equipo:** casilla "Revisor".
- **Plataforma:** catálogo con versiones, editor de borrador, evals y publicar.

## Fases 2 a 4 (después del piloto)

Se detallan al arrancar, cuando se ratifiquen C-06 a C-08. Esqueleto:

- **Fase 2 · Importación y estilo.** `chat_imports` (estado, autorización, quién, cuándo), puerto
  `TranscriptionProvider` (OpenAI audio por `httpx`), anonimizador (reglas + modelo para nombres y
  direcciones), borrado del original en `finally`, y extractores de estilo, ejemplos y objeciones que
  escriben **sugerencias**. Ejemplos en `example_conversations` (anonimizados, con embedding) para
  recuperarlos como las situaciones.
- **Fase 3 · Ciclo de aprendizaje.** `suggestions` (tenant_id, fuente, tipo: situación | ejemplo | dato,
  propuesta jsonb, conversaciones origen, choques, estado, huella para no repetir rechazadas),
  `config_history` (snapshot antes y después, deshacer). Fuentes: evento de toma de control de la 007,
  `orders` confirmados, job semanal de ventas perdidas, `consultar_conocimiento` sin resultados. Bandeja
  del revisor con aprobación en lote para ejemplos.
- **Fase 4 · Rubro y redactor.** Agrupación de situaciones parecidas por rubro (embeddings, solo texto de
  la regla) en el panel de plataforma → borrador de versión de rubro → evals → publicar. Redactor: una
  llamada al modelo que devuelve la tarjeta y los choques detectados contra situaciones, perfil y reglas.

## Riesgos
| Riesgo | Mitigación |
|---|---|
| Partir los textos de etapa en reglas cambia la conducta del agente | Snapshots del prompt y evals al cerrar la Fase 1 (C-01) |
| El selector deja fuera una situación importante | Marca "siempre"; depuración con puntajes; umbral ajustable por settings |
| Una versión de rubro mala llega a todos los negocios | Evals obligatorios antes de publicar; las versiones no se pisan y se puede volver a la anterior |
| Prompt más largo | Topes por sección y test de presupuesto; FAQ y objeciones del rubro por recuperación, no fijas |
| Datos de clientes en reglas que suben al rubro (Fase 4) | Solo texto de la regla, revisado a mano, anonimizado y auditado |

## Milestones
- **Milestone C (TASK-442 de la 004, ajustado):** configurar una tienda desde cero en 30 minutos con
  capas y situaciones, apagar una regla, probar una situación en borrador, publicar una versión de rubro
  con evals y ver la novedad.
- Fases 2 a 4: un milestone por fase con las empresas del holding (definidos al arrancar cada una).

## Estimación (Fase 1)
Unas 12 tasks, la mitad en paralelo: catálogo y migración de datos, resolución de capas, situaciones y
selector, prompt, API, simulador, frontend (proceso, situaciones, wizard, equipo, plataforma), evals.
