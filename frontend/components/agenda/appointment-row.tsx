"use client";

/**
 * One appointment of the agenda (feature 019, phase D): who, when, what, its state, and what a person can do
 * with it: confirm, mark it done or missed, cancel, or move it to another free time.
 */

import Link from "next/link";
import { useState } from "react";

import { Alert, Button, Field, cx } from "@/components/ui";
import { api, type Appointment, type FreeSlots } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { clock, dayOf } from "./dates";

export const STATUS: Record<string, { label: string; tone: string }> = {
  agendada: { label: "Agendada", tone: "bg-surface-muted text-ink-soft" },
  confirmada: { label: "Confirmada", tone: "bg-success-soft text-success" },
  completada: { label: "Completada", tone: "bg-success-soft text-success" },
  cancelada: { label: "Cancelada", tone: "bg-surface-muted text-ink-faint" },
  no_asistio: { label: "No asistió", tone: "bg-warning-soft text-warning" },
};

export function AppointmentRow({
  appointment,
  onChanged,
}: {
  appointment: Appointment;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [moving, setMoving] = useState(false);
  const [day, setDay] = useState(dayOf(appointment.inicio_local));
  const [slots, setSlots] = useState<FreeSlots["items"] | null>(null);
  const live = appointment.status === "agendada" || appointment.status === "confirmada";
  const status = STATUS[appointment.status] ?? STATUS.agendada;

  async function act(accion: "confirmar" | "completar" | "no_asistio" | "cancelar") {
    setBusy(true);
    setError(null);
    try {
      await api.scheduling.act(appointment.id, accion);
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function loadSlots(from: string) {
    setDay(from);
    setSlots(null);
    try {
      const free = await api.scheduling.availability({
        servicio_id: appointment.servicio_id,
        desde: from,
        ignore: appointment.id,
      });
      setSlots(free.items.filter((s) => dayOf(s.inicio_local) === from));
    } catch (caught) {
      setError(humanMessage(caught));
      setSlots([]);
    }
  }

  async function moveTo(inicio: string) {
    setBusy(true);
    setError(null);
    try {
      await api.scheduling.reschedule(appointment.id, inicio);
      setMoving(false);
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      className={cx("space-y-2 p-3 text-sm", !live && "opacity-70")}
      data-testid="appointment"
    >
      <div className="flex flex-wrap items-start gap-3">
        <p className="w-24 shrink-0 font-heading text-base font-semibold text-ink">
          {clock(appointment.inicio_local)}
          <span className="block text-xs font-normal text-ink-faint">a {clock(appointment.fin_local)}</span>
        </p>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-ink">
            {appointment.cliente ?? "Sin nombre"}
            {appointment.telefono ? <span className="ml-2 text-xs text-ink-faint">{appointment.telefono}</span> : null}
          </p>
          <p className="text-ink-soft">
            {appointment.servicio} · {appointment.recurso}
          </p>
          {appointment.notas ? <p className="text-xs text-ink-faint">{appointment.notas}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={cx("rounded-full px-2 py-0.5 text-xs font-semibold", status.tone)}>{status.label}</span>
          {appointment.sin_confirmar ? (
            <span className="rounded-full bg-warning-soft px-2 py-0.5 text-xs font-semibold text-warning">
              Sin confirmar
            </span>
          ) : null}
          {appointment.created_by === "agent" ? (
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-semibold text-accent">
              Agendada por el asistente
            </span>
          ) : null}
        </div>
      </div>

      {live ? (
        <div className="flex flex-wrap gap-2">
          {appointment.status === "agendada" ? (
            <Button type="button" variant="secondary" disabled={busy} onClick={() => void act("confirmar")}>
              Confirmar
            </Button>
          ) : null}
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={() => {
              setMoving(!moving);
              if (!moving) void loadSlots(day);
            }}
          >
            Cambiar hora
          </Button>
          <Button type="button" variant="ghost" disabled={busy} onClick={() => void act("completar")}>
            Completada
          </Button>
          <Button type="button" variant="ghost" disabled={busy} onClick={() => void act("no_asistio")}>
            No asistió
          </Button>
          <Button type="button" variant="ghost" disabled={busy} onClick={() => void act("cancelar")}>
            Cancelar
          </Button>
          {appointment.conversation_id ? (
            <Link
              href={`/bandeja?c=${appointment.conversation_id}`}
              className="inline-flex min-h-11 items-center px-2 text-sm font-semibold text-accent underline"
            >
              Abrir conversación
            </Link>
          ) : null}
        </div>
      ) : null}

      {moving ? (
        <div className="space-y-2 rounded-lg border border-line p-3">
          <Field
            label="Otro día"
            type="date"
            value={day}
            onChange={(e) => e.target.value && void loadSlots(e.target.value)}
          />
          {slots === null ? <p className="text-xs text-ink-faint">Buscando horarios…</p> : null}
          {slots && slots.length === 0 ? (
            <p className="text-xs text-ink-faint">No hay horarios libres ese día.</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {(slots ?? []).map((slot) => (
              <button
                key={slot.inicio_local}
                type="button"
                disabled={busy}
                onClick={() => void moveTo(slot.inicio_local)}
                className="min-h-11 cursor-pointer rounded-lg border border-line-strong px-3 text-sm font-semibold text-ink hover:bg-surface-muted"
              >
                {clock(slot.inicio_local)}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {error ? <Alert tone="danger">{error}</Alert> : null}
    </li>
  );
}
