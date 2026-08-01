"use client";

/**
 * Step 2: how the agent behaves.
 *
 * The escalation rules are the important part and the easiest to skim past.
 * They are what stops the agent improvising at an angry customer, so they
 * arrive already ticked - the default is the safe configuration, and turning
 * one *off* is the deliberate act.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Checkbox, Field, Select, Skeleton, TextArea } from "@/components/ui";
import { api, type AgentConfig, type AgentConfigPatch } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { useAutosave } from "@/lib/use-autosave";
import { SaveStatus, StepIntro, type StepProps } from "./shared";

const TONES = [
  { value: "cercano", label: "Cercano - trata de tú, cálido" },
  { value: "profesional", label: "Profesional - formal y directo" },
  { value: "juvenil", label: "Juvenil - relajado, informal" },
] as const;

const OBJECTIVES = [
  { value: "ambos", label: "Atender y vender" },
  { value: "vender", label: "Vender - cerrar pedidos" },
  { value: "atender", label: "Atender - responder consultas" },
] as const;

/** Mirrors `DEFAULT_ESCALATION_RULES` in `app/models/business.py`. */
const ESCALATIONS = [
  {
    value: "cliente_molesto",
    label: "El cliente está molesto",
    hint: "Una queja mal contestada por un bot cuesta más que la venta.",
  },
  {
    value: "pide_humano",
    label: "Pide hablar con una persona",
    hint: "Insistir después de que lo piden es la queja número uno con los bots.",
  },
  {
    value: "fuera_de_catalogo",
    label: "Pregunta por algo que no vendes",
    hint: "Evita que invente productos o precios que no existen.",
  },
] as const;

type Form = {
  agent_name: string;
  tone: string;
  objective: string;
  restrictions: string;
  escalation_rules: string[];
  farewell_on_pause: string;
};

export function StepAgent({ onSaved }: StepProps) {
  const [form, setForm] = useState<Form | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const seeded = useRef(false);

  const { state, schedule, seed } = useAutosave<Form, AgentConfig>({
    save: (values, expected) => {
      const body: AgentConfigPatch = {
        agent_name: values.agent_name.trim() || "Leo",
        tone: values.tone as AgentConfigPatch["tone"],
        objective: values.objective as AgentConfigPatch["objective"],
        restrictions: values.restrictions.trim() || null,
        escalation_rules: values.escalation_rules,
        farewell_on_pause: values.farewell_on_pause.trim() || null,
        expected_updated_at: expected,
      };
      return api.agentConfig.save(body);
    },
    versionOf: (config) => config.updated_at ?? null,
    overwroteNewer: (config) => config.overwrote_newer,
    onSaved,
  });

  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    api.agentConfig
      .get()
      .then((config) => {
        setForm({
          agent_name: config.agent_name ?? "Leo",
          tone: config.tone ?? "cercano",
          objective: config.objective ?? "ambos",
          restrictions: config.restrictions ?? "",
          escalation_rules: config.escalation_rules ?? [],
          farewell_on_pause: config.farewell_on_pause ?? "",
        });
        seed(config.updated_at ?? null);
      })
      .catch((caught) => setLoadError(humanMessage(caught)));
  }, [seed]);

  const set = useCallback(
    (patch: Partial<Form>) => {
      setForm((previous) => {
        if (previous === null) return previous;
        const next = { ...previous, ...patch };
        schedule(next);
        return next;
      });
    },
    [schedule],
  );

  const toggleRule = useCallback(
    (rule: string, on: boolean) => {
      setForm((previous) => {
        if (previous === null) return previous;
        const rules = on
          ? [...new Set([...previous.escalation_rules, rule])]
          : previous.escalation_rules.filter((r) => r !== rule);
        const next = { ...previous, escalation_rules: rules };
        schedule(next);
        return next;
      });
    },
    [schedule],
  );

  if (loadError) return <p className="text-sm text-danger">{loadError}</p>;
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
      <StepIntro>Cómo habla tu agente y cuándo debe pasarle la conversación a una persona.</StepIntro>

      <Field
        label="¿Cómo se llama tu agente?"
        name="agent_name"
        hint="Es el nombre con el que se presenta a tus clientes."
        value={form.agent_name}
        onChange={(e) => set({ agent_name: e.target.value })}
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Tono"
          name="tone"
          options={TONES}
          value={form.tone}
          onChange={(e) => set({ tone: e.target.value })}
        />
        <Select
          label="Para qué lo quieres"
          name="objective"
          options={OBJECTIVES}
          value={form.objective}
          onChange={(e) => set({ objective: e.target.value })}
        />
      </div>

      <TextArea
        label="Qué NO debe hacer"
        name="restrictions"
        rows={3}
        hint="Opcional, pero muy útil. Escríbelo en tus palabras."
        placeholder="No ofrecer descuentos. No prometer entregas el mismo día. No dar precios al por mayor sin consultar."
        value={form.restrictions}
        onChange={(e) => set({ restrictions: e.target.value })}
      />

      <fieldset className="rounded-lg border border-line p-4">
        <legend className="px-1 text-sm font-medium text-ink">
          ¿Cuándo debe llamarte a ti?
        </legend>
        <p className="mb-3 text-xs text-ink-faint">
          Cuando pase esto, el agente deja de responder y te avisa.
        </p>
        <div className="space-y-3">
          {ESCALATIONS.map((rule) => (
            <Checkbox
              key={rule.value}
              label={rule.label}
              hint={rule.hint}
              checked={form.escalation_rules.includes(rule.value)}
              onChange={(e) => toggleRule(rule.value, e.target.checked)}
            />
          ))}
        </div>
      </fieldset>

      <TextArea
        label="Mensaje de despedida"
        name="farewell_on_pause"
        rows={2}
        hint="Lo que dice al cerrar una conversación si tu prueba se acaba a mitad de camino. Si lo dejas vacío usamos uno neutral."
        placeholder="Gracias por escribirnos. Volvemos a atenderte muy pronto."
        value={form.farewell_on_pause}
        onChange={(e) => set({ farewell_on_pause: e.target.value })}
      />

      <SaveStatus state={state} />
    </div>
  );
}
