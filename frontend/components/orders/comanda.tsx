"use client";

/**
 * The comanda (feature 016): what the business is working on, by stage, one tap to move it.
 *
 * Presentational. The page owns the requests; this renders the columns and the cards, and says
 * what was tapped. It is meant for a tablet at the counter or in the kitchen: big targets, the
 * order that waits longest first, and how long each one has waited, in words and in colour.
 */

import Link from "next/link";

import { BagIcon, ClockIcon, MapPinIcon, TruckIcon } from "@/components/icons";
import { Button, cx } from "@/components/ui";
import type { BoardOrder } from "@/lib/api/client";

import { formatMoney } from "./labels";
import { nextAction, type NextAction } from "./stages";

export interface Column {
  key: string;
  title: string;
  hint: string;
}

export const COLUMNS: Column[] = [
  { key: "por_confirmar", title: "Por confirmar", hint: "Revisa el pago o el pedido" },
  { key: "confirmado", title: "Nuevos", hint: "Confirmados, sin empezar" },
  { key: "preparando", title: "Preparando", hint: "En la cocina o el almacén" },
  { key: "listo", title: "Listos", hint: "Para recoger o para salir" },
  { key: "en_camino", title: "En camino", hint: "Con el repartidor" },
  { key: "completado", title: "Entregados hoy", hint: "Ya cerrados" },
];

/** The column an order is in. */
export function columnOf(order: BoardOrder): string {
  if (order.estado === "en_curso") return order.etapa ?? "preparando";
  return order.estado;
}

/** Minutes since a moment, never negative (a clock a little behind must not say "-1 min"). */
export function minutesSince(iso: string, now: number): number {
  return Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60000));
}

export function waitText(minutes: number): string {
  if (minutes < 1) return "ahora";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

/** How worried to look about a wait, in the columns where waiting is the problem. */
function urgency(column: string, minutes: number): "calm" | "late" | "urgent" {
  if (column === "completado" || column === "en_camino") return "calm";
  if (minutes >= 45) return "urgent";
  if (minutes >= 20) return "late";
  return "calm";
}

const URGENCY_STYLES = {
  calm: "bg-surface-muted text-ink-soft",
  late: "bg-warning-soft text-warning",
  urgent: "bg-danger-soft text-danger",
} as const;

export function ComandaBoard({
  orders,
  now,
  busyId,
  onAdvance,
}: {
  orders: BoardOrder[];
  now: number;
  busyId: string | null;
  onAdvance: (order: BoardOrder, action: NextAction) => void;
}) {
  const byColumn = new Map<string, BoardOrder[]>(COLUMNS.map((column) => [column.key, []]));
  for (const order of orders) byColumn.get(columnOf(order))?.push(order);

  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(15rem,1fr))] items-start gap-4">
      {COLUMNS.map((column) => {
        const inColumn = byColumn.get(column.key) ?? [];
        return (
          <section
            key={column.key}
            aria-label={column.title}
            className="min-w-0 rounded-xl border border-line bg-surface-muted/60 p-3"
          >
            <header className="mb-3 flex items-baseline justify-between gap-2 px-1">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-ink">{column.title}</h2>
                <p className="truncate text-xs text-ink-faint">{column.hint}</p>
              </div>
              <span className="tabular rounded-full bg-surface px-2.5 py-0.5 text-sm font-semibold text-ink">
                {inColumn.length}
              </span>
            </header>
            {inColumn.length === 0 ? (
              <p className="rounded-lg border border-dashed border-line-strong px-3 py-6 text-center text-sm text-ink-faint">
                Nada aquí
              </p>
            ) : (
              <ul className="space-y-3">
                {inColumn.map((order) => (
                  <li key={order.id}>
                    <OrderCard
                      order={order}
                      column={column.key}
                      now={now}
                      busy={busyId === order.id}
                      onAdvance={onAdvance}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function OrderCard({
  order,
  column,
  now,
  busy,
  onAdvance,
}: {
  order: BoardOrder;
  column: string;
  now: number;
  busy: boolean;
  onAdvance: (order: BoardOrder, action: NextAction) => void;
}) {
  const minutes = minutesSince(order.desde, now);
  const action = nextAction(order.estado, order.etapa, order.tipo_entrega);
  const delivery = order.entrega;
  const waiting = order.estado === "por_confirmar";
  const where = [delivery?.direccion, delivery?.distrito].filter(Boolean).join(", ");

  return (
    <article className="rounded-xl border border-line bg-surface p-4 shadow-sm">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link
            href={`/pedidos/${order.id}`}
            className="font-heading text-lg font-semibold text-accent hover:underline"
          >
            #{order.numero}
          </Link>
          <p className="truncate text-sm text-ink">{order.cliente ?? "Sin nombre"}</p>
        </div>
        <span
          className={cx(
            "inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold",
            URGENCY_STYLES[urgency(column, minutes)],
          )}
          title="Tiempo en esta etapa"
        >
          <ClockIcon className="size-3.5" />
          {waitText(minutes)}
        </span>
      </header>

      <ul className="mt-3 space-y-1 text-sm">
        {order.items.map((item, index) => (
          <li key={`${item.nombre}-${index}`}>
            <span className="tabular font-semibold text-ink">{item.cantidad}×</span>{" "}
            <span className="text-ink">{item.nombre}</span>
            {item.selecciones.length > 0 ? (
              <span className="block pl-6 text-xs text-ink-soft">{item.selecciones.join(", ")}</span>
            ) : null}
          </li>
        ))}
      </ul>

      {delivery ? (
        <div className="mt-3 space-y-1 border-t border-line pt-3 text-sm">
          <p className="flex items-center gap-1.5 font-medium text-ink">
            {order.tipo_entrega === "delivery" ? (
              <TruckIcon className="size-4 shrink-0 text-ink-faint" />
            ) : (
              <BagIcon className="size-4 shrink-0 text-ink-faint" />
            )}
            {order.tipo_entrega === "delivery" ? "Delivery" : "Recojo en tienda"}
            {order.zona_envio ? <span className="font-normal text-ink-soft">· {order.zona_envio}</span> : null}
          </p>
          {order.tipo_entrega === "delivery" && where ? <p className="text-ink-soft">{where}</p> : null}
          {order.tipo_entrega === "delivery" && delivery.referencia ? (
            <p className="text-xs text-ink-faint">Ref: {delivery.referencia}</p>
          ) : null}
          <p className="text-xs text-ink-soft">
            {delivery.receptor ?? ""}
            {delivery.telefono ? (
              <>
                {delivery.receptor ? " · " : ""}
                <a href={`tel:${delivery.telefono}`} className="font-medium text-accent hover:underline">
                  {delivery.telefono}
                </a>
              </>
            ) : null}
          </p>
          {delivery.ubicacion_enlace ? (
            <a
              href={delivery.ubicacion_enlace}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline"
            >
              <MapPinIcon className="size-4" />
              Ver en el mapa
            </a>
          ) : null}
        </div>
      ) : null}

      <footer className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
        <div className="text-sm">
          <p className="tabular font-semibold text-ink">{formatMoney(order.total, order.moneda)}</p>
          <p
            className={cx(
              "text-xs font-medium",
              order.cobrar_al_entregar ? "text-warning" : waiting ? "text-ink-faint" : "text-success",
            )}
          >
            {order.cobrar_al_entregar ? "Cobrar al entregar" : waiting ? "Pago por revisar" : "Pagado"}
          </p>
        </div>
        {waiting ? (
          <Link
            href={`/pedidos/${order.id}`}
            className="inline-flex min-h-12 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
          >
            Revisar
          </Link>
        ) : action ? (
          <Button className="min-h-12" loading={busy} onClick={() => onAdvance(order, action)}>
            {action.label}
          </Button>
        ) : null}
      </footer>
      {order.notas > 0 ? (
        <p className="mt-2 text-xs text-warning">
          <Link href={`/pedidos/${order.id}`} className="font-medium underline">
            {order.notas === 1 ? "1 nota del cliente" : `${order.notas} notas del cliente`}
          </Link>
        </p>
      ) : null}
    </article>
  );
}
