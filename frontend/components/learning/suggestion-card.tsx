"use client";

/**
 * One suggestion of the inbox (feature 017, phase 3).
 *
 * The assistant proposes; a person decides. It shows the card as proposed (editable), the conversations
 * that gave rise to it, and what it clashes with. The reviewer may try a situation in the simulator first,
 * approve it (as it is or edited) or reject it, and a rejected one is not proposed again.
 */

import Link from "next/link";
import { useState } from "react";

import { SOURCE_LABELS, TYPE_LABELS } from "@/components/learning/labels";
import { useToast } from "@/components/toast";
import { Alert, Button, Checkbox, Field, TextArea, cx } from "@/components/ui";
import { api, type SuggestionDetail, type SuggestionRow } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function SuggestionCard({ row, onDecided }: { row: SuggestionRow; onDecided: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<SuggestionDetail | null>(null);
  const [card, setCard] = useState<Record<string, unknown>>({});
  const [answer, setAnswer] = useState("");
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = row.estado === "pendiente";

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && detail === null) {
      try {
        const loaded = await api.assistant.suggestion(row.id);
        setDetail(loaded);
        setCard(loaded.propuesta as Record<string, unknown>);
      } catch (caught) {
        setError(humanMessage(caught));
      }
    }
  }

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  const isSituation = row.tipo === "situacion";
  const isFact = row.tipo === "dato";

  return (
    <li className="rounded-xl border border-line bg-surface" data-testid="suggestion-card">
      <button
        type="button"
        onClick={() => void toggle()}
        aria-expanded={open}
        className="flex w-full cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 p-4 text-left"
      >
        <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-semibold text-accent">
          {TYPE_LABELS[row.tipo] ?? row.tipo}
        </span>
        <span className="min-w-0 flex-1 font-medium text-ink">{row.resumen}</span>
        {row.choques > 0 ? (
          <span className="rounded-full bg-warning-soft px-2 py-0.5 text-xs font-semibold text-warning">
            Choca con {row.choques}
          </span>
        ) : null}
        {!pending ? (
          <span
            className={cx(
              "rounded-full px-2 py-0.5 text-xs font-semibold",
              row.estado === "aprobada" ? "bg-success-soft text-success" : "bg-surface-muted text-ink-soft",
            )}
          >
            {row.estado === "aprobada" ? "Aprobada" : "Rechazada"}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="space-y-4 border-t border-line p-4 text-sm">
          {error ? <Alert tone="danger">{error}</Alert> : null}
          {detail === null && !error ? <p className="text-ink-faint">Cargando…</p> : null}
          {detail ? (
            <>
              <p className="text-xs text-ink-faint">Viene de: {SOURCE_LABELS[detail.fuente] ?? detail.fuente}</p>

              {detail.choques_detalle.length > 0 ? (
                <Alert tone="warning" title="Revisa que no se contradigan">
                  <ul className="list-disc pl-5">
                    {detail.choques_detalle.map((clash) => (
                      <li key={clash.id}>
                        «{clash.titulo}»: {clash.motivo}
                      </li>
                    ))}
                  </ul>
                </Alert>
              ) : null}

              {isSituation ? (
                <div className="space-y-3">
                  <Field
                    label="Título"
                    value={text(card.titulo)}
                    maxLength={80}
                    disabled={!pending}
                    onChange={(e) => setCard({ ...card, titulo: e.target.value })}
                  />
                  <TextArea
                    label="Cuándo aplica"
                    rows={2}
                    maxLength={300}
                    value={text(card.cuando_aplica)}
                    disabled={!pending}
                    onChange={(e) => setCard({ ...card, cuando_aplica: e.target.value })}
                  />
                  <TextArea
                    label="Qué hace el asistente"
                    rows={3}
                    maxLength={1000}
                    value={text(card.que_hace)}
                    disabled={!pending}
                    onChange={(e) => setCard({ ...card, que_hace: e.target.value })}
                  />
                  <Checkbox
                    label="Pasa la conversación a una persona"
                    checked={Boolean(card.derivar)}
                    disabled={!pending}
                    onChange={(e) => setCard({ ...card, derivar: e.target.checked })}
                  />
                </div>
              ) : null}

              {isFact ? (
                <div className="space-y-3">
                  <p className="text-ink">
                    Pregunta: <span className="font-semibold">«{text(card.pregunta)}»</span>
                  </p>
                  {pending ? (
                    <TextArea
                      label="Qué debe responder el asistente"
                      rows={3}
                      maxLength={2000}
                      value={answer}
                      onChange={(e) => setAnswer(e.target.value)}
                      hint="La respuesta la escribes tú: el asistente no inventa lo que tu negocio responde. Se agrega a tus preguntas frecuentes."
                    />
                  ) : null}
                </div>
              ) : null}

              {!isSituation && !isFact ? (
                <pre className="whitespace-pre-wrap rounded-lg bg-surface-muted p-3 text-xs text-ink">
                  {JSON.stringify(detail.propuesta, null, 2)}
                </pre>
              ) : null}

              {detail.origen.length > 0 ? (
                <details className="rounded-lg border border-line p-3">
                  <summary className="cursor-pointer font-medium text-ink">
                    {detail.origen.length === 1 ? "La conversación de donde salió" : "Las conversaciones de donde salió"}
                  </summary>
                  <div className="mt-3 space-y-4">
                    {detail.origen.map((origin) => (
                      <div key={origin.conversation_id} className="space-y-1">
                        <p className="text-xs text-ink-faint">
                          {origin.cliente} · {origin.canal === "web" ? "Chat web" : "WhatsApp"} ·{" "}
                          <Link href={`/bandeja?c=${origin.conversation_id}`} className="font-semibold text-accent underline">
                            abrir
                          </Link>
                        </p>
                        <ol className="space-y-1">
                          {origin.mensajes.map((line, index) => (
                            <li key={index} className="text-ink-soft">
                              <span className="font-semibold text-ink">
                                {line.de === "cliente" ? "Cliente" : line.de === "equipo" ? "Tu equipo" : "Asistente"}:
                              </span>{" "}
                              {line.texto}
                            </li>
                          ))}
                        </ol>
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}

              {detail.nota ? <p className="text-ink-soft">Tu nota: {detail.nota}</p> : null}

              {pending ? (
                rejecting ? (
                  <div className="space-y-3 rounded-lg border border-line p-3">
                    <Field
                      label="¿Por qué no? (opcional)"
                      value={note}
                      maxLength={500}
                      hint="No se vuelve a proponer igual."
                      onChange={(e) => setNote(e.target.value)}
                    />
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        loading={busy === "reject"}
                        onClick={() =>
                          void run("reject", async () => {
                            await api.assistant.rejectSuggestion(row.id, note.trim() || undefined);
                            toast.success("Descartada", "No se volverá a proponer");
                            onDecided();
                          })
                        }
                      >
                        Rechazar
                      </Button>
                      <Button type="button" variant="ghost" onClick={() => setRejecting(false)}>
                        Cancelar
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      loading={busy === "approve"}
                      disabled={busy !== null || (isFact && !answer.trim())}
                      onClick={() =>
                        void run("approve", async () => {
                          const result = await api.assistant.approveSuggestion(row.id, {
                            editado: isSituation ? card : null,
                            respuesta: isFact ? answer.trim() : null,
                          });
                          toast.success(
                            "Aprobada",
                            result.indexada ? "El asistente ya la usa" : "Guardada; falta indexarla, reintenta en un momento",
                          );
                          onDecided();
                        })
                      }
                    >
                      Aprobar
                    </Button>
                    {isSituation ? (
                      <Button
                        type="button"
                        variant="secondary"
                        loading={busy === "draft"}
                        disabled={busy !== null}
                        onClick={() =>
                          void run("draft", async () => {
                            const { situation_id } = await api.assistant.draftSuggestion(row.id);
                            window.open(`/sandbox?probar=${situation_id}`, "_blank", "noopener");
                          })
                        }
                      >
                        Probar en el simulador
                      </Button>
                    ) : null}
                    <Button type="button" variant="ghost" disabled={busy !== null} onClick={() => setRejecting(true)}>
                      Rechazar
                    </Button>
                  </div>
                )
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
