"use client";

/**
 * One lead, in a side panel (Feature 012, HU-03): who it is, where it stands, its history
 * (who moved it and why), the conversation's notes, and the actions: move (with the reason
 * when it is lost), assign, write a note, open the conversation, "no es lead".
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { CloseIcon } from "@/components/icons";
import { Alert, Button, Field, Skeleton, cx } from "@/components/ui";
import {
  api,
  type LeadDetail,
  type LostReason,
  type PipelineStage,
  type TeamMember,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const CAUSES: Record<string, string> = {
  turno: "La conversación",
  "persona:mover": "Movido a mano",
  "persona:asignar": "Asignado",
  "turno:cotizacion_solicitada": "El asistente pasó la cotización",
  "persona:cotizacion_enviada": "Cotización enviada",
  "persona:descartar": "Marcado como no lead",
  "operador:confirmar": "Pedido confirmado",
  pedido_descartado: "El pedido venció sin respuesta",
};

function cause(text: string): string {
  if (text.startsWith("herramienta:")) return "El asistente armó el pedido";
  return CAUSES[text] ?? text;
}

type Quote = NonNullable<LeadDetail["lead"]["cotizacion"]>;

/** What to do next with a quote: the follow-up cadence (day 2 to 3, 7, 14, and close at 21). */
export function quoteHint(quote: Quote): string {
  if (quote.estado === "solicitada") return "El cliente la pidió: arma la cotización y envíasela.";
  const days = quote.dias_desde_envio ?? 0;
  if (days >= 21) return `Pasaron ${days} días: ciérrala con un mensaje cortés o márcala como perdida.`;
  if (days >= 14) return `Sin respuesta hace ${days} días: tercer seguimiento, con algo nuevo que aportar.`;
  if (days >= 7) return `Sin respuesta hace ${days} días: segundo seguimiento.`;
  if (days >= 2) return `Sin respuesta hace ${days} días: buen momento para escribirle.`;
  return "Enviada: dale un par de días para responder.";
}

/** A short chip for the board's card and the day's list. */
export function quoteChip(quote: Quote | null | undefined): string | null {
  if (!quote) return null;
  if (quote.estado === "solicitada") return "Cotización pedida";
  return `Cotización enviada hace ${quote.dias_desde_envio ?? 0} d`;
}

export function money(amount: string | null | undefined, currency: string | null | undefined): string | null {
  if (amount == null) return null;
  const symbol = !currency || currency === "PEN" ? "S/" : currency;
  return `${symbol} ${Number(amount).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function LeadDrawer({
  leadId,
  stages,
  reasons,
  members,
  onClose,
  onChanged,
}: {
  leadId: string;
  stages: PipelineStage[];
  reasons: LostReason[];
  members: TeamMember[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<LeadDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [losing, setLosing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [quoted, setQuoted] = useState("");

  const load = useCallback(async () => {
    try {
      setDetail(await api.crm.detail(leadId));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [leadId]);

  useEffect(() => {
    void load();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [load, onClose]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      if (result && typeof result === "object" && "lead" in result) setDetail(result as LeadDetail);
      else await load();
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const lead = detail?.lead;
  const lostStage = stages.find((s) => s.semantic === "perdido");
  const labels = Object.fromEntries(stages.map((s) => [s.key, s.label]));

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-ink/30" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Lead"
        onClick={(event) => event.stopPropagation()}
        className="flex h-full w-full min-w-0 max-w-full flex-col bg-surface shadow-xl sm:max-w-lg"
      >
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="font-heading text-lg font-semibold text-ink">{lead?.nombre ?? lead?.telefono ?? "Lead"}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar"
            className="flex size-11 cursor-pointer items-center justify-center rounded-lg text-ink-soft hover:bg-surface-muted"
          >
            <CloseIcon className="size-5" />
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto p-4 text-sm">
          {error ? <Alert tone="danger">{error}</Alert> : null}
          {!lead ? (
            <Skeleton className="h-40 w-full" />
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-3">
                <div>
                  <dt className="text-xs text-ink-faint">Etapa</dt>
                  <dd className="font-medium text-ink">{labels[lead.stage_key] ?? lead.stage_key}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-faint">Teléfono</dt>
                  <dd className="text-ink">{lead.telefono ?? "—"}</dd>
                </div>
                {lead.product_interest ? (
                  <div>
                    <dt className="text-xs text-ink-faint">Le interesa</dt>
                    <dd className="text-ink">{lead.product_interest}</dd>
                  </div>
                ) : null}
                {money(lead.amount_real ?? lead.amount_estimated, lead.moneda) ? (
                  <div>
                    <dt className="text-xs text-ink-faint">{lead.amount_real ? "Vendido" : "Monto estimado"}</dt>
                    <dd className="font-semibold text-ink">{money(lead.amount_real ?? lead.amount_estimated, lead.moneda)}</dd>
                  </div>
                ) : null}
                <div>
                  <dt className="text-xs text-ink-faint">Origen</dt>
                  <dd className="text-ink">{lead.origen ?? "Directo"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-faint">En esta etapa</dt>
                  <dd className="text-ink">{lead.days_in_stage} días</dd>
                </div>
              </dl>

              {lead.cotizacion ? (
                <section className="space-y-3 rounded-lg border border-line p-3" data-testid="lead-quote">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="font-semibold text-ink">Cotización</h3>
                    <span className="rounded-full bg-surface-muted px-2 py-0.5 text-xs font-semibold text-ink-soft">
                      {quoteChip(lead.cotizacion)}
                    </span>
                  </div>
                  {lead.cotizacion.resumen ? <p className="text-ink">{lead.cotizacion.resumen}</p> : null}
                  {Object.keys(lead.cotizacion.datos).length ? (
                    <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
                      {Object.entries(lead.cotizacion.datos).map(([name, value]) => (
                        <div key={name} className="contents">
                          <dt className="text-xs text-ink-faint">{name}</dt>
                          <dd className="text-ink">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : null}
                  <p className="text-xs text-ink-soft">{quoteHint(lead.cotizacion)}</p>
                  {lead.status === "active" && lead.cotizacion.estado === "solicitada" ? (
                    <div className="flex flex-wrap items-end gap-2">
                      <Field
                        label="Monto cotizado (opcional)"
                        inputMode="decimal"
                        placeholder="800.00"
                        value={quoted}
                        onChange={(e) => setQuoted(e.target.value)}
                      />
                      <Button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          const amount = Number(quoted.replace(",", "."));
                          void act(() =>
                            api.crm.quoteSent(lead.id, {
                              monto: quoted.trim() && !Number.isNaN(amount) ? amount.toFixed(2) : null,
                            }),
                          ).then(() => setQuoted(""));
                        }}
                      >
                        Ya la envié
                      </Button>
                    </div>
                  ) : null}
                </section>
              ) : null}

              <div className="flex flex-wrap gap-2">
                {lead.conversation_id ? (
                  <Link
                    href={`/bandeja?c=${lead.conversation_id}`}
                    className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg"
                  >
                    Abrir conversación
                  </Link>
                ) : null}
                {lead.status === "active" ? (
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void act(() => api.crm.discard(lead.id)).then(onClose)}
                  >
                    No es lead
                  </Button>
                ) : null}
              </div>

              <section className="space-y-2">
                <label className="flex flex-col gap-1.5 font-medium text-ink">
                  Mover a
                  <select
                    value={lead.stage_id}
                    disabled={busy}
                    onChange={(e) => {
                      const target = e.target.value;
                      if (target === lostStage?.id) {
                        setLosing(true);
                        return;
                      }
                      void act(() => api.crm.move(lead.id, target));
                    }}
                    className="min-h-11 w-full min-w-0 cursor-pointer rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
                  >
                    {stages
                      .filter((s) => s.is_active)
                      .map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.label}
                        </option>
                      ))}
                  </select>
                </label>
                {losing && lostStage ? (
                  <div className="flex flex-wrap items-end gap-2 rounded-lg border border-line p-3">
                    <label className="flex flex-1 flex-col gap-1.5 font-medium text-ink">
                      ¿Por qué se perdió?
                      <select
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        className="min-h-11 w-full min-w-0 cursor-pointer rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
                      >
                        <option value="">Elige un motivo</option>
                        {reasons
                          .filter((r) => r.is_active)
                          .map((r) => (
                            <option key={r.key} value={r.key}>
                              {r.label}
                            </option>
                          ))}
                      </select>
                    </label>
                    <Button
                      type="button"
                      disabled={!reason || busy}
                      onClick={() => {
                        setLosing(false);
                        void act(() => api.crm.move(lead.id, lostStage.id, reason));
                      }}
                    >
                      Marcar perdido
                    </Button>
                  </div>
                ) : null}

                <label className="flex flex-col gap-1.5 font-medium text-ink">
                  Asignado a
                  <select
                    value={lead.assigned_to ?? ""}
                    disabled={busy}
                    onChange={(e) => void act(() => api.crm.assign(lead.id, e.target.value || null))}
                    className="min-h-11 w-full min-w-0 cursor-pointer rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
                  >
                    <option value="">Nadie</option>
                    {members.map((m) => (
                      <option key={m.user_id} value={m.user_id}>
                        {m.full_name}
                      </option>
                    ))}
                  </select>
                </label>
              </section>

              <section className="space-y-2">
                <h3 className="font-semibold text-ink">Notas</h3>
                <ul className="space-y-2" data-testid="lead-notes">
                  {detail.notes.map((n) => (
                    <li key={n.id} className="rounded-lg bg-surface-muted p-2">
                      <p className="text-ink">{n.content}</p>
                      <p className="mt-0.5 text-xs text-ink-faint">
                        {n.author ?? "—"} · {new Date(n.created_at).toLocaleString("es-PE")}
                      </p>
                    </li>
                  ))}
                </ul>
                {lead.conversation_id ? (
                  <form
                    className="flex gap-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      const text = note.trim();
                      if (!text) return;
                      setNote("");
                      void act(() => api.crm.note(lead.id, text));
                    }}
                  >
                    <input
                      aria-label="Nueva nota"
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="Ej.: llamarlo el lunes"
                      className="min-h-11 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
                    />
                    <Button type="submit" variant="secondary" disabled={!note.trim() || busy}>
                      Anotar
                    </Button>
                  </form>
                ) : null}
              </section>

              <section className="space-y-2">
                <h3 className="font-semibold text-ink">Historial</h3>
                <ol className="space-y-1.5" data-testid="lead-history">
                  {detail.history.map((h, index) => (
                    <li key={index} className={cx("text-xs", h.actor === "user" ? "text-ink" : "text-ink-soft")}>
                      <span className="text-ink-faint">{new Date(h.created_at).toLocaleString("es-PE")}</span>{" "}
                      {h.to_stage ? (
                        <>
                          {h.from_stage ? `${labels[h.from_stage] ?? h.from_stage} → ` : "Entró en "}
                          <span className="font-medium">{labels[h.to_stage] ?? h.to_stage}</span>
                          {" · "}
                        </>
                      ) : null}
                      {h.actor_name ?? cause(h.cause)}
                      {typeof h.detail?.motivo === "string"
                        ? ` (${reasons.find((r) => r.key === h.detail.motivo)?.label ?? h.detail.motivo})`
                        : ""}
                    </li>
                  ))}
                </ol>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
