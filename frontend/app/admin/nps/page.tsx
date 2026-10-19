"use client";

/**
 * Leo's NPS (Feature 013, C-107): what the businesses think of Leo, by plan, industry and
 * age, with their comments. Only the platform's operator sees it.
 */

import { useEffect, useState } from "react";

import { Alert, Skeleton } from "@/components/ui";
import { api, type NpsSummary } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const SEGMENTS: Record<string, string> = { plan: "Por plan", rubro: "Por rubro", antiguedad: "Por antigüedad" };

export default function NpsPage() {
  const [data, setData] = useState<NpsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.analytics.platformNps().then(setData).catch((caught) => setError(humanMessage(caught)));
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">NPS de Leo</h1>
        <p className="text-sm text-ink-soft">
          Se pregunta a dueños y administradores a los 30 días y luego cada 90. De −100 a 100.
        </p>
      </header>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {!data ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-line bg-surface p-4">
              <p className="text-xs text-ink-soft">NPS</p>
              <p className="mt-1 font-heading text-3xl font-semibold text-ink" data-testid="nps-score">
                {data.nps ?? "—"}
              </p>
            </div>
            <div className="rounded-xl border border-line bg-surface p-4">
              <p className="text-xs text-ink-soft">Respuestas</p>
              <p className="mt-1 font-heading text-3xl font-semibold text-ink">{data.respuestas}</p>
            </div>
            <div className="rounded-xl border border-line bg-surface p-4">
              <p className="text-xs text-ink-soft">Cerraron sin responder</p>
              <p className="mt-1 font-heading text-3xl font-semibold text-ink">{data.descartadas}</p>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {Object.entries(data.segmentos).map(([key, groups]) => (
              <section key={key} className="space-y-1 rounded-xl border border-line bg-surface p-4 text-sm">
                <h2 className="font-semibold text-ink">{SEGMENTS[key] ?? key}</h2>
                {Object.entries(groups).length ? (
                  Object.entries(groups).map(([name, value]) => (
                    <div key={name} className="flex justify-between">
                      <span className="text-ink-soft">{name}</span>
                      <span className="text-ink">
                        <strong>{value.nps ?? "—"}</strong> · {value.respuestas}
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="text-ink-soft">Sin respuestas.</p>
                )}
              </section>
            ))}
          </div>
          <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
            <h2 className="font-semibold text-ink">Comentarios</h2>
            {data.comentarios.length ? (
              <ul className="space-y-2 text-sm">
                {data.comentarios.map((c, index) => (
                  <li key={index} className="rounded-lg bg-surface-muted p-2">
                    <span className="font-semibold">{c.nota}/10</span> · {c.negocio}: {c.comentario}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-soft">Aún no hay comentarios.</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
