"use client";

/**
 * Pedidos (Feature 004, TASK-432).
 *
 * The list an operator opens to answer one question: what is waiting for me?
 * The card on top counts it, the chips narrow it, the table opens it.
 *
 * Container only: it owns the state and the requests, and hands plain props to
 * `components/orders/*`. Two details that are easy to get wrong:
 *
 * - **Stale answers.** Typing in the search box fires a request per keystroke
 *   (after a short pause); an older, slower answer must not overwrite a newer
 *   one, so every request is aborted when the next one starts.
 * - **Simulator orders are hidden** unless asked for (they are not sales), which
 *   is why the toggle exists and why it is off by default.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui";
import {
  NO_FILTERS,
  OrderFilterBar,
  OrderTable,
  Pagination,
  hasFilters,
  type OrderFilters,
} from "@/components/orders/order-list";
import { OrderSummaryCards, PeriodSwitch, type Period } from "@/components/orders/order-summary";
import { api, type OrderPage, type OrderSummary } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const PAGE_SIZE = 20;
/** Pause after the last keystroke before searching. */
const SEARCH_DEBOUNCE_MS = 300;

export default function PedidosPage() {
  const [period, setPeriod] = useState<Period>("hoy");
  const [summary, setSummary] = useState<OrderSummary | null>(null);
  // Requests to return or exchange that wait for a person; the badge of the link.
  const [waitingReturns, setWaitingReturns] = useState(0);

  useEffect(() => {
    api.aftersale
      .returns({ estado: ["solicitada"] })
      .then((list) => setWaitingReturns(list.pendientes))
      .catch(() => setWaitingReturns(0));
  }, []);

  const [filters, setFilters] = useState<OrderFilters>(NO_FILTERS);
  const [query, setQuery] = useState(filters);
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<OrderPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The text waits for a pause; a chip or a date goes through the same short
  // delay, which costs nothing and keeps one code path.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(filters), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters]);

  useEffect(() => {
    const controller = new AbortController();
    setSummary(null);
    api.orders
      .summary(period, controller.signal)
      .then(setSummary)
      .catch((caught) => {
        if (!controller.signal.aborted) setError(humanMessage(caught));
      });
    return () => controller.abort();
  }, [period]);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        setResult(
          await api.orders.list(
            {
              estado: query.estados.length > 0 ? query.estados : undefined,
              q: query.q.trim() || undefined,
              desde: query.desde || undefined,
              hasta: query.hasta || undefined,
              sandbox: query.sandbox || undefined,
              page,
              page_size: PAGE_SIZE,
            },
            signal,
          ),
        );
        setLoading(false);
      } catch (caught) {
        if (signal?.aborted) return;
        setError(humanMessage(caught));
        setLoading(false);
      }
    },
    [query, page],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  function change(next: OrderFilters) {
    setFilters(next);
    setPage(1);
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-ink">Pedidos</h1>
          <p className="mt-1 text-sm text-ink-soft">
            Lo que tus clientes han pedido y los pagos que esperan tu confirmación.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Link
            href="/pedidos/devoluciones"
            className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-line-strong px-3.5 text-sm font-semibold text-ink hover:bg-surface-muted"
          >
            Devoluciones
            {waitingReturns ? (
              <span className="rounded-full bg-warning-soft px-2 py-0.5 text-xs font-semibold text-warning">
                {waitingReturns} por revisar
              </span>
            ) : null}
          </Link>
          <PeriodSwitch value={period} onChange={setPeriod} />
        </div>
      </header>

      <OrderSummaryCards summary={summary} />

      <OrderFilterBar filters={filters} counts={summary?.por_estado ?? null} onChange={change} />

      {error ? (
        <Alert
          tone="danger"
          action={
            <button
              type="button"
              onClick={() => void load()}
              className="cursor-pointer self-start text-sm font-semibold text-accent underline"
            >
              Reintentar
            </button>
          }
        >
          {error}
        </Alert>
      ) : null}

      <OrderTable
        orders={result?.items ?? null}
        loading={loading}
        filtered={hasFilters(query)}
        onClear={() => change(NO_FILTERS)}
      />

      {result ? (
        <Pagination page={page} total={result.total} pageSize={PAGE_SIZE} onPage={setPage} />
      ) : null}
    </div>
  );
}
