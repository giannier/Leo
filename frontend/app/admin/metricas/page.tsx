"use client";

/**
 * Métricas de la plataforma (Feature 010, TASK-1005): the business of Leo.
 *
 * Health (states, sign-ups, conversion, churn, MRR), the margin per business
 * (what it paid in the range against what its model calls cost: the number
 * that decides prices), operation signals, and the model prices the costs are
 * computed with. Traces without a price are counted, never shown as free.
 */

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, Field, Skeleton, cx } from "@/components/ui";
import { api, type ModelCostRow, type PlatformMetrics } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const STATES: Record<string, string> = {
  trial: "En prueba",
  active: "Pagando",
  paused: "En pausa",
  archived: "Archivados",
};

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="text-xs text-ink-soft">{label}</p>
      <p className="mt-1 font-heading text-2xl font-semibold text-ink">{value}</p>
      {note ? <p className="mt-0.5 text-xs text-ink-faint">{note}</p> : null}
    </div>
  );
}

export default function MetricasPage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<PlatformMetrics | null>(null);
  const [costs, setCosts] = useState<ModelCostRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const end = new Date();
    const start = new Date(end.getTime() - (days - 1) * 86_400_000);
    try {
      const [report, prices] = await Promise.all([
        api.metrics.platform({ desde: start.toISOString().slice(0, 10), hasta: end.toISOString().slice(0, 10) }),
        api.metrics.modelCosts(),
      ]);
      setData(report);
      setCosts(prices);
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-ink">Métricas</h1>
          <p className="text-sm text-ink-soft">Cómo va Leo como negocio y qué cuesta cada cliente.</p>
        </div>
        <div className="flex gap-1" role="group" aria-label="Período">
          {[7, 30, 90].map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={days === value}
              onClick={() => setDays(value)}
              className={cx(
                "min-h-9 rounded-full border px-3 text-xs font-medium",
                days === value ? "border-brand bg-brand text-brand-fg" : "border-line bg-surface text-ink-soft",
              )}
            >
              {value} días
            </button>
          ))}
        </div>
      </header>
      {error ? <Alert tone="danger">{error}</Alert> : null}

      {!data ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="platform-kpis">
            <Tile label="MRR" value={`US$ ${Number(data.mrr_usd).toFixed(2)}`} note="Suscripciones vigentes, anual / 12" />
            <Tile
              label="Conversión prueba → pago"
              value={data.conversion === null ? "—" : `${Math.round(data.conversion * 100)} %`}
              note={`${data.convertidos} de ${data.registros} registros del período`}
            />
            <Tile label="Registros" value={String(data.registros)} />
            <Tile label="Bajas" value={String(data.bajas)} note="Cancelados o pausados por falta de pago" />
          </div>

          <Card title="Negocios por estado">
            <ul className="flex flex-wrap gap-4 text-sm">
              {Object.entries(data.negocios_por_estado).map(([state, count]) => (
                <li key={state}>
                  <span className="text-ink-soft">{STATES[state] ?? state}:</span>{" "}
                  <span className="font-semibold text-ink">{count}</span>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Margen por negocio">
            {data.trazas_sin_precio ? (
              <p className="mb-2 text-xs text-warning">
                {data.trazas_sin_precio} respuestas del período no tienen costo: falta el precio de su modelo abajo.
              </p>
            ) : null}
            {data.margen_por_negocio.length === 0 ? (
              <p className="text-sm text-ink-soft">Sin ingresos ni costos en el período.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm" data-testid="margins">
                  <thead className="text-xs text-ink-soft">
                    <tr>
                      <th className="py-1">Negocio</th>
                      <th>Ingreso</th>
                      <th>Costo del modelo</th>
                      <th>Margen</th>
                      <th>Tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.margen_por_negocio.map((row) => (
                      <tr key={row.tenant_id} className="border-t border-line">
                        <td className="py-1">{row.negocio}</td>
                        <td>US$ {row.ingreso_usd}</td>
                        <td>US$ {row.costo_llm_usd}</td>
                        <td className={Number(row.margen_usd) < 0 ? "font-semibold text-danger" : ""}>US$ {row.margen_usd}</td>
                        <td>{row.tokens.toLocaleString("es-PE")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Tile label="Conexiones pendientes" value={String(data.operacion.conexiones_pendientes)} />
            <Tile label="Sin procesar" value={String(data.operacion.sin_procesar)} />
            <Tile label="Envíos fallidos" value={String(data.operacion.envios_fallidos)} />
            <Tile
              label="Latencia p95 del agente"
              value={data.operacion.latencia_p95_ms === null ? "—" : `${(data.operacion.latencia_p95_ms / 1000).toFixed(1)} s`}
            />
            <Tile label="Números en riesgo" value={String(data.operacion.numeros_en_riesgo)} note="Calidad amarilla o roja" />
          </div>
        </>
      )}

      <ModelCosts rows={costs} onSaved={load} onError={setError} />
    </div>
  );
}

function ModelCosts({
  rows,
  onSaved,
  onError,
}: {
  rows: ModelCostRow[];
  onSaved: () => Promise<void>;
  onError: (text: string) => void;
}) {
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");

  return (
    <Card title="Precio de los modelos (US$ por 1 000 tokens)">
      <ul className="mb-3 space-y-1 text-sm" data-testid="model-costs">
        {rows.map((row) => (
          <li key={`${row.modelo}-${row.desde}`}>
            <span className="font-mono text-ink">{row.modelo}</span> · entrada {row.entrada_por_1k_usd} · salida{" "}
            {row.salida_por_1k_usd} · desde {new Date(row.desde).toLocaleDateString("es-PE")}
          </li>
        ))}
      </ul>
      <form
        className="flex flex-wrap items-end gap-2"
        aria-label="Nuevo precio"
        onSubmit={async (event) => {
          event.preventDefault();
          try {
            await api.metrics.setModelCost({ modelo: model.trim(), entrada_por_1k_usd: input.trim(), salida_por_1k_usd: output.trim() });
            setModel("");
            setInput("");
            setOutput("");
            await onSaved();
          } catch (caught) {
            onError(humanMessage(caught));
          }
        }}
      >
        <Field label="Modelo" value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-5.4-mini" />
        <Field label="Entrada" inputMode="decimal" value={input} onChange={(e) => setInput(e.target.value)} />
        <Field label="Salida" inputMode="decimal" value={output} onChange={(e) => setOutput(e.target.value)} />
        <Button type="submit" variant="secondary" disabled={!model.trim() || !input.trim() || !output.trim()}>
          Guardar precio
        </Button>
      </form>
      <p className="mt-2 text-xs text-ink-faint">
        Un precio nuevo rige desde hoy: lo que ya pasó conserva el costo con el que se registró.
      </p>
    </Card>
  );
}
