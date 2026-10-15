"use client";

/**
 * Step 4: what the agent should know beyond the price list.
 *
 * Optional, and the step that makes the difference between an agent that
 * quotes prices and one that answers "¿hacen delivery a San Isidro?".
 *
 * Indexing is asynchronous - the API answers 202 and a worker does the
 * embedding - so a source appears as `pending`, becomes `indexing`, and ends
 * `indexed` or `failed`. This polls while anything is still in flight and
 * stops the moment nothing is, because a timer that runs forever on an idle
 * tab is a battery complaint from somebody on a phone.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { AlertIcon, BookIcon, CheckIcon, CloseIcon, SpinnerIcon } from "@/components/icons";
import { Alert, Button, Field, Select, Skeleton, TextArea, cx } from "@/components/ui";
import { api, type KnowledgeList, type KnowledgeSource } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { StepIntro, type StepProps } from "./shared";

const POLL_MS = 2500;
const IN_FLIGHT = new Set(["pending", "indexing"]);

const TYPES = [
  { value: "faq", label: "Pregunta frecuente" },
  { value: "policy", label: "Política (envíos, cambios, garantía)" },
  { value: "text", label: "Texto libre" },
] as const;

function statusLabel(source: KnowledgeSource): { text: string; tone: string; spinning: boolean } {
  switch (source.status) {
    case "pending":
      return { text: "En cola", tone: "text-ink-faint", spinning: true };
    case "indexing":
      return { text: "Procesando", tone: "text-info", spinning: true };
    case "indexed":
      return { text: "Listo", tone: "text-success", spinning: false };
    default:
      return { text: "Falló", tone: "text-danger", spinning: false };
  }
}

export function StepKnowledge({ onSaved }: StepProps) {
  const [data, setData] = useState<KnowledgeList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.knowledge.list();
      setData(next);
      setError(null);
      return next;
    } catch (caught) {
      setError(humanMessage(caught));
      return null;
    }
  }, []);

  // Poll only while something is actually being indexed.
  useEffect(() => {
    let cancelled = false;

    async function tick() {
      const next = await load();
      if (cancelled) return;
      const busy = next?.sources.some((source) => IN_FLIGHT.has(source.status)) ?? false;
      if (busy) {
        timer.current = setTimeout(() => void tick(), POLL_MS);
      }
    }

    void tick();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  const afterChange = useCallback(async () => {
    await load();
    onSaved();
  }, [load, onSaved]);

  return (
    <div className="space-y-5">
      <StepIntro>
        Opcional, pero es lo que separa un agente que da precios de uno que resuelve dudas.
        Sube tus políticas, tus preguntas frecuentes o cualquier PDF que tengas.
      </StepIntro>

      {error ? <Alert tone="danger">{error}</Alert> : null}

      {data ? <Loaded sources={data.sources} /> : null}
      {data ? <Usage usage={data.usage} /> : null}
      <SourceList sources={data?.sources ?? null} onChanged={afterChange} />
      <AddText onAdded={afterChange} />
      <AddFile onAdded={afterChange} />
    </div>
  );
}

/**
 * How much of this step is already done.
 *
 * This step never gets a tick in the step bar - knowledge is optional, so it
 * is not part of the activation checklist and adding it there would turn an
 * optional step into a blocking one. But somebody who uploaded three PDFs and
 * comes back tomorrow needs to see that they arrived, and "nothing here says
 * so" reads as "it was lost".
 *
 * Counted from `indexed`, not from the file count: indexing is asynchronous,
 * and a source still being processed is not yet something the agent can
 * answer from. Saying it is ready before it is would be the same lie with
 * the sign flipped.
 */
function Loaded({ sources }: { sources: KnowledgeSource[] }) {
  const ready = sources.filter((source) => source.status === "indexed").length;
  const busy = sources.filter((source) => IN_FLIGHT.has(source.status)).length;

  // Nothing usable and nothing on the way: every source failed, and the list
  // below says why. A count of zero here would only add noise.
  if (ready === 0 && busy === 0) return null;

  return (
    <p className="flex items-center gap-2 text-sm text-ink-soft" data-testid="knowledge-count">
      {ready > 0 ? (
        <CheckIcon className="size-4 shrink-0 text-success" />
      ) : (
        <SpinnerIcon className="size-4 shrink-0 text-info" />
      )}
      {ready > 0 ? (
        <span>
          Leo ya usa{" "}
          <span className="font-semibold text-ink">
            {ready} {ready === 1 ? "fuente" : "fuentes"}
          </span>{" "}
          de conocimiento{busy > 0 ? ` · ${busy} en proceso` : ""}.
        </span>
      ) : (
        <span>
          Procesando {busy} {busy === 1 ? "fuente" : "fuentes"}...
        </span>
      )}
    </p>
  );
}

function Usage({ usage }: { usage: KnowledgeList["usage"] }) {
  const chars = usage.chars_limit > 0 ? usage.chars_used / usage.chars_limit : 0;
  const near = chars >= 0.8;
  return (
    <div className="rounded-lg bg-surface-muted p-3 text-xs text-ink-soft">
      <div className="flex flex-wrap justify-between gap-2">
        <span className="tabular">
          {usage.chars_used.toLocaleString("es-PE")} de{" "}
          {usage.chars_limit.toLocaleString("es-PE")} caracteres
        </span>
        <span className="tabular">
          {usage.files_used} de {usage.files_limit} archivos
        </span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-line">
        <div
          className={cx("h-full rounded-full", near ? "bg-warning" : "bg-accent")}
          style={{ width: `${Math.min(chars * 100, 100)}%` }}
        />
      </div>
    </div>
  );
}

function SourceList({
  sources,
  onChanged,
}: {
  sources: KnowledgeSource[] | null;
  onChanged: () => Promise<void>;
}) {
  const [removing, setRemoving] = useState<string | null>(null);

  if (sources === null) return <Skeleton className="h-16 w-full" />;

  if (sources.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-line-strong p-6 text-center">
        <BookIcon className="mx-auto size-8 text-line-strong" />
        <p className="mt-2 text-sm font-medium text-ink">Sin conocimiento cargado</p>
        <p className="mt-1 text-xs text-ink-faint">Puedes saltarte este paso y volver después.</p>
      </div>
    );
  }

  async function remove(id: string) {
    setRemoving(id);
    try {
      await api.knowledge.remove(id);
      await onChanged();
    } finally {
      setRemoving(null);
    }
  }

  return (
    <ul className="divide-y divide-line rounded-lg border border-line">
      {sources.map((source) => {
        const status = statusLabel(source);
        return (
          <li key={source.id} className="flex items-start gap-3 p-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink">{source.title}</p>
              <p className={cx("mt-0.5 flex items-center gap-1.5 text-xs", status.tone)}>
                {status.spinning ? (
                  <SpinnerIcon className="size-3" />
                ) : source.status === "indexed" ? (
                  <CheckIcon className="size-3" />
                ) : (
                  <AlertIcon className="size-3" />
                )}
                {status.text}
                {source.status === "indexed" && source.char_count > 0 ? (
                  <span className="tabular text-ink-faint">
                    · {source.char_count.toLocaleString("es-PE")} caracteres
                  </span>
                ) : null}
              </p>
              {/* The reason, not just "failed". A scanned PDF with no text
               * layer is the common case and it is fixable - but only if we
               * say so. */}
              {source.status === "failed" && source.error_msg ? (
                <p className="mt-1 text-xs text-danger">{source.error_msg}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => void remove(source.id)}
              disabled={removing === source.id}
              className="flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-lg text-ink-faint hover:bg-surface-muted hover:text-danger disabled:opacity-50"
              aria-label={`Quitar ${source.title}`}
            >
              <CloseIcon className="size-4" />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function AddText({ onAdded }: { onAdded: () => Promise<void> }) {
  const [type, setType] = useState<"faq" | "policy" | "text">("faq");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim() || !content.trim()) return;

    setBusy(true);
    setFailure(null);
    try {
      await api.knowledge.addText({ type, title: title.trim(), content: content.trim() });
      setTitle("");
      setContent("");
      await onAdded();
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-lg border border-line p-4">
      <p className="text-sm font-medium text-ink">Escribir algo</p>
      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      <Select
        label="Tipo"
        options={TYPES}
        value={type}
        onChange={(e) => setType(e.target.value as typeof type)}
      />
      <Field
        label="Título"
        name="knowledge_title"
        required
        placeholder="¿Hacen delivery?"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <TextArea
        label="Contenido"
        name="knowledge_content"
        rows={4}
        required
        placeholder="Sí, hacemos delivery en Miraflores, San Isidro y Barranco. El costo es S/ 8 y demora entre 30 y 45 minutos."
        value={content}
        onChange={(e) => setContent(e.target.value)}
      />
      <Button type="submit" loading={busy} disabled={!title.trim() || !content.trim()}>
        Agregar
      </Button>
    </form>
  );
}

function AddFile({ onAdded }: { onAdded: () => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function pick(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    setBusy(true);
    setFailure(null);
    try {
      // The file name doubles as the title. Asking for one separately is a
      // field everybody fills with the file name anyway.
      await api.knowledge.addFile(file, file.name.replace(/\.pdf$/i, ""));
      await onAdded();
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-line p-4">
      <div>
        <p className="text-sm font-medium text-ink">Subir un PDF</p>
        <p className="mt-0.5 text-xs text-ink-faint">
          Catálogos, manuales, políticas. Hasta 10 MB. Si es un papel escaneado, lo leemos página
          por página (hasta 30 páginas).
        </p>
      </div>

      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      <input
        ref={input}
        type="file"
        accept=".pdf,application/pdf"
        onChange={pick}
        className="sr-only"
        id="knowledge-file"
      />
      <label
        htmlFor="knowledge-file"
        className={cx(
          "inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-line-strong px-4 text-sm font-semibold text-ink hover:bg-surface-muted",
          busy && "pointer-events-none opacity-50",
        )}
      >
        {busy ? <SpinnerIcon className="size-4" /> : null}
        {busy ? "Subiendo..." : "Elegir PDF"}
      </label>
    </div>
  );
}
