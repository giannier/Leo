"use client";

/**
 * The decisions on an order (presentational): confirm, reject, move along.
 *
 * Confirming and rejecting open a small form instead of firing on one tap. A
 * confirmation tells the customer their payment was accepted, a rejection tells
 * them it was not; neither should be one stray tap away. The form is also where
 * the two pieces of text live: the "next step" a confirmation can carry, and the
 * reason a rejection must carry (5 to 300 characters, as the API requires).
 * Discarding an order the customer paid for (a receipt waiting or approved) asks
 * for a reason too: the customer is told, and the receipt is voided.
 */

import { useState } from "react";

import { Alert, Button, Card, TextArea } from "@/components/ui";
import type { OperatorState, OrderDetail } from "@/lib/api/client";

import { STATE_LABELS } from "./labels";

const MAX_NEXT_STEP = 300;
const MIN_REASON = 5;
const MAX_REASON = 300;

/** Where an operator can take a confirmed order next (mirrors the API's transitions). */
const NEXT_STATES: Record<string, OperatorState[]> = {
  borrador: ["descartado"],
  confirmado: ["en_curso", "descartado"],
  en_curso: ["completado", "descartado"],
};

const MOVE_LABELS: Record<OperatorState, string> = {
  en_curso: "Marcar en curso",
  completado: "Marcar completado",
  descartado: "Descartar pedido",
};

type Mode = "idle" | "confirm" | "reject" | "discard";

/** Receipts that still stand for a payment; discarding the order voids them. */
const LIVE_PROOF_STATES = new Set(["por_verificar", "aprobado"]);

export function ReviewPanel({
  order,
  busy,
  error,
  onConfirm,
  onReject,
  onMove,
}: {
  order: OrderDetail;
  busy: boolean;
  error: string | null;
  onConfirm: (siguientePaso: string) => void;
  onReject: (motivo: string) => void;
  onMove: (estado: OperatorState, motivo?: string) => void;
}) {
  const [mode, setMode] = useState<Mode>("idle");
  const [nextStep, setNextStep] = useState("");
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);

  const moves = NEXT_STATES[order.estado] ?? [];
  if (order.estado !== "por_confirmar" && moves.length === 0) {
    return null;
  }
  const paid = order.comprobantes.some((proof) => LIVE_PROOF_STATES.has(proof.estado));

  function validReason(): string | null {
    const trimmed = reason.trim();
    if (trimmed.length < MIN_REASON) {
      setReasonError(`Cuéntale al cliente el motivo, al menos ${MIN_REASON} caracteres.`);
      return null;
    }
    setReasonError(null);
    return trimmed;
  }

  function submitReject() {
    const trimmed = validReason();
    if (trimmed) onReject(trimmed);
  }

  function submitDiscard() {
    const trimmed = validReason();
    if (trimmed) onMove("descartado", trimmed);
  }

  function move(state: OperatorState) {
    if (state === "descartado" && paid) {
      setMode("discard");
      return;
    }
    onMove(state);
  }

  return (
    <Card title="Tu decisión">
      {error ? (
        <div className="mb-4">
          <Alert tone="danger">{error}</Alert>
        </div>
      ) : null}

      {order.estado === "por_confirmar" ? (
        <div className="space-y-4">
          <p className="text-sm text-ink-soft">
            {order.comprobantes.length > 0
              ? "Compara el comprobante con el total del pedido antes de decidir."
              : "Este pedido se paga contra entrega. Confírmalo para empezar a prepararlo."}
          </p>

          {mode === "idle" ? (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => setMode("confirm")} disabled={busy}>
                Confirmar
              </Button>
              <Button variant="secondary" onClick={() => setMode("reject")} disabled={busy}>
                Rechazar
              </Button>
            </div>
          ) : null}

          {mode === "confirm" ? (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                onConfirm(nextStep.trim());
              }}
            >
              <TextArea
                label="Siguiente paso para el cliente"
                hint="Opcional. Va en el aviso: por ejemplo, cuándo y cómo recibirá su pedido."
                rows={3}
                maxLength={MAX_NEXT_STEP}
                value={nextStep}
                onChange={(event) => setNextStep(event.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button type="submit" loading={busy}>
                  Confirmar pedido
                </Button>
                <Button type="button" variant="ghost" onClick={() => setMode("idle")} disabled={busy}>
                  Cancelar
                </Button>
              </div>
            </form>
          ) : null}

          {mode === "reject" ? (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                submitReject();
              }}
            >
              <TextArea
                label="Motivo del rechazo"
                hint="El cliente lo recibe por WhatsApp. Sé claro y amable."
                rows={3}
                required
                maxLength={MAX_REASON}
                value={reason}
                error={reasonError ?? undefined}
                onChange={(event) => setReason(event.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button type="submit" variant="danger" loading={busy}>
                  Rechazar pedido
                </Button>
                <Button type="button" variant="ghost" onClick={() => setMode("idle")} disabled={busy}>
                  Cancelar
                </Button>
              </div>
            </form>
          ) : null}
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-ink-soft">
            Estado actual: <span className="font-semibold text-ink">{STATE_LABELS[order.estado]}</span>.
          </p>
          {mode === "discard" ? (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                submitDiscard();
              }}
            >
              <p className="text-sm text-ink-soft">
                El cliente ya pagó: su comprobante quedará anulado y le avisaremos por WhatsApp.
                {order.estado !== "borrador" ? " El pedido quedará marcado con devolución pendiente." : ""}
              </p>
              <TextArea
                label="Motivo de la anulación"
                hint="El cliente lo recibe por WhatsApp. Sé claro y amable."
                rows={3}
                required
                maxLength={MAX_REASON}
                value={reason}
                error={reasonError ?? undefined}
                onChange={(event) => setReason(event.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button type="submit" variant="danger" loading={busy}>
                  Anular pedido
                </Button>
                <Button type="button" variant="ghost" onClick={() => setMode("idle")} disabled={busy}>
                  Cancelar
                </Button>
              </div>
            </form>
          ) : (
            <div className="flex flex-wrap gap-2">
              {moves.map((state) => (
                <Button
                  key={state}
                  variant={state === "descartado" ? "secondary" : "primary"}
                  onClick={() => move(state)}
                  disabled={busy}
                >
                  {MOVE_LABELS[state]}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
