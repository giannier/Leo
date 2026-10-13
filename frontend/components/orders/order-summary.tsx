"use client";

/** The period cards on top of Pedidos (presentational). */

import { Skeleton, cx } from "@/components/ui";
import type { OrderSummary } from "@/lib/api/client";

import { formatMoney } from "./labels";

export type Period = "hoy" | "7d" | "30d";

const PERIODS: { value: Period; label: string }[] = [
  { value: "hoy", label: "Hoy" },
  { value: "7d", label: "7 días" },
  { value: "30d", label: "30 días" },
];

export function PeriodSwitch({
  value,
  onChange,
}: {
  value: Period;
  onChange: (period: Period) => void;
}) {
  return (
    <div role="group" aria-label="Periodo del resumen" className="inline-flex rounded-lg border border-line-strong bg-surface p-0.5">
      {PERIODS.map((period) => (
        <button
          key={period.value}
          type="button"
          aria-pressed={value === period.value}
          onClick={() => onChange(period.value)}
          className={cx(
            "min-h-10 cursor-pointer rounded-md px-3 text-sm transition-colors duration-150",
            value === period.value
              ? "bg-brand font-semibold text-brand-fg"
              : "text-ink-soft hover:bg-surface-muted",
          )}
        >
          {period.label}
        </button>
      ))}
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
  highlight,
  testId,
}: {
  label: string;
  value: string;
  hint?: string;
  highlight?: boolean;
  testId: string;
}) {
  return (
    <div
      className={cx(
        "rounded-xl border p-4",
        highlight ? "border-warning/40 bg-warning-soft" : "border-line bg-surface",
      )}
    >
      <p className="text-xs font-medium text-ink-faint">{label}</p>
      <p data-testid={testId} className="tabular mt-1 text-2xl font-semibold text-ink">
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-xs text-ink-faint">{hint}</p> : null}
    </div>
  );
}

export function OrderSummaryCards({ summary }: { summary: OrderSummary | null }) {
  if (summary === null) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard
        testId="stat-conversiones"
        label="Ventas confirmadas"
        value={String(summary.conversiones)}
        hint="Pedidos confirmados o más allá"
      />
      <StatCard
        testId="stat-valor"
        label="Valor vendido"
        value={formatMoney(summary.valor_total, summary.moneda)}
      />
      <StatCard
        testId="stat-ticket"
        label="Ticket promedio"
        value={formatMoney(summary.ticket_promedio, summary.moneda)}
      />
      <StatCard
        testId="stat-pendientes"
        label="Por revisar"
        value={String(summary.pendientes_revision)}
        hint={summary.pendientes_revision > 0 ? "Esperan tu confirmación" : "Nada pendiente"}
        highlight={summary.pendientes_revision > 0}
      />
    </div>
  );
}
