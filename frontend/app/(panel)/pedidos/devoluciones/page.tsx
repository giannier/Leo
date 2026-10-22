"use client";

/**
 * Devoluciones (feature 019, phase G): what customers ask to return, exchange or claim.
 *
 * The assistant registers each request with what the customer said and the photos they sent; a person
 * decides here, and the customer is told in their own chat. Those waiting come first.
 */

import { useCallback, useEffect, useState } from "react";

import { ReturnCard } from "@/components/aftersale/return-card";
import { OrdersNav } from "@/components/orders/orders-nav";
import { Alert, Skeleton, cx } from "@/components/ui";
import { api, type ReturnList } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const FILTERS = [
  { key: "pendientes", label: "Por revisar", estados: ["solicitada"] },
  { key: "en_curso", label: "Aprobadas", estados: ["aprobada"] },
  { key: "cerradas", label: "Cerradas", estados: ["resuelta", "rechazada", "cancelada"] },
  { key: "todas", label: "Todas", estados: [] as string[] },
] as const;

export default function DevolucionesPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("pendientes");
  const [list, setList] = useState<ReturnList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const chosen = FILTERS.find((f) => f.key === filter);
    try {
      setList(await api.aftersale.returns({ estado: chosen ? [...chosen.estados] : [] }));
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [filter]);

  useEffect(() => {
    setList(null);
    void load();
  }, [load]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Devoluciones y reclamos</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Lo que tus clientes piden devolver, cambiar o reclamar. El asistente solo lo registra: tú decides, y el cliente
          lee tu respuesta en su chat.
        </p>
      </header>

      <OrdersNav />

      <nav aria-label="Filtro" className="flex flex-wrap gap-2">
        {FILTERS.map((option) => (
          <button
            key={option.key}
            type="button"
            aria-pressed={filter === option.key}
            onClick={() => setFilter(option.key)}
            className={cx(
              "min-h-10 cursor-pointer rounded-full border px-3.5 text-sm transition-colors",
              filter === option.key
                ? "border-brand bg-brand font-semibold text-brand-fg"
                : "border-line-strong bg-surface text-ink-soft hover:bg-surface-muted",
            )}
          >
            {option.label}
            {option.key === "pendientes" && list ? ` (${list.pendientes})` : ""}
          </button>
        ))}
      </nav>

      {error ? <Alert tone="danger">{error}</Alert> : null}
      {list === null && !error ? <Skeleton className="h-40 w-full" /> : null}
      {list && list.items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line-strong bg-surface px-6 py-12 text-center">
          <p className="font-semibold text-ink">
            {filter === "pendientes" ? "No hay nada por revisar" : "No hay solicitudes aquí"}
          </p>
          <p className="mx-auto mt-1 max-w-sm text-sm text-ink-soft">
            Cuando un cliente pida devolver o cambiar algo por WhatsApp, el asistente lo registra y aparece en esta lista.
          </p>
        </div>
      ) : null}
      {list && list.items.length > 0 ? (
        <ul className="grid items-start gap-4 xl:grid-cols-2">
          {list.items.map((row) => (
            <ReturnCard key={row.id} row={row} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}
