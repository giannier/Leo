"use client";

/**
 * The assistant is paused in this conversation because it handed it to a person
 * (TASK-443, C-14). Shows why, until when, and lets the operator give it back.
 */

import { useCallback, useEffect, useState } from "react";

import { formatDateTime } from "@/components/orders/labels";
import { Alert, Button } from "@/components/ui";
import { api, type ConversationState } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const REASONS: Record<string, string> = {
  pide_humano: "El cliente pidió hablar con una persona",
  queja: "Queja",
  sin_respuesta: "El asistente no tenía la respuesta",
  asesoria: "Asesoría",
  cotizacion: "Cotización",
  otro: "Otro motivo",
};

export function HandoverBanner({ conversationId }: { conversationId: string }) {
  const [state, setState] = useState<ConversationState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await api.conversations.state(conversationId));
    } catch {
      // The order page works without it.
      setState(null);
    }
  }, [conversationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handover = state?.derivado;
  if (!handover) return null;

  return (
    <Alert
      tone="warning"
      title={`El asistente está pausado hasta ${formatDateTime(handover.hasta)}`}
      action={
        <Button
          type="button"
          variant="secondary"
          loading={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              setState(await api.conversations.resumeBot(conversationId));
            } catch (caught) {
              setError(humanMessage(caught));
            } finally {
              setBusy(false);
            }
          }}
        >
          Reactivar asistente
        </Button>
      }
    >
      <span data-testid="handover">
        {REASONS[handover.motivo] ?? handover.motivo}
        {handover.resumen ? `: ${handover.resumen}` : ""}. Lo que escriba el cliente se guarda sin
        respuesta hasta que lo atiendas o reactives al asistente.
      </span>
      {error ? <p className="mt-1 text-danger">{error}</p> : null}
    </Alert>
  );
}
