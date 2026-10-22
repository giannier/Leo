"use client";

/**
 * Asistente · Revisión semanal (feature 019, phase C).
 *
 * A short sample of real conversations to read once a week and mark: bien, regular or mal, and what
 * went wrong. It is how the owner finds out whether the assistant is doing its job, with no tool or
 * model in between (nothing here calls the LLM). What goes wrong most is shown first, to fix that.
 */

import { useCallback, useEffect, useState } from "react";

import { AssistantHeader } from "@/components/assistant/section-nav";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Checkbox, Skeleton, TextArea, cx } from "@/components/ui";
import { api, type ReviewItem, type ReviewPage } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const MOTIVOS: Record<string, string> = {
  invento_un_dato: "Inventó un dato",
  tono: "El tono no fue el adecuado",
  no_resolvio: "No resolvió lo que el cliente pedía",
  debio_derivar: "Debió pasarlo a una persona",
  muy_largo: "Respondió demasiado largo",
  repetitivo: "Se repitió",
};

const RATINGS = [
  { value: 1, label: "Bien", tone: "border-success text-success" },
  { value: 0, label: "Regular", tone: "border-warning text-warning" },
  { value: -1, label: "Mal", tone: "border-danger text-danger" },
] as const;

const WHO: Record<string, { name: string; bubble: string }> = {
  cliente: { name: "Cliente", bubble: "bg-surface-muted text-ink" },
  asistente: { name: "Asistente", bubble: "bg-accent/10 text-ink" },
  equipo: { name: "Tu equipo", bubble: "bg-warning-soft text-ink" },
};

function ratingLabel(value: number): string {
  return RATINGS.find((r) => r.value === value)?.label ?? "";
}

function Conversation({ item, onRated }: { item: ReviewItem; onRated: () => void }) {
  const toast = useToast();
  const mark = item.revision;
  const [open, setOpen] = useState(mark === null);
  const [rating, setRating] = useState<number | null>(mark?.rating ?? null);
  // What was just saved, shown at once while the list is read again.
  const [rated, setRated] = useState<number | null>(mark?.rating ?? null);
  const [reasons, setReasons] = useState<string[]>(mark?.motivos ?? []);
  const [note, setNote] = useState(mark?.nota ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(value: number, why: string[], text: string) {
    setBusy(true);
    setError(null);
    try {
      await api.assistant.rateReview(item.conversation_id, {
        rating: value,
        motivos: value < 1 ? why : [],
        nota: text.trim() || null,
      });
      toast.success("Marcada", ratingLabel(value));
      setRated(value);
      setOpen(false);
      onRated();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function choose(value: number) {
    setRating(value);
    // A good one needs nothing else: one tap and it is done.
    if (value === 1) void save(1, [], "");
  }

  if (!open && rated !== null) {
    return (
      <li className="flex flex-wrap items-center gap-3 rounded-lg border border-line p-3 text-sm">
        <span className="font-medium text-ink">{item.cliente}</span>
        <span className="text-ink-faint">· {item.canal === "web" ? "Chat web" : "WhatsApp"}</span>
        <span className="ml-auto font-semibold text-ink-soft">{ratingLabel(rated)}</span>
        <Button type="button" variant="ghost" onClick={() => setOpen(true)}>
          Cambiar
        </Button>
      </li>
    );
  }

  return (
    <li className="space-y-4 rounded-lg border border-line p-4">
      <p className="text-sm">
        <span className="font-semibold text-ink">{item.cliente}</span>{" "}
        <span className="text-ink-faint">· {item.canal === "web" ? "Chat web" : "WhatsApp"}</span>
      </p>
      <ol className="space-y-2" aria-label={`Conversación con ${item.cliente}`}>
        {item.mensajes.map((line, index) => {
          const who = WHO[line.de] ?? WHO.cliente;
          return (
            <li key={index} className={cx("max-w-[85%] rounded-lg px-3 py-2 text-sm", who.bubble, line.de === "cliente" ? "" : "ml-auto")}>
              <span className="block text-xs font-semibold text-ink-faint">{who.name}</span>
              <span className="whitespace-pre-wrap">{line.texto}</span>
            </li>
          );
        })}
      </ol>

      <div className="space-y-3">
        <p className="text-sm font-medium text-ink">¿Cómo lo hizo el asistente?</p>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Valoración">
          {RATINGS.map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={busy}
              aria-pressed={rating === option.value}
              onClick={() => choose(option.value)}
              className={cx(
                "min-h-11 cursor-pointer rounded-lg border px-4 text-sm font-semibold transition-colors",
                "disabled:cursor-not-allowed disabled:opacity-50",
                rating === option.value ? cx("bg-surface-muted", option.tone) : "border-line-strong text-ink-soft hover:bg-surface-muted",
              )}
            >
              {option.label}
            </button>
          ))}
        </div>

        {rating !== null && rating < 1 ? (
          <div className="space-y-3">
            <fieldset className="space-y-1">
              <legend className="text-sm font-medium text-ink">¿Qué falló? (puedes marcar varios)</legend>
              {Object.entries(MOTIVOS).map(([key, label]) => (
                <Checkbox
                  key={key}
                  label={label}
                  checked={reasons.includes(key)}
                  onChange={(e) => setReasons(e.target.checked ? [...reasons, key] : reasons.filter((r) => r !== key))}
                />
              ))}
            </fieldset>
            <TextArea
              label="Nota (opcional)"
              rows={2}
              maxLength={500}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              hint="Qué debió decir o hacer. Solo la ve tu equipo."
            />
            <Button type="button" loading={busy} onClick={() => void save(rating, reasons, note)}>
              Guardar
            </Button>
          </div>
        ) : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </li>
  );
}

function Summary({ page }: { page: ReviewPage }) {
  const s = page.resumen;
  if (!s.total) {
    return <p className="text-sm text-ink-soft">Todavía no hay revisiones de los últimos 30 días.</p>;
  }
  const faults = Object.entries(s.fallas);
  return (
    <div className="space-y-3 text-sm">
      <p className="text-ink">
        <span className="font-heading text-3xl font-semibold">{s.porcentaje_bien}%</span>{" "}
        <span className="text-ink-soft">
          salió bien ({s.bien} de {s.total} revisadas en 30 días: {s.regular} regulares, {s.mal} mal)
        </span>
      </p>
      {faults.length ? (
        <div>
          <p className="font-medium text-ink">Lo que más falló</p>
          <ul className="mt-1 space-y-1 text-ink-soft">
            {faults.map(([key, count]) => (
              <li key={key}>
                {MOTIVOS[key] ?? key} <span className="text-ink-faint">· {count}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default function ReviewPageView() {
  const [page, setPage] = useState<ReviewPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPage(await api.assistant.review());
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Revisión semanal"
        description="Lee unas conversaciones reales de la semana y di si el asistente lo hizo bien. Así sabes qué corregir primero."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {!page && !error ? <Skeleton className="h-64 w-full" /> : null}
      {page ? (
        <>
          <Card title="Cómo va">
            <Summary page={page} />
          </Card>
          <Card
            title={`Semana ${page.semana}`}
            description={
              page.items.length
                ? `${page.progreso.revisadas} de ${page.progreso.total} revisadas. Son clientes reales: léelas con cuidado.`
                : undefined
            }
          >
            {page.items.length ? (
              <ul className="grid items-start gap-4 xl:grid-cols-2">
                {page.items.map((item) => (
                  <Conversation key={item.conversation_id} item={item} onRated={() => void load()} />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-soft">
                Aún no hay conversaciones con clientes esta semana. Cuando el asistente atienda, aparecerán aquí.
              </p>
            )}
          </Card>
        </>
      ) : null}
    </div>
  );
}
