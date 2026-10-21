"use client";

/**
 * Conocimiento (Feature 006, TASK-603 and TASK-604).
 *
 * Everything the assistant knows beyond the catalogue: what the business wrote
 * (questions, policies, free text), its documents (PDF, scanned ones included)
 * and its website. Every member can read it, an operator answering customers
 * needs to know what the assistant answers from; adding, editing and removing is
 * configuration (owner and admin), like the rest of the assistant.
 *
 * Indexing happens in a worker, so a source goes pending, processing, then ready
 * or failed; the list polls while anything is in flight and stops when nothing is.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { IN_FLIGHT, SourceCard } from "@/components/knowledge/source-card";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Field, Select, Skeleton, TextArea } from "@/components/ui";
import { api, type KnowledgeList } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

const POLL_MS = 2500;
const TEXT_TYPES = [
  { value: "faq", label: "Pregunta frecuente" },
  { value: "policy", label: "Política (envíos, cambios, garantía)" },
  { value: "text", label: "Texto libre" },
] as const;

export default function KnowledgePage() {
  const canEdit = useHasRole(...MANAGES_CONFIGURATION);
  const [data, setData] = useState<KnowledgeList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const listTop = useRef<HTMLDivElement>(null);
  const lastStatus = useRef<Map<string, string>>(new Map());
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const next = await api.knowledge.list();
      // Indexing happens in the background: say when a source that was being read is done,
      // since the person has usually scrolled away from the list by then.
      for (const source of next.sources) {
        const before = lastStatus.current.get(source.id);
        if (before && IN_FLIGHT.has(before) && !IN_FLIGHT.has(source.status)) {
          if (source.status === "indexed") toast.success("Listo", `${source.title} ya está leído`);
          else toast.error("No se pudo leer", `${source.title}: ${source.error_msg ?? "revisa la fuente"}`);
        }
        lastStatus.current.set(source.id, source.status);
      }
      setData(next);
      setError(null);
      return next;
    } catch (caught) {
      setError(humanMessage(caught));
      return null;
    }
  }, [toast]);

  const refresh = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    const next = await load();
    if (next?.sources.some((s) => IN_FLIGHT.has(s.status))) {
      timer.current = setTimeout(() => void refresh(), POLL_MS);
    }
  }, [load]);

  /** After adding something: tell the person, and bring the list (where it appears) into view. */
  const added = useCallback(
    async (message: string) => {
      toast.success("Recibido", message);
      await refresh();
      listTop.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [refresh, toast],
  );

  useEffect(() => {
    void refresh();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [refresh]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Conocimiento</h1>
        <p className="text-sm text-ink-soft">
          Lo que el asistente sabe de tu negocio además del catálogo. Cuando un cliente pregunta,
          busca aquí antes de responder.
        </p>
      </header>

      {error ? <Alert tone="danger">{error}</Alert> : null}

      {data ? (
        <p className="text-xs text-ink-faint" data-testid="knowledge-usage">
          {data.usage.chars_used.toLocaleString("es-PE")} de{" "}
          {data.usage.chars_limit.toLocaleString("es-PE")} caracteres · {data.usage.files_used} de{" "}
          {data.usage.files_limit} fuentes
        </p>
      ) : null}

      <div ref={listTop} className="scroll-mt-4" />
      {data === null ? (
        <Skeleton className="h-40 w-full" />
      ) : data.sources.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-ink-soft">
          Todavía no hay nada. Agrega tus preguntas frecuentes, un PDF o la dirección de tu web.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line bg-surface" data-testid="knowledge-list">
          {data.sources.map((source) => (
            <SourceCard key={source.id} source={source} canEdit={canEdit} onChanged={refresh} />
          ))}
        </ul>
      )}

      {canEdit ? (
        <div className="grid gap-4 lg:grid-cols-3">
          <AddText onAdded={() => added("Tu texto quedó guardado y se está indexando.")} />
          <AddPdf onAdded={() => added("Subimos tu PDF. Lo estamos leyendo; te avisamos cuando esté listo.")} />
          <AddWeb onAdded={() => added("Estamos leyendo tu web. Puede tardar un minuto; te avisamos cuando esté lista.")} />
        </div>
      ) : null}
    </div>
  );
}

function AddText({ onAdded }: { onAdded: () => Promise<void> }) {
  const [type, setType] = useState<"faq" | "policy" | "text">("faq");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  return (
    <Card>
      <form
        className="space-y-3"
        aria-label="Escribir"
        onSubmit={async (event) => {
          event.preventDefault();
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
        }}
      >
        <h2 className="font-semibold text-ink">Escribir</h2>
        {failure ? <Alert tone="danger">{failure}</Alert> : null}
        <Select label="Tipo" options={TEXT_TYPES} value={type} onChange={(e) => setType(e.target.value as typeof type)} />
        <Field label="Título" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="¿Hacen delivery?" />
        <TextArea
          label="Contenido"
          rows={4}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Sí, hacemos delivery en Miraflores y Barranco por S/ 8."
        />
        <Button type="submit" loading={busy} disabled={!title.trim() || !content.trim()}>
          Agregar
        </Button>
      </form>
    </Card>
  );
}

function AddPdf({ onAdded }: { onAdded: () => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  return (
    <Card>
      <div className="space-y-3">
        <h2 className="font-semibold text-ink">Subir un PDF</h2>
        <p className="text-xs text-ink-soft">
          Cartas, catálogos, manuales. Hasta 10 MB. Si es escaneado, lo leemos página por página
          (hasta 30 páginas) y te avisamos si alguna no se pudo leer.
        </p>
        {failure ? <Alert tone="danger">{failure}</Alert> : null}
        <input
          ref={input}
          type="file"
          accept=".pdf,application/pdf"
          aria-label="Elegir PDF"
          className="block w-full text-sm"
          disabled={busy}
          onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            setBusy(true);
            setFailure(null);
            try {
              await api.knowledge.addFile(file, file.name.replace(/\.pdf$/i, ""));
              await onAdded();
            } catch (caught) {
              setFailure(humanMessage(caught));
            } finally {
              setBusy(false);
              if (input.current) input.current.value = "";
            }
          }}
        />
        {busy ? <p className="text-xs text-ink-faint">Subiendo...</p> : null}
      </div>
    </Card>
  );
}

function AddWeb({ onAdded }: { onAdded: () => Promise<void> }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  // The web written once in the business profile: no need to type it again here.
  useEffect(() => {
    api.profile
      .get()
      .then((profile) => setUrl((current) => current || profile.website || ""))
      .catch(() => undefined);
  }, []);

  return (
    <Card>
      <form
        className="space-y-3"
        aria-label="Desde mi web"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setFailure(null);
          try {
            await api.knowledge.addWeb(url.trim());
            setUrl("");
            await onAdded();
          } catch (caught) {
            setFailure(humanMessage(caught));
          } finally {
            setBusy(false);
          }
        }}
      >
        <h2 className="font-semibold text-ink">Desde mi web</h2>
        <p className="text-xs text-ink-soft">
          Leemos hasta 20 páginas de tu sitio. Si cambias tu web, vuelve aquí y pulsa
          &quot;Actualizar desde mi web&quot;: no la leemos sola.
        </p>
        {failure ? <Alert tone="danger">{failure}</Alert> : null}
        <Field
          label="Dirección de tu web"
          inputMode="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="mitienda.pe"
        />
        <Button type="submit" loading={busy} disabled={!url.trim()}>
          Leer mi web
        </Button>
      </form>
    </Card>
  );
}
