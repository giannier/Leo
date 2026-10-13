"use client";

/**
 * Live consumption on the real channel (HU-04).
 *
 * The trial banner above it is about the calendar and is loaded once with the
 * page. This is the meter, and it moves: an owner watching their agent work
 * through a busy afternoon wants to see the number change, not refresh the
 * page to find out it already ran out.
 *
 * Polling rather than a socket. The number changes at most a few times a
 * minute, a poll costs one small query, and a socket would add a connection
 * to maintain for a figure nobody is watching to the second.
 */

import { useEffect, useState } from "react";

import { Card, Skeleton } from "@/components/ui";
import { api, type Usage } from "@/lib/api/client";

/** Slow enough to be free, fast enough that the number is never stale news. */
const POLL_MS = 20_000;

/** Where the bar stops being reassuring. Matches the email warning. */
const URGENT_PERCENT = 80;

export function UsageMeter() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function read() {
      try {
        const next = await api.tenant.usage();
        if (!cancelled) {
          setUsage(next);
          setFailed(false);
        }
      } catch {
        // Deliberately quiet. This is a secondary read on a page that works
        // without it; an error banner here would tell somebody their agent is
        // broken when all that failed was a counter.
        if (!cancelled) setFailed(true);
      }
    }

    void read();
    const timer = setInterval(() => void read(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (failed && !usage) return null;
  if (!usage) {
    return (
      <Card>
        <Skeleton className="h-4 w-40" />
      </Card>
    );
  }

  const urgent = usage.percent_used >= URGENT_PERCENT;
  const remaining = Math.max(usage.ai_messages_limit - usage.ai_messages_used, 0);

  if (!usage.cobro_activo) {
    // Billing off: the count is still worth seeing, the limit is not enforced.
    return (
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">Consumo de tu agente</h2>
          <p className="tabular text-sm text-ink-faint">
            {usage.messages_in} recibidos · {usage.messages_out} enviados este mes
          </p>
        </div>
        <p className="mt-3 text-sm">
          <span className="tabular font-semibold">{usage.ai_messages_used}</span> respuestas del
          agente por WhatsApp. Sin límite en modo pruebas.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Consumo de tu agente</h2>
        <p className="tabular text-sm text-ink-faint">
          {usage.messages_in} recibidos · {usage.messages_out} enviados este mes
        </p>
      </div>

      <p className="mt-3 text-sm">
        <span className="tabular font-semibold">
          {usage.ai_messages_used} de {usage.ai_messages_limit}
        </span>{" "}
        respuestas del agente por WhatsApp.{" "}
        {remaining === 0 ? (
          <span className="font-semibold">Se acabaron.</span>
        ) : (
          <>Te quedan <span className="tabular font-semibold">{remaining}</span>.</>
        )}
      </p>

      {/* The number above is the message; the bar only reinforces it, so the
          bar is hidden from assistive technology rather than duplicated. */}
      <div
        className="mt-3 h-2 w-full overflow-hidden rounded-full bg-surface-muted"
        aria-hidden="true"
      >
        <div
          className={
            urgent ? "h-full rounded-full bg-warning" : "h-full rounded-full bg-accent"
          }
          style={{ width: `${usage.percent_used}%` }}
        />
      </div>
    </Card>
  );
}
