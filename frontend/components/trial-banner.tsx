"use client";

/**
 * The permanent trial banner (Clarification 004).
 *
 * Its job is to make the end of the trial impossible to be surprised by. It
 * shows both ways the trial can end - days and messages - because a customer
 * watching the calendar can still run out of messages on day three, and the
 * emails alone are not enough for somebody who does not read them.
 *
 * The tone escalates with urgency, but the *icon and the words* carry the
 * meaning; colour only reinforces it.
 */

import Link from "next/link";

import { Alert } from "@/components/ui";
import type { TenantSummary } from "@/lib/api/client";

/** Below this many days left, the banner stops being merely informational. */
const URGENT_DAYS = 3;
/** Fraction of the message allowance that counts as nearly spent. */
const URGENT_USAGE = 0.8;

function messagesLabel(used: number, limit: number): string {
  return `${used} de ${limit} mensajes usados`;
}

export function TrialBanner({ tenant }: { tenant: TenantSummary }) {
  if (tenant.status === "paused") {
    return (
      <Alert
        tone="danger"
        title="Tu prueba terminó"
        action={
          <Link
            href="/plans"
            className="shrink-0 self-start rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
          >
            Ver planes
          </Link>
        }
      >
        Tu agente dejó de responder automáticamente. Los mensajes de tus clientes siguen llegando a
        tu bandeja y puedes contestarlos a mano.
      </Alert>
    );
  }

  if (tenant.status === "archived") {
    return (
      <Alert tone="danger" title="Cuenta archivada">
        Escríbenos para reactivarla. No borramos nada.
      </Alert>
    );
  }

  const trial = tenant.trial;
  if (tenant.status !== "trial" || !trial) {
    return null;
  }

  const days = trial.days_left;
  const spent = trial.ai_messages_limit > 0 ? trial.ai_messages_used / trial.ai_messages_limit : 0;
  const urgent = (days !== null && days <= URGENT_DAYS) || spent >= URGENT_USAGE;

  return (
    <Alert
      tone={urgent ? "warning" : "info"}
      title={
        days === null
          ? "Estás en período de prueba"
          : days <= 0
            ? "Tu prueba termina hoy"
            : days === 1
              ? "Te queda 1 día de prueba"
              : `Te quedan ${days} días de prueba`
      }
      action={
        urgent ? (
          <Link
            href="/plans"
            className="shrink-0 self-start rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
          >
            Ver planes
          </Link>
        ) : undefined
      }
    >
      <span className="tabular">{messagesLabel(trial.ai_messages_used, trial.ai_messages_limit)}</span>
      {". "}
      Lo que ocurra primero termina la prueba.
    </Alert>
  );
}
