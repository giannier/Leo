"use client";

/**
 * One knowledge source in the library (Feature 006, TASK-604).
 *
 * Says what the assistant does with it: whether it is ready, how much the
 * business trusts it against the others (priority), what could not be read
 * (scanned pages, website pages), and lets whoever configures edit the text,
 * read it again, look at the passages the assistant retrieves, or remove it.
 */

import { useState } from "react";

import { AlertIcon, CheckIcon, SpinnerIcon } from "@/components/icons";
import { Alert, Button, Field, Select, TextArea, cx } from "@/components/ui";
import { api, type KnowledgeSource } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

export const IN_FLIGHT = new Set(["pending", "indexing"]);

const TYPE_LABELS: Record<string, string> = {
  faq: "Preguntas frecuentes",
  policy: "Política",
  text: "Texto",
  pdf: "PDF",
  web: "Página web",
};

export const PRIORITIES = [
  { value: "100", label: "Alta: lo que yo escribí" },
  { value: "90", label: "Media: mis documentos" },
  { value: "50", label: "Baja: mi web" },
];

function priorityOptions(current: number) {
  return PRIORITIES.some((p) => p.value === String(current))
    ? PRIORITIES
    : [{ value: String(current), label: `Personalizada (${current})` }, ...PRIORITIES];
}

function pages(numbers: unknown): string {
  return Array.isArray(numbers) && numbers.length ? numbers.join(", ") : "";
}

/** What ingestion could not do, in a sentence. */
export function reportLines(source: KnowledgeSource): string[] {
  const report = (source.report ?? {}) as Record<string, Record<string, unknown>>;
  const lines: string[] = [];
  const ocr = report.ocr;
  if (ocr) {
    const read = Array.isArray(ocr.leidas) ? ocr.leidas.length : 0;
    if (read) lines.push(`Leímos ${read} página${read === 1 ? "" : "s"} escaneada${read === 1 ? "" : "s"}.`);
    if (pages(ocr.ilegibles)) lines.push(`No pudimos leer las páginas ${pages(ocr.ilegibles)}.`);
    if (pages(ocr.sin_leer_por_limite))
      lines.push(`Páginas ${pages(ocr.sin_leer_por_limite)} sin leer: el límite es 30 páginas escaneadas por documento.`);
    if (pages(ocr.sin_leer)) lines.push(`Páginas escaneadas sin leer: ${pages(ocr.sin_leer)}.`);
  }
  const web = report.web;
  if (web) {
    const read = Array.isArray(web.paginas) ? web.paginas.length : 0;
    lines.push(`Leímos ${read} página${read === 1 ? "" : "s"} de tu web.`);
    const skipped = Array.isArray(web.omitidas) ? web.omitidas.length : 0;
    if (skipped) lines.push(`${skipped} se omitieron (no eran páginas, otro sitio o robots.txt).`);
  }
  return lines;
}

export function SourceCard({
  source,
  canEdit,
  onChanged,
}: {
  source: KnowledgeSource;
  canEdit: boolean;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(source.title);
  const [content, setContent] = useState<string | null>(null);
  const [chunks, setChunks] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const editableText = ["faq", "policy", "text"].includes(source.type);
  const flight = IN_FLIGHT.has(source.status);

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    try {
      await run();
      await onChanged();
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function startEditing() {
    setEditing(true);
    setTitle(source.title);
    if (editableText) {
      try {
        setContent((await api.knowledge.get(source.id)).content ?? "");
      } catch (caught) {
        setFailure(humanMessage(caught));
      }
    }
  }

  return (
    <li className="space-y-2 p-4" data-testid="knowledge-source">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink">{source.title}</p>
          <p className="text-xs text-ink-faint">
            {TYPE_LABELS[source.type] ?? source.type}
            {source.char_count > 0 ? ` · ${source.char_count.toLocaleString("es-PE")} caracteres` : ""}
            {source.source_url ? ` · ${source.source_url}` : ""}
            {source.crawled_at
              ? ` · leída el ${new Date(source.crawled_at).toLocaleDateString("es-PE", { day: "numeric", month: "short" })}`
              : ""}
          </p>
        </div>
        <span
          className={cx(
            "flex shrink-0 items-center gap-1 text-xs font-medium",
            source.status === "indexed" && "text-success",
            source.status === "failed" && "text-danger",
            flight && "text-info",
          )}
          data-testid="knowledge-status"
        >
          {flight ? <SpinnerIcon className="size-3" /> : source.status === "indexed" ? <CheckIcon className="size-3" /> : <AlertIcon className="size-3" />}
          {source.status === "pending" ? "En cola" : source.status === "indexing" ? "Procesando" : source.status === "indexed" ? "Listo" : "Falló"}
        </span>
      </div>

      {source.status === "failed" && source.error_msg ? (
        <p className="text-xs text-danger">{source.error_msg}</p>
      ) : null}
      {reportLines(source).map((line) => (
        <p key={line} className="text-xs text-ink-soft" data-testid="knowledge-report">
          {line}
        </p>
      ))}
      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      {canEdit && !editing ? (
        <div className="flex flex-wrap items-center gap-2">
          <div className="w-full sm:w-64">
            <Select
              label="Prioridad"
              hint="Si dos fuentes se contradicen, el asistente cree en la de mayor prioridad."
              options={priorityOptions(source.priority ?? 100)}
              value={String(source.priority ?? 100)}
              onChange={(event) =>
                void act(() => api.knowledge.update(source.id, { priority: Number(event.target.value) }))
              }
            />
          </div>
          <div className="flex flex-wrap gap-2 self-end">
            <Button type="button" variant="ghost" disabled={busy || flight} onClick={() => void startEditing()}>
              Editar
            </Button>
            {source.type === "web" ? (
              <Button type="button" variant="secondary" loading={busy} disabled={flight} onClick={() => void act(() => api.knowledge.resync(source.id))}>
                Actualizar desde mi web
              </Button>
            ) : source.status === "failed" ? (
              <Button type="button" variant="secondary" loading={busy} onClick={() => void act(() => api.knowledge.resync(source.id))}>
                Reintentar
              </Button>
            ) : null}
            <Button
              type="button"
              variant="ghost"
              disabled={source.status !== "indexed"}
              onClick={async () => {
                if (chunks) return setChunks(null);
                try {
                  setChunks((await api.knowledge.chunks(source.id)).chunks);
                } catch (caught) {
                  setFailure(humanMessage(caught));
                }
              }}
            >
              {chunks ? "Ocultar fragmentos" : "Ver qué lee el asistente"}
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={busy}
              onClick={() => {
                if (window.confirm(`El asistente dejará de saber lo que dice "${source.title}". ¿Eliminar?`)) {
                  void act(() => api.knowledge.remove(source.id));
                }
              }}
            >
              Eliminar
            </Button>
          </div>
        </div>
      ) : null}

      {editing ? (
        <form
          className="space-y-3 rounded-lg border border-line p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void act(async () => {
              await api.knowledge.update(source.id, {
                title: title.trim(),
                ...(editableText && content !== null ? { content } : {}),
              });
              setEditing(false);
            });
          }}
        >
          <Field label="Título" value={title} onChange={(event) => setTitle(event.target.value)} required />
          {editableText ? (
            content === null ? (
              <p className="text-xs text-ink-faint">Cargando el texto...</p>
            ) : (
              <TextArea
                label="Contenido"
                rows={6}
                value={content}
                onChange={(event) => setContent(event.target.value)}
                hint="El asistente sigue usando el texto anterior hasta que el nuevo esté listo."
              />
            )
          ) : (
            <p className="text-xs text-ink-faint">
              {source.type === "web"
                ? "El contenido de una web se cambia en la web y se vuelve a leer con \"Actualizar desde mi web\"."
                : "Para cambiar el contenido de un PDF, sube la versión nueva y elimina esta."}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" loading={busy} disabled={!title.trim()}>
              Guardar
            </Button>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
          </div>
        </form>
      ) : null}

      {chunks ? (
        <ol className="max-h-80 space-y-2 overflow-y-auto rounded-lg bg-surface-muted p-3 text-xs text-ink-soft" data-testid="knowledge-chunks">
          {chunks.map((chunk, index) => (
            <li key={index} className="whitespace-pre-wrap border-b border-line pb-2 last:border-0">
              {chunk}
            </li>
          ))}
        </ol>
      ) : null}
    </li>
  );
}
