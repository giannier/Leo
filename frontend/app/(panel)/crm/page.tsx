"use client";

/**
 * CRM (Feature 012, TASK-1205): the business's sales board.
 *
 * Leads move by themselves with the sales process (a customer who asks for products is
 * "Nuevo", one with an order "Interesado", one who was sent the payment methods "Por
 * pagar", a confirmed order "Ganado"), and the team corrects them: drag a card on a
 * computer, or "Mover a" on any card (phones, keyboard). "Hoy" lists the leads nobody
 * has touched in days. The board refreshes with the inbox's live events.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { LeadDrawer, money } from "@/components/crm/lead-drawer";
import { PipelineConfig } from "@/components/crm/pipeline-config";
import { useInboxSocket } from "@/components/inbox/use-inbox-socket";
import { Alert, Button, Skeleton, cx } from "@/components/ui";
import {
  api,
  type CrmMetrics,
  type LeadBoard,
  type LeadCard,
  type LeadPage,
  type Pipeline,
  type TeamMember,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

type View = "tablero" | "hoy";

function sum(values: Record<string, string> | undefined): string {
  if (!values || !Object.keys(values).length) return "—";
  return Object.entries(values)
    .map(([currency, amount]) => money(amount, currency))
    .join(" · ");
}

export default function CrmPage() {
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);
  const [view, setView] = useState<View>("tablero");
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [board, setBoard] = useState<LeadBoard | null>(null);
  const [today, setToday] = useState<LeadPage | null>(null);
  const [metrics, setMetrics] = useState<CrmMetrics | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [assigned, setAssigned] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [configuring, setConfiguring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [losing, setLosing] = useState<{ lead: LeadCard; stageId: string } | null>(null);
  const [reason, setReason] = useState("");
  const dragged = useRef<LeadCard | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    try {
      const [p, b, t, m] = await Promise.all([
        api.crm.pipeline(),
        api.crm.board({ search: query || undefined, asignado: assigned || undefined }),
        api.crm.today(),
        api.crm.metrics(),
      ]);
      setPipeline(p);
      setBoard(b);
      setToday(t);
      setMetrics(m);
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [query, assigned]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api.team
      .get()
      .then((team) => setMembers(team.members.filter((m) => m.status === "active")))
      .catch(() => setMembers([]));
  }, []);

  // Every live event of the business may have moved a lead: refresh, at most every few seconds.
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  const soon = useCallback(() => {
    if (pending.current) return;
    pending.current = setTimeout(() => {
      pending.current = null;
      void load();
    }, 1500);
  }, [load]);
  useInboxSocket({ viewing: null, onEvent: soon, onResync: soon });

  const stages = pipeline?.stages ?? [];
  const lostStage = stages.find((s) => s.semantic === "perdido");

  async function move(lead: LeadCard, stageId: string, lostReason?: string) {
    if (stageId === lead.stage_id) return;
    if (stageId === lostStage?.id && !lostReason) {
      setLosing({ lead, stageId });
      setReason("");
      return;
    }
    try {
      await api.crm.move(lead.id, stageId, lostReason);
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  const tiles = useMemo(
    () =>
      metrics
        ? [
            { label: "Valor del embudo", value: sum(metrics.valor_pipeline), hide: !metrics.valor_pipeline },
            { label: "Ganados (30 días)", value: String(metrics.ganados) },
            {
              label: "Conversión",
              value: metrics.conversion === null ? "—" : `${Math.round(metrics.conversion * 100)} %`,
            },
            { label: "Perdidos (30 días)", value: String(metrics.perdidos) },
          ].filter((t) => !t.hide)
        : [],
    [metrics],
  );

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-ink">CRM</h1>
          <p className="text-sm text-ink-soft">
            Tus clientes por etapa de venta. Se mueven solos con la conversación; tú los corriges.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            onClick={async () => {
              try {
                const blob = await api.crm.exportCsv();
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = url;
                link.download = `leo-leads-${new Date().toISOString().slice(0, 10)}.csv`;
                link.click();
                URL.revokeObjectURL(url);
              } catch (caught) {
                setError(humanMessage(caught));
              }
            }}
          >
            Descargar CSV
          </Button>
          {canConfigure ? (
            <Button variant="secondary" onClick={() => setConfiguring((on) => !on)}>
              Configurar embudo
            </Button>
          ) : null}
        </div>
      </header>

      {tiles.length ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="crm-tiles">
          {tiles.map((tile) => (
            <div key={tile.label} className="rounded-xl border border-line bg-surface p-4">
              <p className="text-xs text-ink-soft">{tile.label}</p>
              <p className="mt-1 font-heading text-xl font-semibold text-ink">{tile.value}</p>
            </div>
          ))}
        </div>
      ) : null}

      {configuring && pipeline ? (
        <PipelineConfig
          pipeline={pipeline}
          onSaved={(next) => {
            setPipeline(next);
            void load();
          }}
          onClose={() => setConfiguring(false)}
        />
      ) : null}

      <div className="flex flex-wrap items-end gap-2">
        <div className="flex gap-1" role="tablist" aria-label="Vista">
          {(["tablero", "hoy"] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={view === value}
              onClick={() => setView(value)}
              className={cx(
                "min-h-11 rounded-full border px-4 text-sm font-medium",
                view === value ? "border-brand bg-brand text-brand-fg" : "border-line bg-surface text-ink-soft",
              )}
            >
              {value === "tablero" ? "Tablero" : `Hoy${today?.total ? ` (${today.total})` : ""}`}
            </button>
          ))}
        </div>
        <label className="flex min-w-48 flex-1 flex-col gap-1.5 text-sm font-medium text-ink">
          Buscar
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Nombre, teléfono o producto"
            className="min-h-11 rounded-lg border border-line-strong bg-surface px-3 text-base text-ink placeholder:text-ink-faint"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
          Asignados
          <select
            value={assigned}
            onChange={(e) => setAssigned(e.target.value)}
            className="min-h-11 cursor-pointer rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
          >
            <option value="">Todos</option>
            <option value="me">A mí</option>
            <option value="nadie">Sin asignar</option>
            {members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.full_name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? <Alert tone="danger">{error}</Alert> : null}

      {losing && lostStage ? (
        <div className="flex flex-wrap items-end gap-2 rounded-xl border border-line bg-surface p-3" role="dialog" aria-label="Motivo de pérdida">
          <label className="flex flex-1 flex-col gap-1.5 text-sm font-medium text-ink">
            ¿Por qué se perdió {losing.lead.nombre ?? "este lead"}?
            <select
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="min-h-11 cursor-pointer rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
            >
              <option value="">Elige un motivo</option>
              {pipeline?.lost_reasons
                .filter((r) => r.is_active)
                .map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.label}
                  </option>
                ))}
            </select>
          </label>
          <Button
            disabled={!reason}
            onClick={() => {
              const { lead, stageId } = losing;
              setLosing(null);
              void move(lead, stageId, reason);
            }}
          >
            Marcar perdido
          </Button>
          <Button variant="secondary" onClick={() => setLosing(null)}>
            Cancelar
          </Button>
        </div>
      ) : null}

      {!board || !pipeline ? (
        <Skeleton className="h-72 w-full" />
      ) : view === "hoy" ? (
        <TodayList page={today} onOpen={setOpen} />
      ) : (
        <div className="-mx-4 overflow-x-auto px-4 pb-2">
          <div className="flex min-w-max gap-3" data-testid="crm-board">
            {board.columns.map((column) => (
              <section
                key={column.stage.id}
                aria-label={column.stage.label}
                data-testid={`column-${column.stage.key}`}
                onDragEnter={(event) => event.preventDefault()}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  const id = event.dataTransfer.getData("text/plain");
                  const lead =
                    dragged.current ?? board.columns.flatMap((c) => c.items).find((item) => item.id === id) ?? null;
                  dragged.current = null;
                  if (lead) void move(lead, column.stage.id);
                }}
                className="flex w-72 shrink-0 flex-col rounded-xl border border-line bg-surface-muted"
              >
                <h2 className="flex items-center justify-between px-3 py-2 text-sm font-semibold text-ink">
                  {column.stage.label}
                  <span className="rounded-full bg-surface px-2 text-xs text-ink-soft">{column.total}</span>
                </h2>
                <ul className="flex flex-col gap-2 p-2">
                  {column.items.map((lead) => (
                    <li
                      key={lead.id}
                      draggable
                      onDragStart={(event) => {
                        dragged.current = lead;
                        event.dataTransfer.setData("text/plain", lead.id);
                        event.dataTransfer.effectAllowed = "move";
                      }}
                      onDragEnd={() => {
                        dragged.current = null;
                      }}
                      className="relative space-y-1.5 rounded-lg border border-line bg-surface p-3 text-sm shadow-sm"
                      data-testid="lead-card"
                    >
                      <button
                        type="button"
                        onClick={() => setOpen(lead.id)}
                        className="block w-full cursor-pointer text-left"
                      >
                        <p className="font-medium text-ink">{lead.nombre ?? lead.telefono ?? "Sin nombre"}</p>
                        {lead.product_interest ? (
                          <p className="truncate text-xs text-ink-soft">{lead.product_interest}</p>
                        ) : null}
                        <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-ink-faint">
                          {money(lead.amount_real ?? lead.amount_estimated, lead.moneda) ? (
                            <span className="font-semibold text-ink">
                              {money(lead.amount_real ?? lead.amount_estimated, lead.moneda)}
                            </span>
                          ) : null}
                          <span>{lead.days_in_stage} d en etapa</span>
                          {lead.assigned_name ? <span>· {lead.assigned_name}</span> : null}
                          {lead.origen ? <span>· {lead.origen}</span> : null}
                        </p>
                      </button>
                      <select
                        id={`move-${lead.id}`}
                        aria-label={`Mover ${lead.nombre ?? "lead"} a`}
                        value={lead.stage_id}
                        onChange={(e) => void move(lead, e.target.value)}
                        className="min-h-9 w-full cursor-pointer rounded-md border border-line bg-surface px-2 text-xs text-ink-soft"
                      >
                        {stages
                          .filter((s) => s.is_active)
                          .map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.id === lead.stage_id ? `En ${s.label}` : `Mover a ${s.label}`}
                            </option>
                          ))}
                      </select>
                    </li>
                  ))}
                  {!column.items.length ? <li className="p-3 text-center text-xs text-ink-faint">Sin leads</li> : null}
                  {column.total > column.items.length ? (
                    <li className="px-2 text-xs text-ink-faint">
                      y {column.total - column.items.length} más (usa la búsqueda o el CSV)
                    </li>
                  ) : null}
                </ul>
              </section>
            ))}
          </div>
        </div>
      )}

      {open && pipeline ? (
        <LeadDrawer
          leadId={open}
          stages={pipeline.stages}
          reasons={pipeline.lost_reasons}
          members={members}
          onClose={() => setOpen(null)}
          onChanged={() => void load()}
        />
      ) : null}
    </div>
  );
}

function TodayList({ page, onOpen }: { page: LeadPage | null; onOpen: (id: string) => void }) {
  if (!page) return <Skeleton className="h-40 w-full" />;
  if (!page.items.length) {
    return (
      <div className="rounded-xl border border-dashed border-line-strong p-8 text-center text-sm text-ink-soft">
        Nadie espera respuesta: todos tus leads activos tuvieron movimiento en los últimos 3 días.
      </div>
    );
  }
  return (
    <ul className="divide-y divide-line rounded-xl border border-line bg-surface" data-testid="crm-today">
      {page.items.map((lead) => (
        <li key={lead.id} className="flex flex-wrap items-center gap-3 p-3 text-sm">
          <div className="min-w-0 flex-1">
            <p className="font-medium text-ink">{lead.nombre ?? lead.telefono ?? "Sin nombre"}</p>
            <p className="text-xs text-ink-faint">
              Sin movimiento hace {lead.days_idle} días · {lead.product_interest ?? "sin producto"}
            </p>
          </div>
          <Button variant="secondary" onClick={() => onOpen(lead.id)}>
            Ver
          </Button>
          {lead.conversation_id ? (
            <a
              href={`/bandeja?c=${lead.conversation_id}`}
              className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 font-semibold text-accent-fg"
            >
              Escribirle
            </a>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
