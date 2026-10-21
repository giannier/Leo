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

import { useCallback, useEffect, useMemo, useState } from "react";

import { FunnelPanel, ProductsPanel, SatisfactionPanel } from "@/components/analytics/panels";
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

function waitMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1) return "menos de 1 min";
  if (value < 90) return `${Math.round(value)} min`;
  return `${(value / 60).toFixed(1)} h`;
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

function Resumen() {
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
          <h2 className="font-heading text-lg font-semibold text-ink">Resumen</h2>
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
            {report.traspasos && report.traspasos.total > 0 ? (
              <Tile
                label="Tu equipo responde en"
                value={waitMinutes(report.traspasos.mediana_minutos)}
                note={
                  report.traspasos.sin_atender
                    ? `${report.traspasos.sin_atender} sin responder · el 90 % en ${waitMinutes(report.traspasos.p90_minutos)}`
                    : `El 90 % en ${waitMinutes(report.traspasos.p90_minutos)}`
                }
                testid="kpi-handoff-response"
              />
            ) : null}
            {report.actividad?.citas ? (
              <Tile
                label="Citas agendadas"
                value={String(report.actividad.citas.agendadas)}
                note={[
                  `${report.actividad.citas.por_el_asistente} por el asistente`,
                  report.actividad.citas.faltas ? `${report.actividad.citas.faltas} faltas` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
                testid="kpi-appointments"
              />
            ) : null}
            {report.actividad?.cotizaciones ? (
              <Tile
                label="Cotizaciones pedidas"
                value={String(report.actividad.cotizaciones.pedidas)}
                note={`${report.actividad.cotizaciones.enviadas} ya enviadas`}
                testid="kpi-quotes"
              />
            ) : null}
            {report.actividad?.calificados ? (
              <Tile
                label="Clientes calificados"
                value={String(report.actividad.calificados.total)}
                note={`${report.actividad.calificados.calientes} calientes · ${report.actividad.calificados.tibios} tibios · ${report.actividad.calificados.frios} fríos`}
                testid="kpi-qualified"
              />
            ) : null}
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
            {report.origenes?.length ? (
              <section className="rounded-xl border border-line bg-surface p-4" aria-label="De dónde llegan" data-testid="lead-origins">
                <h2 className="font-semibold text-ink">De dónde llegan tus clientes</h2>
                <ul className="mt-2 space-y-1 text-sm">
                  {report.origenes.map((item) => (
                    <li key={item.origen} className="flex justify-between gap-3">
                      <span className="text-ink-soft">{item.origen}</span>
                      <span className="font-semibold text-ink">
                        {item.leads} {item.leads === 1 ? "lead" : "leads"}
                        {item.ganados ? <span className="font-normal text-ink-soft"> · {item.ganados} ganados</span> : null}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-xs text-ink-faint">
                  Sale del enlace con el que entró el cliente o de lo que dijo. Sin dato, «Directo».
                </p>
              </section>
            ) : null}
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
            {report.whatsapp_mes ? (
              <section className="rounded-xl border border-line bg-surface p-4" aria-label="WhatsApp este mes" data-testid="whatsapp-spend">
                <h2 className="font-semibold text-ink">WhatsApp este mes</h2>
                <p className="mt-1 font-heading text-2xl font-semibold text-ink">
                  {report.whatsapp_mes.costo_estimado_usd !== null ? `US$ ${report.whatsapp_mes.costo_estimado_usd}` : "—"}
                </p>
                <ul className="mt-2 space-y-1 text-sm">
                  <li className="flex justify-between">
                    <span className="text-ink-soft">Respuestas enviadas</span>
                    <span className="font-semibold text-ink">{report.whatsapp_mes.mensajes_servicio}</span>
                  </li>
                  <li className="flex justify-between">
                    <span className="text-ink-soft">Gratis que quedan</span>
                    <span className="font-semibold text-ink">{report.whatsapp_mes.gratis_restantes}</span>
                  </li>
                  <li className="flex justify-between">
                    <span className="text-ink-soft">Plantillas (utilidad / marketing)</span>
                    <span className="font-semibold text-ink">
                      {report.whatsapp_mes.plantillas_utilidad} / {report.whatsapp_mes.plantillas_marketing}
                    </span>
                  </li>
                </ul>
                <p className="mt-2 text-xs text-ink-faint">
                  Aproximado. Desde octubre de 2026 Meta cobra cada respuesta pasadas las 1,000 gratis del mes por número. Lo
                  factura YCloud, no Leo.
                </p>
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

const VIEWS = [
  ["resumen", "Resumen"],
  ["embudo", "Embudo"],
  ["productos", "Productos"],
  ["satisfaccion", "Satisfacción"],
] as const;
type View = (typeof VIEWS)[number][0];

export default function ResultadosPage() {
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);
  const [view, setView] = useState<View>("resumen");
  const [days, setDays] = useState(30);
  const range = useMemo(() => {
    const end = new Date();
    const start = new Date(end.getTime() - (days - 1) * 86_400_000);
    return { desde: iso(start), hasta: iso(end) };
  }, [days]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-2xl font-semibold text-ink">Resultados</h1>
        <p className="text-sm text-ink-soft">Lo que tu asistente hizo por tu negocio en WhatsApp.</p>
      </div>
      <nav aria-label="Vistas de resultados" className="flex gap-2 overflow-x-auto pb-1">
        {VIEWS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-current={view === key ? "page" : undefined}
            onClick={() => setView(key)}
            className={cx(
              "min-h-11 shrink-0 cursor-pointer rounded-full px-4 text-sm font-semibold",
              view === key ? "bg-brand text-brand-fg" : "border border-line bg-surface text-ink-soft hover:bg-surface-muted",
            )}
          >
            {label}
          </button>
        ))}
      </nav>
      {view === "resumen" ? (
        <Resumen />
      ) : (
        <>
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
          {view === "embudo" ? <FunnelPanel range={range} /> : null}
          {view === "productos" ? <ProductsPanel range={range} /> : null}
          {view === "satisfaccion" ? <SatisfactionPanel range={range} canConfigure={canConfigure} /> : null}
        </>
      )}
    </div>
  );
}
