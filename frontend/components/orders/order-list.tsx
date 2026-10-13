"use client";

/** Filters and the table of orders (presentational: the page owns the state). */

import Link from "next/link";

import { AlertIcon, ReceiptIcon } from "@/components/icons";
import { Button, Checkbox, Field, Skeleton, cx } from "@/components/ui";
import type { OrderListItem, OrderSummary } from "@/lib/api/client";

import {
  STATE_LABELS,
  STATE_ORDER,
  StateBadge,
  formatDateTime,
  formatMoney,
  observationLabel,
} from "./labels";

export interface OrderFilters {
  estados: string[];
  q: string;
  desde: string;
  hasta: string;
  sandbox: boolean;
}

export const NO_FILTERS: OrderFilters = { estados: [], q: "", desde: "", hasta: "", sandbox: false };

export function hasFilters(filters: OrderFilters): boolean {
  return (
    filters.estados.length > 0 ||
    filters.q.trim() !== "" ||
    filters.desde !== "" ||
    filters.hasta !== "" ||
    filters.sandbox
  );
}

export function OrderFilterBar({
  filters,
  counts,
  onChange,
}: {
  filters: OrderFilters;
  /** Counts of the summary period, shown on each chip. */
  counts: OrderSummary["por_estado"] | null;
  onChange: (next: OrderFilters) => void;
}) {
  function toggle(state: string) {
    const estados = filters.estados.includes(state)
      ? filters.estados.filter((s) => s !== state)
      : [...filters.estados, state];
    onChange({ ...filters, estados });
  }

  return (
    <div className="space-y-3">
      <div role="group" aria-label="Filtrar por estado" className="flex flex-wrap gap-2">
        <button
          type="button"
          aria-pressed={filters.estados.length === 0}
          onClick={() => onChange({ ...filters, estados: [] })}
          className={chip(filters.estados.length === 0)}
        >
          Todos
        </button>
        {STATE_ORDER.map((state) => {
          const active = filters.estados.includes(state);
          return (
            <button
              key={state}
              type="button"
              aria-pressed={active}
              onClick={() => toggle(state)}
              className={chip(active)}
            >
              {STATE_LABELS[state]}
              {counts && counts[state] ? (
                <span className="tabular ml-1.5 text-xs opacity-70">{counts[state]}</span>
              ) : null}
            </button>
          );
        })}
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
        <Field
          label="Buscar"
          type="search"
          placeholder="Número, nombre o teléfono"
          value={filters.q}
          onChange={(event) => onChange({ ...filters, q: event.target.value })}
        />
        <Field
          label="Desde"
          type="date"
          value={filters.desde}
          max={filters.hasta || undefined}
          onChange={(event) => onChange({ ...filters, desde: event.target.value })}
        />
        <Field
          label="Hasta"
          type="date"
          value={filters.hasta}
          min={filters.desde || undefined}
          onChange={(event) => onChange({ ...filters, hasta: event.target.value })}
        />
      </div>

      <Checkbox
        label="Incluir pedidos de prueba"
        hint="Los que salen del modo prueba. No son ventas reales."
        checked={filters.sandbox}
        onChange={(event) => onChange({ ...filters, sandbox: event.target.checked })}
      />
    </div>
  );
}

function chip(active: boolean): string {
  return cx(
    "min-h-10 cursor-pointer rounded-full border px-3.5 text-sm transition-colors duration-150",
    active
      ? "border-brand bg-brand font-semibold text-brand-fg"
      : "border-line-strong bg-surface text-ink-soft hover:bg-surface-muted",
  );
}

export function OrderTable({
  orders,
  loading,
  filtered,
  onClear,
}: {
  orders: OrderListItem[] | null;
  loading: boolean;
  filtered: boolean;
  onClear: () => void;
}) {
  if (orders === null) {
    return <Skeleton className="h-64 w-full" />;
  }

  if (orders.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-line-strong bg-surface px-6 py-12 text-center">
        <ReceiptIcon className="size-8 text-ink-faint" />
        <p className="font-semibold text-ink">
          {filtered ? "Ningún pedido coincide con esos filtros" : "Todavía no hay pedidos"}
        </p>
        <p className="max-w-sm text-sm text-ink-soft">
          {filtered
            ? "Prueba con otro estado o amplía las fechas."
            : "Cuando un cliente termine de armar su pedido por WhatsApp, aparecerá aquí."}
        </p>
        {filtered ? (
          <Button variant="secondary" onClick={onClear}>
            Quitar filtros
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div
      aria-busy={loading || undefined}
      className={cx("overflow-hidden rounded-xl border border-line bg-surface", loading && "opacity-60")}
    >
      {/* Table from `md`; cards below, where six columns cannot fit. */}
      <table className="hidden w-full text-sm md:table">
        <thead className="bg-surface-muted text-left text-xs font-semibold text-ink-faint">
          <tr>
            <th className="px-4 py-2.5">Pedido</th>
            <th className="px-4 py-2.5">Cliente</th>
            <th className="px-4 py-2.5">Detalle</th>
            <th className="px-4 py-2.5 text-right">Total</th>
            <th className="px-4 py-2.5">Estado</th>
            <th className="px-4 py-2.5">Fecha</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {orders.map((order) => (
            <tr key={order.id} className="hover:bg-surface-muted/60">
              <td className="px-4 py-3 font-semibold">
                <Link href={`/pedidos/${order.id}`} className="text-accent hover:underline">
                  #{order.numero}
                </Link>
              </td>
              <td className="px-4 py-3">
                <p className="text-ink">{order.cliente.nombre ?? "Sin nombre"}</p>
                <p className="text-xs text-ink-faint">{order.cliente.telefono ?? "—"}</p>
              </td>
              <td className="max-w-xs px-4 py-3 text-ink-soft">
                <p className="truncate" title={order.items_resumen}>
                  {order.items_resumen || "—"}
                </p>
                <Flags order={order} />
              </td>
              <td className="tabular px-4 py-3 text-right font-medium text-ink">
                {formatMoney(order.total, order.moneda)}
              </td>
              <td className="px-4 py-3">
                <StateBadge state={order.estado} />
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-xs text-ink-faint">
                {formatDateTime(order.creado)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <ul className="divide-y divide-line md:hidden">
        {orders.map((order) => (
          <li key={order.id}>
            <Link href={`/pedidos/${order.id}`} className="block space-y-1.5 px-4 py-3 hover:bg-surface-muted/60">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold text-accent">#{order.numero}</span>
                <StateBadge state={order.estado} />
              </div>
              <p className="text-sm text-ink">
                {order.cliente.nombre ?? "Sin nombre"}{" "}
                <span className="text-xs text-ink-faint">{order.cliente.telefono}</span>
              </p>
              <p className="truncate text-sm text-ink-soft">{order.items_resumen || "—"}</p>
              <Flags order={order} />
              <div className="flex items-center justify-between text-xs text-ink-faint">
                <span>{formatDateTime(order.creado)}</span>
                <span className="tabular text-sm font-medium text-ink">
                  {formatMoney(order.total, order.moneda)}
                </span>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A receipt that arrived, and what looks off about it. */
function Flags({ order }: { order: OrderListItem }) {
  if (!order.tiene_comprobante && !order.devolucion_pendiente) return null;
  return (
    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
      {order.devolucion_pendiente ? (
        <span data-testid="refund-flag" className="inline-flex items-center gap-1 font-semibold text-danger">
          <AlertIcon className="size-3.5" />
          Devolución pendiente
        </span>
      ) : null}
      {order.tiene_comprobante ? <span className="text-ink-faint">Con comprobante</span> : null}
      {order.observaciones.map((code) => (
        <span key={code} className="inline-flex items-center gap-1 font-medium text-warning">
          <AlertIcon className="size-3.5" />
          {observationLabel(code)}
        </span>
      ))}
    </p>
  );
}

export function Pagination({
  page,
  total,
  pageSize,
  onPage,
}: {
  page: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <nav aria-label="Páginas" className="flex items-center justify-between text-sm text-ink-soft">
      <Button variant="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Anterior
      </Button>
      <span className="tabular">
        Página {page} de {pages}
      </span>
      <Button variant="secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Siguiente
      </Button>
    </nav>
  );
}
