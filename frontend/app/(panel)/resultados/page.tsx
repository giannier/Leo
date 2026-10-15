"use client";

/**
 * Resultados (Feature 010, TASK-1004): what the assistant did for the business.
 *
 * KPI tiles first (a number with its change against the period before; no chart
 * where a number says it better), then conversations per day, handover reasons,
 * best sellers and the CSV. Every member sees it; an operator gets it without
 * money, because the API leaves sales out (RF-004). "Ventas" are only orders the
 * business confirmed, never estimates (C-001).
 */

import { useCallback, useEffect, useState } from "react";

import { ConversationsChart } from "@/components/metrics/conversations-chart";
import { Alert, Button, Field, Skeleton, cx } from "@/components/ui";
import { api, type MetricsReport, type MetricsSummary } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

const PERIODS = [
  { days: 7, label: "7 días" },
  { days: 30, label: "30 días" },
  { days: 90, label: "90 días" },
];

function iso(day: Date): string {
  return day.toISOString().slice(0, 10);
}

function change(now: number, before: number): { text: string; tone: string } | null {
  if (!before) return null;
  const pct = Math.round(((now - before) / before) * 100);
  return { text: `${pct > 0 ? "+" : ""}${pct} % vs. período anterior`, tone: "text-ink-soft" };
}

function seconds(value: number | null): string {
  if (value === null) return "—";
  return value < 120 ? `${value} s` : `${Math.round(value / 60)} min`;
}

function Tile({
  label,
  value,
  note,
  testid,
}: {
  label: string;
  value: string;
  note?: string | null;
  testid?: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4" data-testid={testid}>
      <p className="text-xs text-ink-soft">{label}</p>
      <p className="mt-1 font-heading text-2xl font-semibold text-ink">{value}</p>
      {note ? <p className="mt-0.5 text-xs text-ink-faint">{note}</p> : null}
    </div>
  );
}

export default function ResultadosPage() {
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);
  const [days, setDays] = useState(30);
  const [report, setReport] = useState<MetricsReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minutes, setMinutes] = useState("");

  const range = useCallback(() => {
    const end = new Date();
    const start = new Date(end.getTime() - (days - 1) * 86_400_000);
    return { desde: iso(start), hasta: iso(end) };
  }, [days]);

  // The previous report stays on screen while the next one loads: switching
  // the period must not blank the page or reset the chart's table view.
  const load = useCallback(async () => {
    try {
      const next = await api.metrics.report(range());
      setReport(next);
      setMinutes(String(next.minutos_por_conversacion));
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [range]);

  useEffect(() => {
    void load();
  }, [load]);

  const total: MetricsSummary | undefined = report?.total;
  const before: MetricsSummary | undefined = report?.anterior;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-ink">Resultados</h1>
          <p className="text-sm text-ink-soft">Lo que tu asistente hizo por tu negocio en WhatsApp.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1" role="group" aria-label="Período">
            {PERIODS.map((p) => (
              <button
                key={p.days}
                type="button"
                aria-pressed={days === p.days}
                onClick={() => setDays(p.days)}
                className={cx(
                  "min-h-9 rounded-full border px-3 text-xs font-medium",
                  days === p.days ? "border-brand bg-brand text-brand-fg" : "border-line bg-surface text-ink-soft",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          <Button
            type="button"
            variant="secondary"
            disabled={days > 90}
            onClick={async () => {
              try {
                const { desde, hasta } = range();
                const blob = await api.metrics.exportCsv(desde, hasta);
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = url;
                link.download = `leo-resultados-${desde}-${hasta}.csv`;
                link.click();
                URL.revokeObjectURL(url);
              } catch (caught) {
                setError(humanMessage(caught));
              }
            }}
          >
            Descargar CSV
          </Button>
        </div>
      </header>

      {error ? <Alert tone="danger">{error}</Alert> : null}

      {!report || !total || !before ? (
        <Skeleton className="h-72 w-full" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile
              label="Conversaciones"
              value={total.conversations.toLocaleString("es-PE")}
              note={change(total.conversations, before.conversations)?.text}
              testid="kpi-conversations"
            />
            <Tile
              label="Solo con el asistente"
              value={`${total.conversations ? Math.round((100 * total.conversations_agent_only) / total.conversations) : 0} %`}
              note={`${total.conversations_agent_only} sin que intervenga nadie`}
              testid="kpi-agent-only"
            />
            <Tile
              label="Horas ahorradas"
              value={total.horas_ahorradas.toLocaleString("es-PE")}
              note={`A ${report.minutos_por_conversacion} min por conversación`}
              testid="kpi-hours"
            />
            <Tile
              label="Primera respuesta"
              value={seconds(total.primera_respuesta_promedio_segundos)}
              note="En promedio"
            />
            <Tile label="Pasaron a una persona" value={String(total.handovers)} note={change(total.handovers, before.handovers)?.text} />
            <Tile label="Pedidos confirmados" value={String(total.orders_confirmed)} testid="kpi-orders" />
            {total.ventas ? (
              <Tile
                label="Ventas confirmadas"
                value={
                  Object.entries(total.ventas)
                    .map(([currency, amount]) => `${currency === "PEN" ? "S/" : currency} ${Number(amount).toLocaleString("es-PE", { minimumFractionDigits: 2 })}`)
                    .join(" · ") || "—"
                }
                note="Solo pedidos que confirmaste"
                testid="kpi-sales"
              />
            ) : null}
            <Tile label="Mensajes recibidos" value={total.messages_in.toLocaleString("es-PE")} />
          </div>

          <ConversationsChart days={report.por_dia} />

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-line bg-surface p-4" aria-label="Motivos de derivación">
              <h2 className="font-semibold text-ink">Por qué pasaron a una persona</h2>
              {Object.keys(total.derivaciones_por_motivo).length ? (
                <ul className="mt-2 space-y-1 text-sm">
                  {Object.entries(total.derivaciones_por_motivo).map(([reason, count]) => (
                    <li key={reason} className="flex justify-between">
                      <span className="text-ink-soft">{reason}</span>
                      <span className="font-semibold text-ink">{count}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-ink-soft">Ninguna en este período.</p>
              )}
            </section>
            {report.mas_vendidos ? (
              <section className="rounded-xl border border-line bg-surface p-4" aria-label="Más vendidos">
                <h2 className="font-semibold text-ink">Más vendidos</h2>
                {report.mas_vendidos.length ? (
                  <ul className="mt-2 space-y-1 text-sm" data-testid="best-sellers">
                    {report.mas_vendidos.map((item) => (
                      <li key={item.producto} className="flex justify-between">
                        <span className="text-ink-soft">{item.producto}</span>
                        <span className="font-semibold text-ink">{item.cantidad}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-sm text-ink-soft">Todavía no hay pedidos confirmados.</p>
                )}
              </section>
            ) : null}
          </div>

          {canConfigure ? (
            <form
              className="flex flex-wrap items-end gap-2"
              aria-label="Minutos por conversación"
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  await api.metrics.saveSettings(Number(minutes));
                  await load();
                } catch (caught) {
                  setError(humanMessage(caught));
                }
              }}
            >
              <div className="w-56">
                <Field
                  label="Minutos que tardaría una persona"
                  hint="Para calcular las horas ahorradas."
                  inputMode="numeric"
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value)}
                />
              </div>
              <Button type="submit" variant="ghost">
                Guardar
              </Button>
            </form>
          ) : null}
        </>
      )}
    </div>
  );
}
