"use client";

/**
 * Asistente · Agenda (feature 019, phase D; spec 014): what can be booked, who takes it and when, the rules
 * and the reminders.
 *
 * What is bookable is chosen in the catalogue (a service "se puede agendar"). Hours are written here, once per
 * resource (a person, a chair, a room); a resource without hours of its own follows the business's. The
 * reminders are off until the owner turns them on, and need a template approved by WhatsApp, because they go
 * out days ahead, when the customer's 24-hour window has closed.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AssistantHeader } from "@/components/assistant/section-nav";
import { Hours, HoursEditor, OFFICE_HOURS, describeHours } from "@/components/agenda/hours-editor";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Checkbox, Field, Select, Skeleton } from "@/components/ui";
import {
  api,
  type MessageTemplate,
  type Scheduling,
  type SchedulingResource,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

/* --------------------------------------------------------------- resources */

function ResourceCard({
  resource,
  setup,
  onChanged,
}: {
  resource: SchedulingResource | null;
  setup: Scheduling;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(resource === null);
  const [name, setName] = useState(resource?.nombre ?? "Atención");
  const [own, setOwn] = useState(resource ? resource.horario !== null : !Object.keys(setup.horario_negocio).length);
  const [hours, setHours] = useState<Hours>((resource?.horario as Hours | null) ?? OFFICE_HOURS);
  const [only, setOnly] = useState<string[]>(resource?.product_ids ?? []);
  const [active, setActive] = useState(resource?.is_active ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const business = describeHours(setup.horario_negocio as Hours);

  async function save() {
    setBusy(true);
    setError(null);
    const body = { nombre: name.trim(), horario: own ? hours : null, product_ids: only, is_active: active };
    try {
      if (resource) await api.scheduling.saveResource(resource.id, body);
      else await api.scheduling.createResource(body);
      toast.success("Guardado", name.trim());
      setEditing(false);
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      await api.scheduling.deleteResource(resource.id);
      toast.success("Eliminado", resource.nombre);
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (!editing && resource) {
    return (
      <li className="flex flex-wrap items-center gap-3 rounded-lg border border-line p-3 text-sm">
        <div className="min-w-0 flex-1">
          <p className="font-medium text-ink">
            {resource.nombre}
            {!resource.is_active ? <span className="ml-2 text-xs text-ink-faint">(desactivado)</span> : null}
          </p>
          <p className="text-xs text-ink-soft">
            {resource.horario ? describeHours(resource.horario as Hours) || "Sin horario" : business ? `Horario del negocio: ${business}` : "Sin horario: no se puede agendar"}
          </p>
          {resource.product_ids.length ? (
            <p className="text-xs text-ink-faint">
              Atiende:{" "}
              {setup.servicios
                .filter((s) => resource.product_ids.includes(s.id))
                .map((s) => s.nombre)
                .join(", ")}
            </p>
          ) : null}
        </div>
        <Button type="button" variant="secondary" onClick={() => setEditing(true)}>
          Editar
        </Button>
      </li>
    );
  }

  return (
    <li className="space-y-4 rounded-lg border border-accent p-3 text-sm">
      <Field label="Nombre" value={name} onChange={(e) => setName(e.target.value)} hint="Una persona, un consultorio, una silla." />
      <Select
        label="Horario"
        value={own ? "propio" : "negocio"}
        onChange={(e) => setOwn(e.target.value === "propio")}
        options={[
          { value: "propio", label: "Horario propio (lo escribo aquí)" },
          ...(business ? [{ value: "negocio", label: `El del negocio (${business})` }] : []),
        ]}
      />
      {own ? <HoursEditor value={hours} onChange={setHours} /> : null}
      {setup.servicios.length > 1 ? (
        <fieldset className="space-y-1">
          <legend className="font-medium text-ink">Qué atiende</legend>
          <Checkbox
            label="Todos los servicios"
            checked={only.length === 0}
            onChange={(e) => e.target.checked && setOnly([])}
          />
          {setup.servicios.map((service) => (
            <Checkbox
              key={service.id}
              label={service.nombre}
              checked={only.includes(service.id)}
              onChange={(e) =>
                setOnly(e.target.checked ? [...only, service.id] : only.filter((id) => id !== service.id))
              }
            />
          ))}
        </fieldset>
      ) : null}
      <Checkbox label="Está activo" hint="Un recurso desactivado no recibe citas nuevas." checked={active} onChange={(e) => setActive(e.target.checked)} />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        <Button type="button" loading={busy} disabled={!name.trim()} onClick={() => void save()}>
          Guardar
        </Button>
        {resource ? (
          <>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
            <Button type="button" variant="ghost" disabled={busy} onClick={() => void remove()}>
              Eliminar
            </Button>
          </>
        ) : null}
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------- rules */

function Rules({ setup, onChanged }: { setup: Scheduling; onChanged: () => void }) {
  const toast = useToast();
  const [min, setMin] = useState(String(setup.ajustes.anticipacion_min_horas));
  const [max, setMax] = useState(String(setup.ajustes.anticipacion_max_dias));
  const [held, setHeld] = useState(String(setup.ajustes.max_citas_activas));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.scheduling.saveSettings({
        anticipacion_min_horas: Number(min),
        anticipacion_max_dias: Number(max),
        max_citas_activas: Number(held),
      });
      toast.success("Guardado", "Reglas de la agenda");
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Anticipación mínima (horas)" inputMode="numeric" value={min} onChange={(e) => setMin(e.target.value)} hint="Nadie agenda para dentro de 20 minutos." />
        <Field label="Hasta cuántos días antes" inputMode="numeric" value={max} onChange={(e) => setMax(e.target.value)} />
        <Field label="Citas pendientes por persona" inputMode="numeric" value={held} onChange={(e) => setHeld(e.target.value)} />
      </div>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Button type="button" loading={busy} onClick={() => void save()}>
        Guardar reglas
      </Button>
    </div>
  );
}

/* --------------------------------------------------------------- reminders */

function Reminders({
  setup,
  templates,
  onChanged,
}: {
  setup: Scheduling;
  templates: MessageTemplate[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const a = setup.ajustes;
  const [h24, setH24] = useState(a.recordatorio_24h);
  const [h2, setH2] = useState(a.recordatorio_2h);
  const [template, setTemplate] = useState(a.plantilla_recordatorio_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const approved = templates.filter((t) => t.status === "approved" && t.category === "utility");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.scheduling.saveSettings({
        recordatorio_24h: h24,
        recordatorio_2h: h2,
        plantilla_recordatorio_id: template || null,
      });
      toast.success("Guardado", "Recordatorios");
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">
        Un recordatorio baja las faltas. Cada uno es un mensaje de WhatsApp que se cobra, por eso empiezan apagados.
      </p>
      <Checkbox label="Recordar 24 horas antes" checked={h24} onChange={(e) => setH24(e.target.checked)} />
      <Checkbox label="Recordar 2 horas antes" checked={h2} onChange={(e) => setH2(e.target.checked)} />
      <Select
        label="Plantilla aprobada"
        value={template}
        onChange={(e) => setTemplate(e.target.value)}
        hint="Va con la plantilla porque el recordatorio sale días antes, cuando la ventana de 24 horas del cliente ya cerró."
        options={[
          { value: "", label: approved.length ? "Elige una plantilla" : "No tienes plantillas aprobadas" },
          ...approved.map((t) => ({ value: t.id, label: t.label })),
        ]}
      />
      {!approved.length ? (
        <Alert tone="info" title="Primero crea la plantilla">
          En{" "}
          <Link href="/campanas" className="font-semibold text-accent underline">
            Campañas
          </Link>{" "}
          clona «Recordatorio de cita» de la biblioteca y envíala a aprobar. Cuando WhatsApp la apruebe, vuelve aquí.
        </Alert>
      ) : null}
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Button type="button" loading={busy} onClick={() => void save()}>
        Guardar recordatorios
      </Button>
    </div>
  );
}

/* ------------------------------------------------------------------ blocks */

function Blocks({ setup, onChanged }: { setup: Scheduling; onChanged: () => void }) {
  const toast = useToast();
  const [resource, setResource] = useState(setup.recursos[0]?.id ?? "");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const names = Object.fromEntries(setup.recursos.map((r) => [r.id, r.nombre]));

  async function add() {
    setBusy(true);
    setError(null);
    try {
      await api.scheduling.createBlock({ resource_id: resource, desde: from, hasta: to, motivo: reason.trim() || null });
      toast.success("Horario bloqueado", reason.trim() || "Sin motivo");
      setFrom("");
      setTo("");
      setReason("");
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    try {
      await api.scheduling.deleteBlock(id);
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">Un almuerzo, un feriado o el mantenimiento: nadie podrá agendar en ese horario.</p>
      {setup.bloqueos.length ? (
        <ul className="space-y-1 text-sm">
          {setup.bloqueos.map((block) => (
            <li key={block.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-surface-muted px-3 py-2">
              <span className="font-medium text-ink">{names[block.resource_id] ?? "—"}</span>
              <span className="text-ink-soft">
                {block.desde.replace("T", " ")} → {block.hasta.replace("T", " ")}
              </span>
              {block.motivo ? <span className="text-ink-faint">· {block.motivo}</span> : null}
              <button
                type="button"
                onClick={() => void remove(block.id)}
                className="ml-auto cursor-pointer text-sm font-semibold text-danger underline"
              >
                Quitar
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {setup.recursos.length ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="De"
            value={resource}
            onChange={(e) => setResource(e.target.value)}
            options={setup.recursos.map((r) => ({ value: r.id, label: r.nombre }))}
          />
          <Field label="Motivo (opcional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Field label="Desde" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
          <Field label="Hasta" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
      ) : null}
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Button type="button" loading={busy} disabled={!resource || !from || !to} onClick={() => void add()}>
        Bloquear horario
      </Button>
    </div>
  );
}

/* -------------------------------------------------------------------- page */

export default function AgendaSetupPage() {
  const [setup, setSetup] = useState<Scheduling | null>(null);
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      const [overview, list] = await Promise.all([api.scheduling.overview(), api.templates.list()]);
      setSetup(overview);
      setTemplates(list);
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const changed = () => {
    setAdding(false);
    void load();
  };

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Agenda"
        description="Qué se agenda, quién lo atiende y cuándo. El asistente ofrece solo horarios libres y nunca da la misma hora a dos clientes."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {!setup ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <>
          <Card>
            <div className="space-y-3">
              <h2 className="font-heading text-lg font-semibold text-ink">Qué se agenda</h2>
              {setup.servicios.length ? (
                <ul className="space-y-1 text-sm">
                  {setup.servicios.map((service) => (
                    <li key={service.id} className="text-ink">
                      {service.nombre}{" "}
                      <span className="text-ink-faint">
                        · {service.duracion_min} min{service.buffer_min ? ` + ${service.buffer_min} de pausa` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Alert tone="warning" title="Todavía no hay servicios que se agenden">
                  Abre un servicio en el{" "}
                  <Link href="/catalog" className="font-semibold text-accent underline">
                    catálogo
                  </Link>{" "}
                  y marca «Se puede agendar» con su duración.
                </Alert>
              )}
            </div>
          </Card>

          <Card>
            <div className="space-y-3">
              <h2 className="font-heading text-lg font-semibold text-ink">Quién atiende y cuándo</h2>
              {setup.recursos.length === 0 && !Object.keys(setup.horario_negocio).length ? (
                <Alert tone="warning" title="Falta tu horario">
                  Sin horario no hay nada que ofrecer. Escríbelo aquí abajo.
                </Alert>
              ) : null}
              <ul className="space-y-2">
                {setup.recursos.map((resource) => (
                  <ResourceCard key={resource.id} resource={resource} setup={setup} onChanged={changed} />
                ))}
                {adding || setup.recursos.length === 0 ? (
                  <ResourceCard resource={null} setup={setup} onChanged={changed} />
                ) : null}
              </ul>
              {setup.recursos.length > 0 && !adding ? (
                <Button type="button" variant="secondary" onClick={() => setAdding(true)}>
                  Agregar otro (persona, silla o consultorio)
                </Button>
              ) : null}
            </div>
          </Card>

          <Card>
            <div className="space-y-3">
              <h2 className="font-heading text-lg font-semibold text-ink">Reglas</h2>
              <Rules setup={setup} onChanged={() => void load()} />
            </div>
          </Card>

          <Card>
            <div className="space-y-3">
              <h2 className="font-heading text-lg font-semibold text-ink">Recordatorios</h2>
              <Reminders setup={setup} templates={templates} onChanged={() => void load()} />
            </div>
          </Card>

          <Card>
            <div className="space-y-3">
              <h2 className="font-heading text-lg font-semibold text-ink">Horarios bloqueados</h2>
              <Blocks setup={setup} onChanged={() => void load()} />
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
