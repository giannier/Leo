"use client";

/**
 * Step 1: who the business is.
 *
 * Everything the agent needs to introduce itself. The description field is
 * the one that matters most and the one people skip, so its prompt is the
 * spec's own words - "cuéntale a Leo tu negocio como a un empleado nuevo" -
 * which gets a useful paragraph where "Descripción" gets a blank.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Alert, Field, Select, Skeleton, TextArea } from "@/components/ui";
import {
  api,
  type BusinessProfile,
  type BusinessProfilePatch,
  type RubroOption,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { useAutosave } from "@/lib/use-autosave";
import { ReadMyWeb } from "./read-my-web";
import { SaveStatus, StepIntro, type StepProps } from "./shared";

/** The empty option: the rubros themselves come from the catalogue (`GET /tenant/rubros`). */
const NO_RUBRO = { value: "", label: "Elige tu rubro" };

const CURRENCIES = [
  { value: "PEN", label: "Soles (PEN)" },
  { value: "USD", label: "Dólares (USD)" },
] as const;

const TIMEZONES = [
  { value: "America/Lima", label: "Perú (Lima)" },
  { value: "America/Bogota", label: "Colombia (Bogotá)" },
  { value: "America/Santiago", label: "Chile (Santiago)" },
  { value: "America/Mexico_City", label: "México (Ciudad de México)" },
] as const;

type Form = {
  business_name: string;
  industry: string;
  description: string;
  website: string;
  currency: string;
  timezone: string;
};

function toForm(profile: BusinessProfile): Form {
  return {
    business_name: profile.business_name ?? "",
    industry: profile.industry ?? "",
    description: profile.description ?? "",
    website: profile.website ?? "",
    currency: profile.currency ?? "PEN",
    timezone: profile.timezone ?? "America/Lima",
  };
}

export function StepIdentity({ onSaved }: StepProps) {
  const [form, setForm] = useState<Form | null>(null);
  const [rubros, setRubros] = useState<RubroOption[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const seeded = useRef(false);

  const { state, schedule, seed } = useAutosave<Form, BusinessProfile>({
    save: (values, expected) => {
      const body: BusinessProfilePatch = {
        business_name: values.business_name.trim() || null,
        // The empty option means "not chosen yet"; "" is not a rubro, so it goes as null.
        industry: values.industry || null,
        description: values.description.trim() || null,
        website: values.website.trim() || null,
        currency: values.currency as BusinessProfilePatch["currency"],
        timezone: values.timezone as BusinessProfilePatch["timezone"],
        expected_updated_at: expected,
      };
      return api.profile.save(body);
    },
    versionOf: (profile) => profile.updated_at ?? null,
    overwroteNewer: (profile) => profile.overwrote_newer,
    onSaved,
  });

  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    Promise.all([api.profile.get(), api.profile.rubros()])
      .then(([profile, options]) => {
        setRubros(options);
        setForm(toForm(profile));
        seed(profile.updated_at ?? null);
      })
      .catch((caught) => setLoadError(humanMessage(caught)));
  }, [seed]);

  const set = useCallback(
    <K extends keyof Form>(key: K, value: string) => {
      setForm((previous) => {
        if (previous === null) return previous;
        const next = { ...previous, [key]: value };
        schedule(next);
        return next;
      });
    },
    [schedule],
  );

  const chosen = rubros.find((r) => r.key === form?.industry);

  if (loadError) {
    return <p className="text-sm text-danger">{loadError}</p>;
  }
  if (form === null) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <StepIntro>
        Con esto tu agente sabe a quién representa. Es lo primero que usa para presentarse.
      </StepIntro>

      <Field
        label="Nombre comercial"
        name="business_name"
        required
        hint="Como te conocen tus clientes, no la razón social."
        value={form.business_name}
        onChange={(e) => set("business_name", e.target.value)}
      />

      <Select
        label="Rubro"
        name="industry"
        required
        hint="Con tu rubro, Leo te sugiere una plantilla de partida que luego puedes cambiar."
        options={[NO_RUBRO, ...rubros.map((r) => ({ value: r.key, label: r.nombre }))]}
        value={form.industry}
        onChange={(e) => set("industry", e.target.value)}
      />
      {chosen?.aviso_politica && (
        <Alert tone="warning" title="Antes de activar tu agente">
          {chosen.aviso_politica}
        </Alert>
      )}

      <TextArea
        label="Cuéntale a Leo sobre tu negocio"
        name="description"
        rows={5}
        hint="Escríbelo como se lo explicarías a un empleado nuevo en su primer día. Mientras más contexto, mejor responde."
        placeholder="Somos una panadería en Miraflores. Hacemos pan artesanal y pasteles por encargo. Abrimos de 7am a 8pm..."
        value={form.description}
        onChange={(e) => set("description", e.target.value)}
      />

      <Field
        label="Tu página web"
        name="website"
        inputMode="url"
        autoCapitalize="none"
        spellCheck={false}
        hint="Opcional. Escríbela una sola vez: la damos a tus clientes y, si quieres, la leemos para que el asistente conozca tu negocio."
        placeholder="mitienda.pe"
        value={form.website}
        onChange={(e) => set("website", e.target.value)}
      />
      <ReadMyWeb website={form.website} />

      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Moneda"
          name="currency"
          options={CURRENCIES}
          value={form.currency}
          onChange={(e) => set("currency", e.target.value)}
        />
        <Select
          label="Zona horaria"
          name="timezone"
          options={TIMEZONES}
          value={form.timezone}
          onChange={(e) => set("timezone", e.target.value)}
        />
      </div>

      <SaveStatus state={state} />
    </div>
  );
}
