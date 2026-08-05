"use client";

/**
 * The webhooks the pipeline gave up on (RF-007).
 *
 * Every row here is a customer's message that never got an answer, and the
 * screen exists so that "dead letter" means "waiting for a person" rather
 * than "discarded". A parser bug found on Tuesday can be fixed and Monday's
 * messages replayed.
 *
 * Deliberately plain. This is read during an incident, by somebody who wants
 * to know how many, what kind, and what stopped them - and then to press one
 * button. The raw payload is not shown: it is in the database for whoever
 * needs it, and putting a JSON blob on screen would bury the three fields
 * that decide what to do.
 */

import { useCallback, useEffect, useState } from "react";

import { AlertIcon } from "@/components/icons";
import { Alert, Button, Card, Skeleton } from "@/components/ui";
import { api, type DeadLetter } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

function when(iso: string): string {
  return new Date(iso).toLocaleString("es-PE", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function DeadLettersPage() {
  const [letters, setLetters] = useState<DeadLetter[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setLetters(await api.admin.deadLetters());
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function retry(id: string) {
    setRetrying(id);
    setDone(null);
    try {
      await api.admin.retryDeadLetter(id);
      setDone(id);
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setRetrying(null);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Mensajes sin procesar</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Webhooks que fallaron cinco veces y dejaron de reintentarse solos. No se borró nada:
          cada uno se puede volver a encolar.
        </p>
      </header>

      {error ? (
        <Alert tone="danger" title="No pudimos cargar la cola">
          {error}
        </Alert>
      ) : null}

      {done ? (
        <Alert tone="success" title="Reencolado">
          El worker lo va a procesar en unos segundos. Si vuelve a fallar, reaparece acá.
        </Alert>
      ) : null}

      {!letters && !error ? (
        <Card>
          <div className="space-y-3">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
          </div>
        </Card>
      ) : null}

      {letters && letters.length === 0 ? (
        <Card>
          <p className="text-sm text-ink-soft">
            No hay nada pendiente. Todos los mensajes que llegaron se procesaron.
          </p>
        </Card>
      ) : null}

      {letters && letters.length > 0 ? (
        <Card title={`${letters.length} pendientes`}>
          <ul className="divide-y divide-line">
            {letters.map((letter) => (
              <li key={letter.id} className="py-4 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-2 text-sm font-medium text-ink">
                      <AlertIcon className="size-4 shrink-0 text-danger" />
                      {letter.event_type ?? "Evento no reconocido"}
                    </p>
                    <p className="mt-1 text-xs text-ink-faint">
                      {letter.phone_e164 ? `${letter.phone_e164} · ` : ""}
                      {when(letter.created_at)} · {letter.attempts} intentos
                    </p>
                    {letter.error_msg ? (
                      <p className="mt-2 break-words rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink-soft">
                        {letter.error_msg}
                      </p>
                    ) : null}
                  </div>

                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => void retry(letter.id)}
                    disabled={retrying === letter.id}
                  >
                    {retrying === letter.id ? "Reencolando…" : "Reintentar"}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
