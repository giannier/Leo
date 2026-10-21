"use client";

/**
 * Agenda (feature 019, phase D; spec 014): the day's appointments, or the week's.
 *
 * What the assistant books and what a person books are the same calendar. A day lists each appointment with
 * its state and what to do with it; a week is a glance at the next seven days. The agenda refreshes with the
 * inbox's live events, because the assistant books from a conversation.
 */

import Link from "next/link";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { AppointmentRow } from "@/components/agenda/appointment-row";
import { NewAppointment } from "@/components/agenda/new-appointment";
import { addDays, clock, dayLabel, dayOf, mondayOf, todayIn } from "@/components/agenda/dates";
import { useInboxSocket } from "@/components/inbox/use-inbox-socket";
import { Alert, Button, Card, Skeleton, cx } from "@/components/ui";
import { api, type Appointment, type Conflict, type Scheduling } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

type View = "dia" | "semana";

const WHY: Record<string, string> = {
  fuera_de_horario: "quedó fuera del horario",
  recurso_inactivo: "su recurso está desactivado",
  bloqueada: "hay un horario bloqueado encima",
};

export default function AgendaPage() {
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);
  const [setup, setSetup] = useState<Scheduling | null>(null);
  const [view, setView] = useState<View>("dia");
  const [day, setDay] = useState<string | null>(null);
  const [items, setItems] = useState<Appointment[] | null>(null);
  const [creating, setCreating] = useState(false);
  /** Appointments to come that stopped fitting: the hours changed, a resource went off, a block was put. */
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.scheduling
      .overview()
      .then((overview) => {
        setSetup(overview);
        setDay((current) => current ?? todayIn(overview.zona));
      })
      .catch((caught) => setError(humanMessage(caught)));
  }, []);

  const from = day ? (view === "semana" ? mondayOf(day) : day) : null;
  const to = from ? (view === "semana" ? addDays(from, 6) : from) : null;

  const load = useCallback(async () => {
    if (!from || !to) return;
    try {
      const [list, clashes] = await Promise.all([
        api.scheduling.appointments({ desde: from, hasta: to }),
        api.scheduling.conflicts().catch(() => ({ items: [] as Conflict[] })),
      ]);
      setItems(list.items);
      setConflicts(clashes.items);
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [from, to]);

  useEffect(() => {
    void load();
  }, [load]);

  // The assistant books from a conversation: refresh on the business's live events, at most every few seconds.
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  const soon = useCallback(() => {
    if (pending.current) return;
    pending.current = setTimeout(() => {
      pending.current = null;
      void load();
    }, 1500);
  }, [load]);
  useInboxSocket({ viewing: null, onEvent: soon, onResync: soon });

  const services = setup?.servicios ?? [];
  const today = setup ? todayIn(setup.zona) : null;

  if (error && !setup) return <Alert tone="danger">{error}</Alert>;
  if (!setup || !day || !from) return <Skeleton className="h-64 w-full" />;

  const byDay = new Map<string, Appointment[]>();
  for (const item of items ?? []) {
    const key = dayOf(item.inicio_local);
    byDay.set(key, [...(byDay.get(key) ?? []), item]);
  }
  const live = (items ?? []).filter((a) => a.status === "agendada" || a.status === "confirmada");

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-ink">Agenda</h1>
          <p className="text-sm text-ink-soft">
            {view === "dia" ? dayLabel(day) : `Semana del ${dayLabel(from)}`}
            {items ? ` · ${live.length} ${live.length === 1 ? "cita" : "citas"}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canConfigure ? (
            <Link
              href="/asistente/agenda"
              className="inline-flex min-h-11 items-center rounded-lg border border-line-strong px-4 text-sm font-semibold text-ink hover:bg-surface-muted"
            >
              Configurar agenda
            </Link>
          ) : null}
          {services.length ? (
            <Button type="button" onClick={() => setCreating(true)}>
              Nueva cita
            </Button>
          ) : null}
        </div>
      </header>

      {error ? <Alert tone="danger">{error}</Alert> : null}

      {conflicts.length ? (
        <section className="space-y-2 rounded-xl border border-warning bg-warning-soft p-4" data-testid="agenda-conflicts">
          <h2 className="font-heading text-base font-semibold text-ink">
            {conflicts.length === 1 ? "1 cita ya no cabe en tu agenda" : `${conflicts.length} citas ya no caben en tu agenda`}
          </h2>
          <p className="text-sm text-ink-soft">
            Cambiaste un horario o bloqueaste ese momento. No se canceló nada: cambia la hora o cancela cada una.
          </p>
          <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
            {conflicts.map((conflict) => (
              <Fragment key={conflict.id}>
                <li className="px-3 pt-2 text-xs font-semibold text-warning">
                  {dayLabel(dayOf(conflict.inicio_local))} · {WHY[conflict.motivo] ?? conflict.motivo}
                </li>
                <AppointmentRow appointment={conflict} onChanged={() => void load()} />
              </Fragment>
            ))}
          </ul>
        </section>
      ) : null}

      {services.length === 0 ? (
        <Alert tone="info" title="Aún no hay nada que se agende">
          Marca un servicio de tu catálogo como «Se puede agendar» y activa «Agendar citas» en{" "}
          <Link href="/asistente/capacidades" className="font-semibold text-accent underline">
            Qué hará tu agente
          </Link>
          .
        </Alert>
      ) : null}

      {creating ? (
        <NewAppointment services={services} day={day} onBooked={() => void load()} onClose={() => setCreating(false)} />
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="secondary" onClick={() => setDay(addDays(day, view === "semana" ? -7 : -1))}>
          Anterior
        </Button>
        <Button type="button" variant="secondary" onClick={() => today && setDay(today)}>
          Hoy
        </Button>
        <Button type="button" variant="secondary" onClick={() => setDay(addDays(day, view === "semana" ? 7 : 1))}>
          Siguiente
        </Button>
        <div className="ml-auto flex overflow-hidden rounded-lg border border-line-strong" role="group" aria-label="Vista">
          {(["dia", "semana"] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={view === option}
              onClick={() => setView(option)}
              className={cx(
                "min-h-11 cursor-pointer px-4 text-sm font-semibold",
                view === option ? "bg-accent text-accent-fg" : "bg-surface text-ink hover:bg-surface-muted",
              )}
            >
              {option === "dia" ? "Día" : "Semana"}
            </button>
          ))}
        </div>
      </div>

      {items === null ? (
        <Skeleton className="h-40 w-full" />
      ) : view === "dia" ? (
        items.length === 0 ? (
          <Card>
            <p className="text-sm text-ink-soft">No hay citas este día.</p>
          </Card>
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line bg-surface" data-testid="agenda-day">
            {items.map((item) => (
              <AppointmentRow key={item.id} appointment={item} onChanged={() => void load()} />
            ))}
          </ul>
        )
      ) : (
        <div className="grid gap-3 md:grid-cols-7" data-testid="agenda-week">
          {Array.from({ length: 7 }, (_, index) => addDays(from, index)).map((date) => (
            <section
              key={date}
              className={cx("rounded-xl border bg-surface p-2", date === today ? "border-accent" : "border-line")}
            >
              <button
                type="button"
                onClick={() => {
                  setDay(date);
                  setView("dia");
                }}
                className="mb-1 block w-full cursor-pointer text-left text-xs font-semibold uppercase tracking-wide text-ink-faint hover:text-ink"
              >
                {dayLabel(date)}
              </button>
              <ul className="space-y-1">
                {(byDay.get(date) ?? []).map((item) => (
                  <li
                    key={item.id}
                    className={cx(
                      "rounded-lg bg-surface-muted px-2 py-1 text-xs",
                      (item.status === "cancelada" || item.status === "no_asistio") && "line-through opacity-60",
                    )}
                  >
                    <span className="font-semibold text-ink">{clock(item.inicio_local)}</span>{" "}
                    <span className="text-ink-soft">{item.cliente ?? "Sin nombre"}</span>
                  </li>
                ))}
                {!(byDay.get(date) ?? []).length ? <li className="text-xs text-ink-faint">—</li> : null}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
