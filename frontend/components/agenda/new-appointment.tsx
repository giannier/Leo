"use client";

/**
 * A walk-in or a phone call (feature 019, phase D): pick the service and the day, tap a free time, write who
 * it is. The same rules and the same refusal of a double booking as the assistant.
 */

import { useEffect, useState } from "react";

import { PhoneField } from "@/components/phone-field";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Field, Select, TextArea } from "@/components/ui";
import { api, type BookableService, type FreeSlots } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { clock, dayOf } from "./dates";

export function NewAppointment({
  services,
  day,
  contact,
  onBooked,
  onClose,
}: {
  services: BookableService[];
  day: string;
  /** Who it is, when it is booked from their lead: the meeting is linked to them. */
  contact?: { id: string; nombre: string | null; telefono: string | null };
  onBooked: () => void;
  onClose: () => void;
}) {
  const toast = useToast();
  const [service, setService] = useState(services[0]?.id ?? "");
  const [date, setDate] = useState(day);
  const [slots, setSlots] = useState<FreeSlots["items"] | null>(null);
  const [chosen, setChosen] = useState("");
  const [name, setName] = useState(contact?.nombre ?? "");
  const [phone, setPhone] = useState(contact?.telefono ?? "");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!service || !date) return;
    let live = true;
    setSlots(null);
    setChosen("");
    api.scheduling
      .availability({ servicio_id: service, desde: date })
      .then((free) => live && setSlots(free.items.filter((s) => dayOf(s.inicio_local) === date)))
      .catch((caught) => {
        if (live) {
          setError(humanMessage(caught));
          setSlots([]);
        }
      });
    return () => {
      live = false;
    };
  }, [service, date]);

  async function submit() {
    if (!chosen || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.scheduling.book({
        servicio_id: service,
        inicio: chosen,
        nombre_cliente: name.trim(),
        telefono: phone || null,
        notas: notes.trim() || null,
        contact_id: contact?.id ?? null,
      });
      toast.success("Cita agendada", `${name.trim()} · ${clock(chosen)}`);
      onBooked();
      onClose();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <div className="space-y-4">
        <h2 className="font-heading text-lg font-semibold text-ink">{contact ? "Agendar reunión" : "Nueva cita"}</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Servicio"
            value={service}
            onChange={(e) => setService(e.target.value)}
            options={services.map((s) => ({ value: s.id, label: `${s.nombre} (${s.duracion_min} min)` }))}
          />
          <Field label="Día" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </div>

        <div>
          <p className="mb-1.5 text-sm font-medium text-ink">Horario libre</p>
          {slots === null ? <p className="text-sm text-ink-faint">Buscando horarios…</p> : null}
          {slots && slots.length === 0 ? (
            <p className="text-sm text-ink-faint">No hay horarios libres ese día. Prueba con otro.</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {(slots ?? []).map((slot) => (
              <button
                key={slot.inicio_local}
                type="button"
                aria-pressed={chosen === slot.inicio_local}
                onClick={() => setChosen(slot.inicio_local)}
                className={
                  chosen === slot.inicio_local
                    ? "min-h-11 cursor-pointer rounded-lg bg-accent px-3 text-sm font-semibold text-accent-fg"
                    : "min-h-11 cursor-pointer rounded-lg border border-line-strong px-3 text-sm font-semibold text-ink hover:bg-surface-muted"
                }
              >
                {clock(slot.inicio_local)}
              </button>
            ))}
          </div>
        </div>

        <Field label="Nombre del cliente" required value={name} onChange={(e) => setName(e.target.value)} />
        <PhoneField label="Teléfono (opcional)" value={phone} onChange={setPhone} />
        <TextArea
          label="Notas (opcional)"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="flex flex-wrap gap-2">
          <Button type="button" loading={busy} disabled={!chosen || !name.trim()} onClick={() => void submit()}>
            Agendar
          </Button>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
        </div>
      </div>
    </Card>
  );
}
