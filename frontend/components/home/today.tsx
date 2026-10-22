"use client";

/**
 * "Hoy" on the panel home: what needs a person right now, one number per thing, each a link to
 * where it is done.
 *
 * Once the agent is live, nobody opens the home to read a checklist: they open it to see whether
 * something is waiting. Every figure is read on its own and fails quietly to a dash, so one slow
 * answer never holds the others back, and the strip refreshes while the page stays open.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { Skeleton, cx } from "@/components/ui";
import { formatMoney } from "@/components/orders/labels";
import { api } from "@/lib/api/client";

const REFRESH_MS = 30_000;

interface Figures {
  /** Orders waiting for a person: to confirm, or confirmed and not started. */
  waiting: number | null;
  /** Orders being prepared, ready or on their way. */
  working: number | null;
  /** Conversations that need a person to answer. */
  conversations: number | null;
  returns: number | null;
  sales: { total: string; currency: string; count: number } | null;
}

const EMPTY: Figures = { waiting: null, working: null, conversations: null, returns: null, sales: null };

async function read(): Promise<Figures> {
  const [board, conversations, returns, summary] = await Promise.allSettled([
    api.orders.board(),
    api.conversations.list({ filtro: "necesitan_humano" }),
    api.aftersale.returns({ estado: ["solicitada"] }),
    api.orders.summary("hoy"),
  ]);
  const orders = board.status === "fulfilled" ? board.value.ordenes : null;
  return {
    waiting: orders ? orders.filter((o) => o.estado === "por_confirmar" || o.estado === "confirmado").length : null,
    working: orders ? orders.filter((o) => o.estado === "en_curso").length : null,
    conversations: conversations.status === "fulfilled" ? conversations.value.esperando : null,
    returns: returns.status === "fulfilled" ? returns.value.pendientes : null,
    sales:
      summary.status === "fulfilled"
        ? { total: summary.value.valor_total, currency: summary.value.moneda, count: summary.value.conversiones }
        : null,
  };
}

export function TodayStrip() {
  const [figures, setFigures] = useState<Figures | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      read()
        .then((next) => alive && setFigures(next))
        .catch(() => alive && setFigures(EMPTY));
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (figures === null) {
    return (
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} className="h-28 w-full" />
        ))}
      </div>
    );
  }

  return (
    <section aria-label="Hoy" className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
      <Tile
        href="/pedidos/comanda"
        label="Pedidos por atender"
        value={figures.waiting}
        hint="Por confirmar o sin empezar"
        attention={(figures.waiting ?? 0) > 0}
      />
      <Tile href="/pedidos/comanda" label="Pedidos en marcha" value={figures.working} hint="Preparando, listos o en camino" />
      <Tile
        href="/bandeja"
        label="Esperan a una persona"
        value={figures.conversations}
        hint="Conversaciones que necesitan tu respuesta"
        attention={(figures.conversations ?? 0) > 0}
      />
      <Tile
        href="/pedidos/devoluciones"
        label="Devoluciones por revisar"
        value={figures.returns}
        hint="Cambios y reclamos"
        attention={(figures.returns ?? 0) > 0}
      />
      <Tile
        href="/pedidos"
        label="Ventas de hoy"
        value={figures.sales ? formatMoney(figures.sales.total, figures.sales.currency) : null}
        hint={
          figures.sales
            ? figures.sales.count === 1
              ? "1 pedido confirmado"
              : `${figures.sales.count} pedidos confirmados`
            : undefined
        }
      />
    </section>
  );
}

function Tile({
  href,
  label,
  value,
  hint,
  attention = false,
}: {
  href: string;
  label: string;
  value: number | string | null;
  hint?: string;
  /** Something is waiting: the tile says so with a colour and the number, never the colour alone. */
  attention?: boolean;
}) {
  return (
    <Link
      href={href}
      className={cx(
        "group block rounded-xl border p-4 transition-colors sm:p-5",
        attention
          ? "border-warning/40 bg-warning-soft hover:border-warning"
          : "border-line bg-surface hover:border-line-strong",
      )}
    >
      <p className="text-sm text-ink-soft">{label}</p>
      <p className={cx("tabular mt-1 whitespace-nowrap font-heading text-2xl font-semibold sm:text-3xl", attention ? "text-warning" : "text-ink")}>
        {value ?? "—"}
      </p>
      {hint ? <p className="mt-1 text-xs text-ink-faint">{hint}</p> : null}
    </Link>
  );
}
