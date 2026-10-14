# Spec + Plan — Conocimiento Avanzado (RAG v2)

**Feature ID:** 006 · **Módulo:** 06-knowledge · **Estado:** En construcción (2026-10-14). Decisiones en [clarifications.md](clarifications.md)
**Depende de:** 001 (ingesta base), 004 (visión disponible)
**Tamaño:** feature corta — spec, plan y tasks en un solo documento.

## Resumen
Cierra las brechas de la ingesta v0: PDFs escaneados (OCR vía visión), ingesta desde URL
del sitio web del negocio, re-indexación al editar, y calidad de recuperación medible.
La investigación de mercado señaló "entrenar al agente desde tu web y documentos" (Cliengo,
Zaia, Darwin) como práctica estándar que Leo debe igualar.

## User Stories y Requerimientos
- **HU-01 OCR de escaneados:** WHEN un PDF sin capa de texto entra a ingesta, THEN se rasteriza por página y se extrae texto con el modelo de visión (task `vision` de model_policies); páginas ilegibles se reportan. Límite trial: 30 páginas OCR/documento.
- **HU-02 Ingesta desde URL:** WHEN el dueño ingresa la URL de su web, THEN se rastrean hasta 20 páginas del mismo dominio (respetando robots.txt), se limpia el HTML y se indexa como source tipo `web` con re-sincronización manual (botón "actualizar desde mi web").
- **HU-03 Re-indexación:** WHEN se edita una FAQ/política/texto, THEN sus chunks se regeneran (borrar+crear, transaccional); el agente nunca lee estado a medias.
- **HU-04 Calidad de recuperación:** conjunto de preguntas doradas por tenant demo con verificación de que el chunk correcto aparece en top-k (métrica recall@5 en CI).
- **HU-05 Citas internas:** la tool `consultar_conocimiento` devuelve las fuentes usadas; la traza las muestra en bandeja/sandbox ("respondió usando: Política de envíos").
- [RF-001] Nuevo type `web` en knowledge_sources (+ `source_url`, `crawled_at`).
- [RF-002] OCR contabiliza contra el límite de caracteres del plan como cualquier fuente.
- [RF-003] Deduplicación de chunks casi idénticos (hash de contenido normalizado).
- [RF-004] Chunking mejorado: por estructura (títulos/párrafos) con overlap, tamaño objetivo 300–500 tokens.

## Decisiones y Edge Cases
- **C-001 Webs con JS pesado:** v1 solo HTML estático (httpx + parser); si el contenido queda vacío, mensaje claro "no pudimos leer tu web, sube el contenido como texto/PDF". Rendering con navegador queda post-MVP.
- **C-002 Frecuencia de crawl:** solo manual (botón) en v1 — evita costos silenciosos; programado llega con billing maduro.
- **C-003 Contenido conflictivo (web dice A, FAQ dice B):** prioridad configurable por source (default: FAQ/política > web); la traza evidencia la fuente usada.
- **C-004 Idioma de embeddings:** modelo multilingüe desde F001; verificar recall en español con el dataset dorado antes de cerrar la feature.

## Plan técnico (delta)
- `KnowledgeIngestService` gana estrategias por tipo (pdf_text, pdf_ocr, web, text/faq/policy) — patrón Strategy sobre el worker existente.
- Migración: `ALTER TABLE knowledge_sources ADD COLUMN source_url TEXT, ADD COLUMN crawled_at TIMESTAMPTZ, ADD COLUMN priority INT NOT NULL DEFAULT 100;` + `ALTER TABLE knowledge_chunks ADD COLUMN content_hash TEXT;` (unique parcial por tenant+hash).
- API: `POST /tenant/knowledge/web` `{url}` → 202; `POST /tenant/knowledge/:id/resync`; `GET /tenant/knowledge/:id/chunks` (debug, owner).
- Testing: fixtures de PDFs escaneados reales; crawler contra sitio de prueba local; recall@5 ≥0.8 en dataset dorado; transaccionalidad de re-indexación.
- Riesgos: costo OCR (límite de páginas + modelo económico), webs bloqueando crawler (user-agent honesto + mensaje claro al fallar).

## Tasks
Commits: `feat(knowledge): ... [TASK-6XX]`
- [x] **TASK-601: Migraciones + chunking v2 + dedup** (re-chunk de sources existentes vía comando de mantenimiento).
- [ ] **TASK-602: OCR de PDFs escaneados** (rasterizado + visión + límites por plan). Depends: 601, F004 visión.
- [ ] **TASK-603: Ingesta web** (crawler acotado + limpieza + resync manual + UI). Depends: 601.
- [ ] **TASK-604: Prioridad de fuentes + citas en traza + recall@5 en CI + UI de fuentes.** Depends: 601–603. Milestone humano: probar con la web real de un cliente de Zentek360.

Estimación: 1 semana. Complejidad: Media.
