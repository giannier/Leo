"use client";

/**
 * The inbox's list (Feature 007, TASK-707): filters, search and the conversations,
 * most recent first, with who has each one and how many messages are unread.
 */

import { Button, Skeleton, cx } from "@/components/ui";
import type { InboxFilter, InboxItem } from "@/lib/api/client";

export const FILTERS: { value: InboxFilter; label: string }[] = [
  { value: "todas", label: "Todas" },
  { value: "necesitan_humano", label: "Necesitan a una persona" },
  { value: "agente", label: "Con el asistente" },
  { value: "tomadas", label: "Tomadas" },
  { value: "mias", label: "Mías" },
  { value: "cerradas", label: "Cerradas" },
];

function when(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("es-PE", { day: "numeric", month: "short" });
}

export function ConversationList({
  items,
  filter,
  search,
  sandbox,
  waiting,
  selected,
  more,
  onFilter,
  onSearch,
  onSandbox,
  onSelect,
  onMore,
}: {
  items: InboxItem[] | null;
  filter: InboxFilter;
  search: string;
  sandbox: boolean;
  waiting: number;
  selected: string | null;
  more: boolean;
  onFilter: (filter: InboxFilter) => void;
  onSearch: (text: string) => void;
  onSandbox: (on: boolean) => void;
  onSelect: (id: string) => void;
  onMore: () => void;
}) {
  return (
    <section className="flex h-full min-h-0 flex-col gap-3" aria-label="Conversaciones">
      <input
        type="search"
        aria-label="Buscar conversaciones"
        placeholder="Buscar por nombre, teléfono o mensaje"
        className="min-h-11 w-full rounded-lg border border-line-strong bg-surface px-3 text-sm"
        value={search}
        onChange={(event) => onSearch(event.target.value)}
      />
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtrar">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={filter === option.value}
            onClick={() => onFilter(option.value)}
            className={cx(
              "min-h-9 rounded-full border px-3 text-xs font-medium",
              filter === option.value
                ? "border-brand bg-brand text-brand-fg"
                : "border-line bg-surface text-ink-soft hover:bg-surface-muted",
            )}
          >
            {option.label}
            {option.value === "necesitan_humano" && waiting > 0 ? (
              <span className="ml-1 rounded-full bg-danger px-1.5 text-[11px] text-white" data-testid="waiting-count">
                {waiting}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 text-xs text-ink-soft">
        <input type="checkbox" checked={sandbox} onChange={(event) => onSandbox(event.target.checked)} />
        Ver las del simulador
      </label>

      {items === null ? (
        <Skeleton className="h-64 w-full" />
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-ink-soft">
          No hay conversaciones aquí.
        </p>
      ) : (
        <ul className="min-h-0 flex-1 divide-y divide-line overflow-y-auto rounded-xl border border-line bg-surface" data-testid="inbox-list">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => onSelect(item.id)}
                aria-current={selected === item.id ? "true" : undefined}
                className={cx(
                  "flex w-full flex-col gap-0.5 px-3 py-2.5 text-left hover:bg-surface-muted",
                  selected === item.id && "bg-surface-muted",
                )}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className={cx("truncate text-sm text-ink", item.no_leidos > 0 && "font-semibold")}>
                    {item.contacto.nombre ?? item.contacto.telefono ?? "Cliente del simulador"}
                  </span>
                  <span className="shrink-0 text-[11px] text-ink-faint">{when(item.ultimo_en)}</span>
                </span>
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate text-xs text-ink-soft">
                    {item.ultimo_autor === "human_operator" ? "Tú/equipo: " : item.ultimo_autor === "agent" ? "Asistente: " : ""}
                    {item.ultimo_mensaje ?? ""}
                  </span>
                  {item.no_leidos > 0 ? (
                    <span className="shrink-0 rounded-full bg-accent px-1.5 text-[11px] font-semibold text-white">
                      {item.no_leidos}
                    </span>
                  ) : null}
                </span>
                <span className="flex flex-wrap gap-1 text-[11px]">
                  {item.estado === "closed" ? <span className="text-ink-faint">Cerrada</span> : null}
                  {item.tomada_por ? (
                    <span className="text-ink-soft">La tiene {item.tomada_por.nombre ?? "alguien"}</span>
                  ) : item.necesita_humano ? (
                    <span className="font-semibold text-danger">Necesita a una persona</span>
                  ) : null}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {more ? (
        <Button type="button" variant="ghost" onClick={onMore}>
          Cargar más
        </Button>
      ) : null}
    </section>
  );
}
