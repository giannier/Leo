"use client";

/**
 * The structured sections of "Información del negocio" (TASK-438): every one is
 * a form with a schema behind it, never free text the model has to interpret.
 * Each saves on its own; the backend validates and the next turn sees it.
 */

import { useEffect, useState } from "react";

import { Saved } from "@/components/assistant/section-nav";
import { Alert, Button, Checkbox, Field, Select, TextArea } from "@/components/ui";
import { api, type Behavior, type BehaviorBody, type InfoSection } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

type Data = Record<string, unknown>;

/** Load one section, and save it with a status line. */
function useSection(section: InfoSection) {
  const [data, setData] = useState<Data | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.assistant
      .section(section)
      .then((response) => setData((response.data as Data | null) ?? null))
      .catch((caught) => setError(humanMessage(caught)))
      .finally(() => setLoaded(true));
  }, [section]);

  async function save(body: Data) {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const response = await api.assistant.saveSection(section, body);
      setData(response.data as Data);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return { data, loaded, error, saving, saved, save };
}

function Footer({ saving, saved, label = "Guardar" }: { saving: boolean; saved: boolean; label?: string }) {
  return (
    <div className="flex items-center gap-3">
      <Button type="submit" loading={saving}>
        {label}
      </Button>
      <Saved show={saved} />
    </div>
  );
}

/* --------------------------------------------------------------- Entregas */

interface Zone {
  nombre: string;
  costo: string;
  tiempo: string;
}

export function DeliveryForm() {
  const { data, loaded, error, saving, saved, save } = useSection("entregas");
  const [delivers, setDelivers] = useState(false);
  const [pickup, setPickup] = useState(false);
  const [zones, setZones] = useState<Zone[]>([]);
  const [free, setFree] = useState("");
  const [notes, setNotes] = useState("");

  useEffect(() => {
    if (!data) return;
    setDelivers(Boolean(data.hace_delivery));
    setPickup(Boolean(data.recojo_en_tienda));
    setZones(((data.zonas as Zone[] | undefined) ?? []).map((z) => ({ ...z, costo: String(z.costo) })));
    setFree(data.envio_gratis_desde ? String(data.envio_gratis_desde) : "");
    setNotes((data.notas as string | null) ?? "");
  }, [data]);

  if (!loaded) return null;
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        void save({
          hace_delivery: delivers,
          recojo_en_tienda: pickup,
          zonas: delivers ? zones.filter((z) => z.nombre.trim()) : [],
          envio_gratis_desde: free.trim() || null,
          notas: notes.trim() || null,
        });
      }}
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Checkbox label="Hacemos delivery" checked={delivers} onChange={(e) => setDelivers(e.target.checked)} />
      {delivers ? (
        <div className="space-y-3">
          {zones.map((zone, index) => (
            <div key={index} className="grid gap-2 rounded-lg border border-line p-3 sm:grid-cols-[2fr_1fr_1fr_auto]">
              <Field
                label="Zona"
                value={zone.nombre}
                onChange={(e) => setZones(zones.map((z, i) => (i === index ? { ...z, nombre: e.target.value } : z)))}
              />
              <Field
                label="Costo"
                inputMode="decimal"
                value={zone.costo}
                onChange={(e) => setZones(zones.map((z, i) => (i === index ? { ...z, costo: e.target.value } : z)))}
              />
              <Field
                label="Tiempo"
                placeholder="24 a 48 h"
                value={zone.tiempo}
                onChange={(e) => setZones(zones.map((z, i) => (i === index ? { ...z, tiempo: e.target.value } : z)))}
              />
              <Button
                type="button"
                variant="ghost"
                className="self-end"
                onClick={() => setZones(zones.filter((_, i) => i !== index))}
              >
                Quitar
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="secondary"
            onClick={() => setZones([...zones, { nombre: "", costo: "", tiempo: "" }])}
          >
            Agregar zona
          </Button>
          <Field
            label="Envío gratis desde (opcional)"
            inputMode="decimal"
            value={free}
            onChange={(e) => setFree(e.target.value)}
            hint="El total del pedido desde el que el envío no se cobra."
          />
        </div>
      ) : null}
      <Checkbox label="Se puede recoger en tienda" checked={pickup} onChange={(e) => setPickup(e.target.checked)} />
      <TextArea label="Notas (opcional)" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      <Footer saving={saving} saved={saved} />
    </form>
  );
}

/* ---------------------------------------------------------------- Locales */

const DAYS = [
  ["mon", "Lunes"],
  ["tue", "Martes"],
  ["wed", "Miércoles"],
  ["thu", "Jueves"],
  ["fri", "Viernes"],
  ["sat", "Sábado"],
  ["sun", "Domingo"],
] as const;

type Hours = Record<string, string[][]>;

interface Location {
  nombre: string;
  direccion: string;
  referencia: string;
  hours: Record<string, string>;
}

function hoursToText(hours: Hours | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [day] of DAYS) {
    out[day] = (hours?.[day] ?? []).map(([a, b]) => `${a}-${b}`).join(", ");
  }
  return out;
}

function textToHours(text: Record<string, string>): Hours {
  const out: Hours = {};
  for (const [day] of DAYS) {
    const ranges = (text[day] ?? "")
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => part.split("-").map((x) => x.trim()));
    if (ranges.length) out[day] = ranges;
  }
  return out;
}

export function LocationsForm() {
  const { data, loaded, error, saving, saved, save } = useSection("locales");
  const [inPerson, setInPerson] = useState(false);
  const [locations, setLocations] = useState<Location[]>([]);

  useEffect(() => {
    if (!data) return;
    setInPerson(Boolean(data.atiende_presencial));
    const rows = (data.locales as Array<Record<string, unknown>> | undefined) ?? [];
    setLocations(
      rows.map((row) => ({
        nombre: String(row.nombre ?? ""),
        direccion: String(row.direccion ?? ""),
        referencia: String(row.referencia ?? ""),
        hours: hoursToText(row.horarios as Hours | undefined),
      })),
    );
  }, [data]);

  if (!loaded) return null;
  const update = (index: number, patch: Partial<Location>) =>
    setLocations(locations.map((l, i) => (i === index ? { ...l, ...patch } : l)));

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        void save({
          atiende_presencial: inPerson,
          locales: inPerson
            ? locations
                .filter((l) => l.nombre.trim())
                .map((l) => ({
                  nombre: l.nombre,
                  direccion: l.direccion,
                  referencia: l.referencia.trim() || null,
                  horarios: textToHours(l.hours),
                }))
            : [],
        });
      }}
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Checkbox
        label="Atendemos en persona"
        checked={inPerson}
        onChange={(e) => setInPerson(e.target.checked)}
      />
      {inPerson
        ? locations.map((location, index) => (
            <fieldset key={index} className="space-y-3 rounded-lg border border-line p-3">
              <legend className="px-1 text-sm font-medium text-ink">Local {index + 1}</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Nombre" value={location.nombre} onChange={(e) => update(index, { nombre: e.target.value })} />
                <Field
                  label="Dirección"
                  value={location.direccion}
                  onChange={(e) => update(index, { direccion: e.target.value })}
                />
              </div>
              <Field
                label="Referencia (opcional)"
                value={location.referencia}
                onChange={(e) => update(index, { referencia: e.target.value })}
              />
              <div className="grid gap-2 sm:grid-cols-2">
                {DAYS.map(([day, label]) => (
                  <Field
                    key={day}
                    label={label}
                    placeholder="09:00-13:00, 15:00-19:00"
                    value={location.hours[day] ?? ""}
                    onChange={(e) => update(index, { hours: { ...location.hours, [day]: e.target.value } })}
                  />
                ))}
              </div>
              <Button type="button" variant="ghost" onClick={() => setLocations(locations.filter((_, i) => i !== index))}>
                Quitar este local
              </Button>
            </fieldset>
          ))
        : null}
      {inPerson ? (
        <Button
          type="button"
          variant="secondary"
          onClick={() =>
            setLocations([...locations, { nombre: "", direccion: "", referencia: "", hours: hoursToText(undefined) }])
          }
        >
          Agregar local
        </Button>
      ) : null}
      <Footer saving={saving} saved={saved} />
    </form>
  );
}

/* ---------------------------------------------------------- Tipos cliente */

interface CustomerType {
  nombre: string;
  como_reconocerlo: string;
  trato: string;
}

export function CustomerTypesForm() {
  const { data, loaded, error, saving, saved, save } = useSection("tipos_cliente");
  const [types, setTypes] = useState<CustomerType[]>([]);

  useEffect(() => {
    if (data) setTypes((data.tipos as CustomerType[] | undefined) ?? []);
  }, [data]);

  if (!loaded) return null;
  const update = (index: number, patch: Partial<CustomerType>) =>
    setTypes(types.map((t, i) => (i === index ? { ...t, ...patch } : t)));
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        void save({ tipos: types.filter((t) => t.nombre.trim()) });
      }}
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {types.map((type, index) => (
        <div key={index} className="space-y-3 rounded-lg border border-line p-3">
          <Field label="Nombre" placeholder="Mayorista" value={type.nombre} onChange={(e) => update(index, { nombre: e.target.value })} />
          <Field
            label="Cómo reconocerlo"
            placeholder="Pide más de 12 unidades"
            value={type.como_reconocerlo}
            onChange={(e) => update(index, { como_reconocerlo: e.target.value })}
          />
          <Field
            label="Cómo tratarlo"
            placeholder="Ofrécele el precio por mayor"
            value={type.trato}
            onChange={(e) => update(index, { trato: e.target.value })}
          />
          <Button type="button" variant="ghost" onClick={() => setTypes(types.filter((_, i) => i !== index))}>
            Quitar
          </Button>
        </div>
      ))}
      {types.length < 6 ? (
        <Button type="button" variant="secondary" onClick={() => setTypes([...types, { nombre: "", como_reconocerlo: "", trato: "" }])}>
          Agregar tipo de cliente
        </Button>
      ) : null}
      <Footer saving={saving} saved={saved} />
    </form>
  );
}

/* ------------------------------------------------------------ Devoluciones */

export function ReturnsForm() {
  const { data, loaded, error, saving, saved, save } = useSection("devoluciones");
  const [changes, setChanges] = useState(false);
  const [returns, setReturns] = useState(false);
  const [days, setDays] = useState("");
  const [conditions, setConditions] = useState("");

  useEffect(() => {
    if (!data) return;
    setChanges(Boolean(data.acepta_cambios));
    setReturns(Boolean(data.acepta_devoluciones));
    setDays(data.plazo_dias ? String(data.plazo_dias) : "");
    setConditions((data.condiciones as string | null) ?? "");
  }, [data]);

  if (!loaded) return null;
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        void save({
          acepta_cambios: changes,
          acepta_devoluciones: returns,
          plazo_dias: days ? Number(days) : null,
          condiciones: conditions.trim() || null,
        });
      }}
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Checkbox label="Aceptamos cambios" checked={changes} onChange={(e) => setChanges(e.target.checked)} />
      <Checkbox label="Aceptamos devoluciones" checked={returns} onChange={(e) => setReturns(e.target.checked)} />
      <Field label="Plazo en días (opcional)" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} />
      <TextArea label="Condiciones (opcional)" rows={2} value={conditions} onChange={(e) => setConditions(e.target.value)} />
      <Footer saving={saving} saved={saved} />
    </form>
  );
}

/* --------------------------------------------------------------- Contacto */

export function ContactForm() {
  const { data, loaded, error, saving, saved, save } = useSection("contacto");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [web, setWeb] = useState("");

  useEffect(() => {
    if (!data) return;
    setPhone((data.telefono_humano as string | null) ?? "");
    setEmail((data.correo as string | null) ?? "");
    setWeb((data.web as string | null) ?? "");
  }, [data]);

  if (!loaded) return null;
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        void save({
          telefono_humano: phone.trim() || null,
          correo: email.trim() || null,
          web: web.trim() || null,
          redes: (data?.redes as Record<string, string> | undefined) ?? {},
        });
      }}
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Field
        label="Teléfono de una persona"
        placeholder="+51987654321"
        hint="Con código de país. El asistente lo da cuando alguien pide hablar con una persona."
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
      />
      <Field label="Correo" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Web" placeholder="https://..." value={web} onChange={(e) => setWeb(e.target.value)} />
      <Footer saving={saving} saved={saved} />
    </form>
  );
}

/* --------------------------------------------------------------- Descuentos */

const POLICIES = [
  { value: "solo_promociones", label: "Solo las promociones que cargue" },
  { value: "no_ofrece", label: "No damos descuentos" },
  { value: "negociable", label: "Se pueden negociar (los decide una persona)" },
] as const;

export function DiscountPolicyForm() {
  const [behavior, setBehavior] = useState<Behavior | null>(null);
  const [policy, setPolicy] = useState("solo_promociones");
  const [max, setMax] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.assistant
      .behavior()
      .then((b) => {
        setBehavior(b);
        setPolicy(b.politica_descuentos);
        setMax(b.descuento_maximo_pct ? String(b.descuento_maximo_pct) : "");
      })
      .catch((caught) => setError(humanMessage(caught)));
  }, []);

  if (!behavior) return error ? <Alert tone="danger">{error}</Alert> : null;
  return (
    <form
      className="space-y-4"
      onSubmit={async (event) => {
        event.preventDefault();
        setSaving(true);
        setError(null);
        try {
          await api.assistant.saveBehavior({
            politica_descuentos: policy as BehaviorBody["politica_descuentos"],
            descuento_maximo_pct: policy === "negociable" && max ? max : null,
          });
          setSaved(true);
          window.setTimeout(() => setSaved(false), 2500);
        } catch (caught) {
          setError(humanMessage(caught));
        } finally {
          setSaving(false);
        }
      }}
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Select label="Política de descuentos" options={POLICIES} value={policy} onChange={(e) => setPolicy(e.target.value)} />
      {policy === "negociable" ? (
        <Field
          label="Descuento máximo (%)"
          inputMode="decimal"
          value={max}
          onChange={(e) => setMax(e.target.value)}
          hint="Lo que una persona del negocio puede dar. El asistente nunca concede descuentos: deriva la conversación."
        />
      ) : null}
      <Footer saving={saving} saved={saved} />
    </form>
  );
}
