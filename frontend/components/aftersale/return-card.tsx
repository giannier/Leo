"use client";

/**
 * One return, exchange, warranty or complaint (feature 019, phase G).
 *
 * The assistant only registers it; this is where a person decides, because refunding is money. Approving
 * or rejecting asks for what the customer will read (what happens next, or why not), and the customer is
 * told in their own chat. A closed request shows its history and nothing to press.
 */

import Link from "next/link";
import { useState } from "react";

import {
  ACTOR_LABELS,
  RETURN_REASONS,
  RETURN_STATES,
  RETURN_TYPES,
} from "@/components/aftersale/labels";
import { formatDateTime, formatMoney, noticeMessage } from "@/components/orders/labels";
import { useToast } from "@/components/toast";
import { Alert, Button, TextArea, cx } from "@/components/ui";
import { api, type ReturnDetail, type ReturnRow } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

export function StateChip({ state }: { state: string }) {
  const style = RETURN_STATES[state] ?? RETURN_STATES.solicitada;
  return <span className={cx("rounded-full px-2 py-0.5 text-xs font-semibold", style.tone)}>{style.label}</span>;
}

function Decision({ detail, onDone }: { detail: ReturnDetail; onDone: (next: ReturnDetail) => void }) {
  const toast = useToast();
  const [text, setText] = useState(detail.resolucion ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(to: "aprobada" | "rechazada" | "resuelta" | "cancelada") {
    setBusy(to);
    setError(null);
    try {
      const result = await api.aftersale.decide(detail.id, { a: to, resolucion: text.trim() || null });
      const notice = result.aviso_cliente ? noticeMessage(result.aviso_cliente) : null;
      toast.success("Guardado", notice?.text ?? RETURN_STATES[to].label);
      onDone(result.solicitud);
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  if (detail.estado === "rechazada" || detail.estado === "resuelta" || detail.estado === "cancelada") {
    return null;
  }
  const waiting = detail.estado === "solicitada";
  return (
    <div className="space-y-3 rounded-lg border border-line p-3">
      <TextArea
        label={waiting ? "Qué le vas a responder al cliente" : "Nota para el cliente (opcional)"}
        rows={3}
        maxLength={500}
        value={text}
        onChange={(e) => setText(e.target.value)}
        hint={
          waiting
            ? "Aprobar: qué pasará (cómo y cuándo le devuelves o le cambias). Rechazar: por qué no. El cliente lo lee en su chat."
            : "Si la resuelves, el cliente lo recibe en su chat."
        }
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        {waiting ? (
          <>
            <Button type="button" loading={busy === "aprobada"} disabled={!text.trim() || busy !== null} onClick={() => void decide("aprobada")}>
              Aprobar
            </Button>
            <Button type="button" variant="secondary" loading={busy === "rechazada"} disabled={!text.trim() || busy !== null} onClick={() => void decide("rechazada")}>
              Rechazar
            </Button>
          </>
        ) : (
          <Button type="button" loading={busy === "resuelta"} disabled={busy !== null} onClick={() => void decide("resuelta")}>
            Marcar como resuelta
          </Button>
        )}
        <Button type="button" variant="ghost" disabled={busy !== null} onClick={() => void decide("cancelada")}>
          Cancelar solicitud
        </Button>
      </div>
    </div>
  );
}

export function ReturnCard({ row }: { row: ReturnRow }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ReturnDetail | null>(null);
  const [state, setState] = useState(row.estado);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && detail === null) {
      try {
        setDetail(await api.aftersale.ret(row.id));
      } catch (caught) {
        setError(humanMessage(caught));
      }
    }
  }

  return (
    <li className="rounded-xl border border-line bg-surface" data-testid="return-card">
      <button
        type="button"
        onClick={() => void toggle()}
        aria-expanded={open}
        className="flex w-full cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 p-4 text-left"
      >
        <span className="font-semibold text-ink">
          {RETURN_TYPES[row.tipo]} #{row.numero}
        </span>
        <StateChip state={state} />
        <span className="text-sm text-ink-soft">
          Pedido #{row.pedido} · {row.cliente ?? "Cliente"} · {RETURN_REASONS[row.motivo]}
        </span>
        <span className="ml-auto text-xs text-ink-faint">{formatDateTime(row.creada)}</span>
      </button>
      {open ? (
        <div className="space-y-3 border-t border-line p-4 text-sm">
          {error ? <Alert tone="danger">{error}</Alert> : null}
          {detail === null && !error ? <p className="text-ink-faint">Cargando…</p> : null}
          {detail ? (
            <>
              {detail.detalle ? <p className="text-ink">«{detail.detalle}»</p> : null}
              <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-ink-faint">Cliente</dt>
                  <dd className="text-ink">
                    {detail.cliente ?? "—"} {detail.telefono ? `· ${detail.telefono}` : ""}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-faint">Pedido</dt>
                  <dd className="text-ink">
                    #{detail.pedido} · {formatMoney(detail.total_pedido, detail.moneda)}
                  </dd>
                </div>
                <div className="sm:col-span-2">
                  <dt className="text-xs text-ink-faint">Qué se devuelve</dt>
                  <dd className="text-ink">
                    {detail.items.length
                      ? detail.items.map((i) => `${i.cantidad} × ${i.nombre}`).join(", ")
                      : "Todo el pedido"}
                  </dd>
                </div>
              </dl>
              {detail.fotos > 0 ? (
                <p className="text-ink-soft">
                  El cliente mandó {detail.fotos} {detail.fotos === 1 ? "foto" : "fotos"}:{" "}
                  {detail.conversation_id ? (
                    <Link href={`/bandeja?c=${detail.conversation_id}`} className="font-semibold text-accent underline">
                      míralas en la conversación
                    </Link>
                  ) : (
                    "están en su conversación."
                  )}
                </p>
              ) : null}
              <Decision
                detail={detail}
                onDone={(next) => {
                  setDetail(next);
                  setState(next.estado);
                }}
              />
              <ol className="space-y-1 border-t border-line pt-3 text-xs text-ink-soft" aria-label="Historial">
                {detail.eventos.map((event, index) => (
                  <li key={index}>
                    {formatDateTime(event.en)} · {ACTOR_LABELS[event.actor] ?? event.actor}:{" "}
                    {RETURN_STATES[event.hacia]?.label ?? event.hacia}
                    {event.resolucion ? ` — ${event.resolucion}` : ""}
                  </li>
                ))}
              </ol>
              <div className="flex flex-wrap gap-3 text-sm">
                <Link href={`/pedidos/${detail.pedido_id}`} className="font-semibold text-accent underline">
                  Ver el pedido
                </Link>
                {detail.conversation_id ? (
                  <Link href={`/bandeja?c=${detail.conversation_id}`} className="font-semibold text-accent underline">
                    Abrir la conversación
                  </Link>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
