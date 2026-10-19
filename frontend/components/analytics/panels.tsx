"use client";

/**
 * Results beyond the summary (Feature 013): the sales funnel, products and customer
 * satisfaction. Numbers first, with their rates; bars only where a comparison helps.
 */

import { useEffect, useState } from "react";

import { money } from "@/components/crm/lead-drawer";
import { Alert, Button, Checkbox, Skeleton, cx } from "@/components/ui";
import {
  api,
  type AnalyticsSettings,
  type CsatReport,
  type FunnelReport,
  type FunnelStep,
  type ProductsReport,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

type Range = { desde: string; hasta: string };

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)} %`;
}

function Bar({ value, max, label }: { value: number; max: number; label: string }) {
  const width = max && value ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-32 shrink-0 truncate text-ink-soft sm:w-44">{label}</span>
      <div className="h-3 flex-1 overflow-hidden rounded-full bg-surface-muted">
        <div className="h-full rounded-full bg-accent" style={{ width: `${width}%` }} />
      </div>
      <span className="w-10 shrink-0 text-right font-semibold text-ink">{value}</span>
    </div>
  );
}

const STEPS: [keyof FunnelStep, string, keyof FunnelStep | null][] = [
  ["conversaciones", "Conversaciones", null],
  ["con_intencion", "Interesados", "tasa_intencion"],
  ["por_pagar", "Llegaron a pagar", "tasa_por_pagar"],
  ["ganados", "Compraron", "tasa_cierre"],
];

export function FunnelPanel({ range }: { range: Range }) {
  const [data, setData] = useState<FunnelReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.analytics.funnel(range).then(setData).catch((caught) => setError(humanMessage(caught)));
  }, [range]);

  if (error) return <Alert tone="danger">{error}</Alert>;
  if (!data) return <Skeleton className="h-48 w-full" />;
  const now = data.actual;
  const before = data.anterior;
  const top = Math.max(1, now.conversaciones);
  const origins = Object.entries(data.por_origen);
  const busiest = Object.entries(data.por_dia_semana);
  const peak = Math.max(1, ...busiest.map(([, v]) => v.conversaciones));
  return (
    <div className="space-y-6" data-testid="funnel">
      <section className="space-y-3 rounded-xl border border-line bg-surface p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-semibold text-ink">Embudo de ventas</h2>
          <p className="text-sm text-ink-soft">
            Conversión: <strong className="text-ink">{pct(now.conversion)}</strong>
            {before.conversion !== null ? ` (antes ${pct(before.conversion)})` : ""}
          </p>
        </div>
        {STEPS.map(([key, label, rate]) => (
          <div key={key} className="space-y-0.5">
            <Bar value={now[key] as number} max={top} label={label} />
            {rate ? (
              <p className="pl-34 text-xs text-ink-faint sm:pl-46">
                {pct(now[rate] as number | null)} del paso anterior · antes {before[key] as number}
              </p>
            ) : null}
          </div>
        ))}
      </section>
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
          <h2 className="font-semibold text-ink">¿De dónde llegan?</h2>
          {origins.length ? (
            origins.map(([origin, value]) => (
              <div key={origin} className="flex justify-between gap-2 text-sm">
                <span className="truncate text-ink-soft">{origin}</span>
                <span className="shrink-0 text-ink">
                  <strong>{value.conversaciones}</strong> · {value.ganados} compraron
                </span>
              </div>
            ))
          ) : (
            <p className="text-sm text-ink-soft">Sin conversaciones en este período.</p>
          )}
        </section>
        <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
          <h2 className="font-semibold text-ink">Días con más conversaciones</h2>
          {busiest.map(([day, value]) => (
            <Bar key={day} value={value.conversaciones} max={peak} label={day} />
          ))}
        </section>
      </div>
    </div>
  );
}

export function ProductsPanel({ range }: { range: Range }) {
  const [data, setData] = useState<ProductsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.analytics.products(range).then(setData).catch((caught) => setError(humanMessage(caught)));
  }, [range]);

  if (error) return <Alert tone="danger">{error}</Alert>;
  if (!data) return <Skeleton className="h-48 w-full" />;
  const top = Math.max(1, ...data.vendidos.map((p) => p.unidades));
  return (
    <div className="space-y-4" data-testid="products">
      {data.ticket_promedio && Object.keys(data.ticket_promedio).length ? (
        <div className="rounded-xl border border-line bg-surface p-4">
          <p className="text-xs text-ink-soft">Ticket promedio ({data.pedidos} pedidos)</p>
          <p className="mt-1 font-heading text-2xl font-semibold text-ink">
            {Object.entries(data.ticket_promedio)
              .map(([cur, amount]) => money(amount, cur))
              .join(" · ")}
          </p>
        </div>
      ) : null}
      <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
        <h2 className="font-semibold text-ink">Lo que más se vende</h2>
        {data.vendidos.length ? (
          data.vendidos.map((p) => <Bar key={p.producto} value={p.unidades} max={top} label={p.producto} />)
        ) : (
          <p className="text-sm text-ink-soft">Todavía no hay ventas confirmadas en este período.</p>
        )}
      </section>
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
          <h2 className="font-semibold text-ink">Preguntan pero no compran</h2>
          <p className="text-xs text-ink-soft">Revisa precio, fotos o disponibilidad.</p>
          {data.consultados_sin_venta.length ? (
            data.consultados_sin_venta.map((p) => (
              <div key={p.producto} className="flex justify-between text-sm">
                <span className="text-ink-soft">{p.producto}</span>
                <span className="font-semibold text-ink">{p.consultas} consultas</span>
              </div>
            ))
          ) : (
            <p className="text-sm text-ink-soft">Nada por aquí.</p>
          )}
        </section>
        <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
          <h2 className="font-semibold text-ink">Agotados que te piden</h2>
          <p className="text-xs text-ink-soft">Vale la pena reponerlos.</p>
          {data.agotados_pedidos.length ? (
            data.agotados_pedidos.map((p) => (
              <div key={p.producto} className="flex justify-between text-sm">
                <span className="text-ink-soft">{p.producto}</span>
                <span className="font-semibold text-ink">{p.consultas} consultas</span>
              </div>
            ))
          ) : (
            <p className="text-sm text-ink-soft">Nada por aquí.</p>
          )}
        </section>
      </div>
    </div>
  );
}

export function SatisfactionPanel({ range, canConfigure }: { range: Range; canConfigure: boolean }) {
  const [data, setData] = useState<CsatReport | null>(null);
  const [settings, setSettings] = useState<AnalyticsSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.analytics.csat(range).then(setData).catch((caught) => setError(humanMessage(caught)));
    api.analytics.settings().then(setSettings).catch(() => setSettings(null));
  }, [range]);

  async function save(next: AnalyticsSettings) {
    const before = settings;
    setSettings(next);
    setSaved(false);
    try {
      setSettings(await api.analytics.saveSettings(next));
      setSaved(true);
    } catch (caught) {
      setSettings(before);
      setError(humanMessage(caught));
    }
  }

  return (
    <div className="space-y-4" data-testid="satisfaction">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {settings ? (
        <section className="space-y-3 rounded-xl border border-line bg-surface p-4">
          <h2 className="font-semibold text-ink">Encuesta y reporte</h2>
          <Checkbox
            checked={settings.csat_activo}
            disabled={!canConfigure}
            onChange={(e) => void save({ ...settings, csat_activo: e.target.checked })}
            label="Preguntar a mis clientes qué tal los atendimos (del 1 al 5)"
            hint="Una hora después de una venta o de cerrar una conversación, solo dentro de las 24 h de WhatsApp y como máximo una vez al mes por cliente. Sin IA; cuenta como un mensaje de WhatsApp."
          />
          <Checkbox
            checked={settings.reporte_mensual}
            disabled={!canConfigure}
            onChange={(e) => void save({ ...settings, reporte_mensual: e.target.checked })}
            label="Enviarme un resumen por correo el día 1 de cada mes"
          />
          {saved ? <p className="text-xs text-success">Guardado.</p> : null}
        </section>
      ) : null}
      {!data ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-line bg-surface p-4">
              <p className="text-xs text-ink-soft">Satisfacción promedio</p>
              <p className="mt-1 font-heading text-2xl font-semibold text-ink" data-testid="csat-average">
                {data.promedio !== null ? `${data.promedio} / 5` : "—"}
              </p>
              <p className="text-xs text-ink-faint">{data.con_nota} respuestas</p>
            </div>
            <div className="rounded-xl border border-line bg-surface p-4">
              <p className="text-xs text-ink-soft">Encuestas enviadas</p>
              <p className="mt-1 font-heading text-2xl font-semibold text-ink">{data.enviadas}</p>
              <p className="text-xs text-ink-faint">{data.respondidas} respondidas</p>
            </div>
            <div className="rounded-xl border border-line bg-surface p-4">
              <p className="text-xs text-ink-soft">Atendidos solo por el asistente</p>
              <p className="mt-1 font-heading text-2xl font-semibold text-ink">
                {data.asistente.promedio !== null ? `${data.asistente.promedio} / 5` : "—"}
              </p>
              <p className="text-xs text-ink-faint">{data.asistente.respuestas} respuestas</p>
            </div>
          </div>
          <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
            <h2 className="font-semibold text-ink">Notas</h2>
            {["5", "4", "3", "2", "1"].map((n) => (
              <Bar
                key={n}
                value={data.distribucion[n] ?? 0}
                max={Math.max(1, ...Object.values(data.distribucion))}
                label={`${"★".repeat(Number(n))}`}
              />
            ))}
          </section>
          {data.por_persona && data.por_persona.length ? (
            <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
              <h2 className="font-semibold text-ink">Por persona del equipo</h2>
              <p className="text-xs text-ink-soft">Solo lo ves tú. Se muestra con 5 respuestas o más.</p>
              {data.por_persona.map((person) => (
                <div key={person.nombre} className="flex justify-between text-sm">
                  <span className="text-ink-soft">{person.nombre}</span>
                  <span className="text-ink">
                    <strong>{person.promedio !== null ? `${person.promedio} / 5` : "aún pocas"}</strong> ·{" "}
                    {person.respuestas}
                  </span>
                </div>
              ))}
            </section>
          ) : null}
          <section className="space-y-2 rounded-xl border border-line bg-surface p-4">
            <h2 className="font-semibold text-ink">Lo que dicen</h2>
            {data.comentarios.length ? (
              <ul className="space-y-2">
                {data.comentarios.map((c, index) => (
                  <li key={index} className={cx("rounded-lg bg-surface-muted p-2 text-sm text-ink")}>
                    {c.nota ? <span className="mr-1 font-semibold">{"★".repeat(c.nota)}</span> : null}
                    {c.comentario}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-soft">Aún no hay comentarios.</p>
            )}
          </section>
        </>
      )}
      {canConfigure ? (
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            const last = new Date();
            last.setDate(0);
            const month = `${last.getFullYear()}${String(last.getMonth() + 1).padStart(2, "0")}`;
            void api.analytics
              .monthlyPreview(month)
              .then((html) => {
                const tab = window.open("", "_blank");
                if (tab) tab.document.write(html);
              })
              .catch((caught) => setError(humanMessage(caught)));
          }}
        >
          Ver el reporte del mes pasado
        </Button>
      ) : null}
    </div>
  );
}
