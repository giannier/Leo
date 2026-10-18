"use client";

/**
 * ¿Respondió bien? (Feature 018, TASK-1804, HU-06).
 *
 * Under each reply of the assistant in the simulator: 👍 or 👎. A 👎 asks what was
 * wrong, shows why the assistant answered that way (the stage, the situations it
 * applied, the tools and the knowledge it used: the turn's own trace) and offers
 * the shortcut that fixes it: a situation for this case, a frequent question, the
 * products or the business data. Nothing here calls the model; the rating is
 * kept for the business and for the learning suggestions to come.
 */

import Link from "next/link";
import { useState } from "react";

import { Button, cx } from "@/components/ui";
import { api, type SandboxDebug, type SandboxTrace, type Valoracion } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const PRODUCT_TOOLS = new Set(["buscar_productos", "ver_producto", "mostrar_opciones", "gestionar_pedido"]);

function link(path: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value.slice(0, 300));
  }
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

export function ReplyFeedback({
  conversationId,
  messageId,
  question,
  trace,
  debug,
  initial,
}: {
  conversationId: string;
  messageId: string;
  question?: string;
  trace?: SandboxTrace;
  debug?: SandboxDebug | null;
  initial?: Valoracion | null;
}) {
  const [rating, setRating] = useState<Valoracion | null>(initial ?? null);
  const [open, setOpen] = useState(false);
  const [motivo, setMotivo] = useState(initial?.motivo ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function rate(value: 1 | -1, reason?: string) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      if (rating?.rating === value && reason === undefined) {
        await api.sandbox.unrate(conversationId, messageId);
        setRating(null);
        setOpen(false);
      } else {
        setRating(
          await api.sandbox.rate(conversationId, messageId, {
            rating: value,
            motivo: reason?.trim() || null,
          }),
        );
        if (value === -1) setOpen(true);
        if (reason !== undefined) setSaved(true);
        if (value === 1) setOpen(false);
      }
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const tools = (debug?.herramientas_llamadas ?? []).map((t) => t.nombre);
  const usedTools = [
    ...new Set(tools.length ? tools : (trace?.tools_called ?? []).map((t) => String(t.tool ?? ""))),
  ].filter(Boolean);
  const sources = [
    ...new Set(
      (trace?.rag_sources ?? []).flatMap((s) => (Array.isArray(s.fuentes) ? s.fuentes.map(String) : [])),
    ),
  ];
  const situations = debug?.situaciones ?? [];

  return (
    <div className="space-y-2 text-xs" data-testid="reply-feedback">
      <div className="flex items-center gap-1.5 text-ink-faint">
        <span>¿Respondió bien?</span>
        <button
          type="button"
          aria-pressed={rating?.rating === 1}
          aria-label="Sí, respondió bien"
          disabled={busy}
          onClick={() => void rate(1)}
          className={cx(
            "min-h-8 cursor-pointer rounded-full border px-2.5",
            rating?.rating === 1 ? "border-success bg-success-soft text-success" : "border-line hover:bg-surface-muted",
          )}
        >
          👍
        </button>
        <button
          type="button"
          aria-pressed={rating?.rating === -1}
          aria-label="No, respondió mal"
          disabled={busy}
          onClick={() => (rating?.rating === -1 ? setOpen((o) => !o) : void rate(-1))}
          className={cx(
            "min-h-8 cursor-pointer rounded-full border px-2.5",
            rating?.rating === -1 ? "border-danger bg-danger/10 text-danger" : "border-line hover:bg-surface-muted",
          )}
        >
          👎
        </button>
      </div>
      {error ? <p className="text-danger">{error}</p> : null}

      {open && rating?.rating === -1 ? (
        <div className="space-y-3 rounded-lg border border-line bg-surface p-3" data-testid="feedback-panel">
          <label className="block space-y-1">
            <span className="font-medium text-ink">¿Qué estuvo mal o qué debió decir?</span>
            <textarea
              value={motivo}
              maxLength={500}
              rows={2}
              onChange={(e) => {
                setSaved(false);
                setMotivo(e.target.value);
              }}
              placeholder="Ej.: debió ofrecer el paquete de 5 antes de pedir mis datos"
              className="w-full rounded-lg border border-line-strong bg-surface px-2 py-1.5 text-sm text-ink"
            />
          </label>
          <div className="flex items-center gap-2">
            <Button type="button" variant="secondary" loading={busy} onClick={() => void rate(-1, motivo)}>
              Guardar
            </Button>
            {saved ? (
              <span role="status" className="text-success">
                Guardado
              </span>
            ) : null}
          </div>

          <div className="space-y-1 text-ink-soft" data-testid="feedback-why">
            <p className="font-medium text-ink">Por qué respondió así</p>
            {debug ? (
              <p>
                Etapa: {debug.etapa_antes} → {debug.etapa_despues}
              </p>
            ) : null}
            <p>
              Situaciones: {situations.length ? situations.map((s) => s.titulo).join(", ") : "ninguna"}
            </p>
            <p>Herramientas: {usedTools.length ? usedTools.join(", ") : "ninguna"}</p>
            {sources.length ? <p>Conocimiento: {sources.join(", ")}</p> : null}
          </div>

          <div className="space-y-1">
            <p className="font-medium text-ink">Arréglalo</p>
            <ul className="flex flex-wrap gap-1.5">
              <li>
                <Link
                  className="inline-flex min-h-8 items-center rounded-full border border-accent px-2.5 text-accent hover:bg-accent/10"
                  href={link("/asistente/situaciones", {
                    nueva: "1",
                    cuando: question,
                    ejemplo: motivo || undefined,
                  })}
                >
                  Crear una situación para este caso
                </Link>
              </li>
              <li>
                <Link
                  className="inline-flex min-h-8 items-center rounded-full border border-line px-2.5 text-ink hover:bg-surface-muted"
                  href={link("/asistente/informacion", { bloque: "faqs", pregunta: question })}
                >
                  Agregar una pregunta frecuente
                </Link>
              </li>
              {situations.length ? (
                <li>
                  <Link
                    className="inline-flex min-h-8 items-center rounded-full border border-line px-2.5 text-ink hover:bg-surface-muted"
                    href="/asistente/situaciones"
                  >
                    Revisar la situación que usó
                  </Link>
                </li>
              ) : null}
              {usedTools.some((t) => PRODUCT_TOOLS.has(t)) ? (
                <li>
                  <Link
                    className="inline-flex min-h-8 items-center rounded-full border border-line px-2.5 text-ink hover:bg-surface-muted"
                    href="/catalog"
                  >
                    Revisar mis productos
                  </Link>
                </li>
              ) : null}
              <li>
                <Link
                  className="inline-flex min-h-8 items-center rounded-full border border-line px-2.5 text-ink hover:bg-surface-muted"
                  href="/asistente/informacion"
                >
                  Revisar los datos del negocio
                </Link>
              </li>
            </ul>
          </div>
        </div>
      ) : null}
    </div>
  );
}
