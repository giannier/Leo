"use client";

/**
 * Asistente · Comportamiento (TASK-439, 017 RF-010).
 *
 * How the assistant talks: tone, pace, detail, tú or usted, emojis and the
 * greeting; the intelligence level; follow-ups and the pause after handing a
 * conversation to a person; and the messages the system sends by itself.
 * The objective and when to hand over are here too (Feature 018, HU-05), not
 * only in the setup wizard. The business's own rules are not here: they are situations.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { AssistantHeader, Saved } from "@/components/assistant/section-nav";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Checkbox, Field, Select, Skeleton, TextArea } from "@/components/ui";
import { api, type Behavior, type BehaviorBody } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const TONES = [
  { value: "cercano", label: "Cercano" },
  { value: "profesional", label: "Profesional" },
  { value: "juvenil", label: "Juvenil" },
] as const;
const PACES = [
  { value: "directo", label: "Directo: va al grano" },
  { value: "equilibrado", label: "Equilibrado: una pregunta por mensaje" },
  { value: "paso_a_paso", label: "Paso a paso: explica antes de pedir" },
] as const;
const DETAILS = [
  { value: "breve", label: "Breve (1 a 3 líneas)" },
  { value: "moderado", label: "Moderado (hasta 700 caracteres)" },
  { value: "extenso", label: "Extenso (cuando pide detalles)" },
] as const;
/** Mirrors `DEFAULT_ESCALATION_RULES` in `app/models/business.py`. */
const ESCALATIONS = [
  { value: "cliente_molesto", label: "El cliente está molesto" },
  { value: "pide_humano", label: "Pide hablar con una persona" },
  { value: "fuera_de_catalogo", label: "Pregunta por algo que no vendes" },
] as const;
/** Pilot mode: the share of customers the assistant answers; the rest wait for the team. */
const PILOT = [
  { value: "100", label: "Todos tus clientes" },
  { value: "50", label: "La mitad" },
  { value: "25", label: "Uno de cada cuatro" },
  { value: "10", label: "Uno de cada diez" },
  { value: "0", label: "Ninguno: solo responde tu equipo" },
] as const;
const LEVELS: Record<string, string> = { economico: "Económico", medio: "Medio", mejor: "El mejor" };
const MESSAGES: Record<string, string> = {
  pago_confirmado: "Cuando confirmas un pago",
  pago_rechazado: "Cuando rechazas un comprobante",
  pedido_rechazado: "Cuando rechazas un pedido",
  pedido_anulado: "Cuando anulas un pedido",
  derivacion: "Cuando la conversación pasa a una persona",
};

export default function BehaviorPage() {
  const [behavior, setBehavior] = useState<Behavior | null>(null);
  const [draft, setDraft] = useState<Behavior | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.assistant
      .behavior()
      .then((b) => {
        setBehavior(b);
        setDraft(b);
      })
      .catch((caught) => setError(humanMessage(caught)));
  }, []);

  if (!draft || !behavior) {
    return (
      <div className="space-y-5">
        <AssistantHeader title="Comportamiento" description="Cómo habla tu asistente." />
        {error ? <Alert tone="danger">{error}</Alert> : <Skeleton className="h-64 w-full" />}
      </div>
    );
  }

  const set = (patch: Partial<Behavior>) => setDraft({ ...draft, ...patch });

  async function save() {
    if (!draft || !behavior) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    const messages: Record<string, string | null> = {};
    for (const [key, text] of Object.entries(draft.mensajes_sistema)) {
      if (text !== behavior.mensajes_sistema[key]) messages[key] = text.trim() || null;
    }
    const body: BehaviorBody = {
      agent_name: draft.agent_name,
      tone: draft.tone as BehaviorBody["tone"],
      ritmo: draft.ritmo as BehaviorBody["ritmo"],
      detalle: draft.detalle as BehaviorBody["detalle"],
      tratamiento: draft.tratamiento as BehaviorBody["tratamiento"],
      emojis: draft.emojis as BehaviorBody["emojis"],
      saludo: draft.saludo ?? "",
      nivel_inteligencia: draft.nivel_inteligencia as BehaviorBody["nivel_inteligencia"],
      seguimiento: draft.seguimiento,
      derivacion_pausa_horas: draft.derivacion_pausa_horas,
      piloto_pct: draft.piloto_pct,
      max_situaciones: draft.max_situaciones,
      objective: draft.objective as BehaviorBody["objective"],
      escalation_rules: draft.escalation_rules as BehaviorBody["escalation_rules"],
      ...(Object.keys(messages).length ? { mensajes_sistema: messages } : {}),
    };
    try {
      const updated = await api.assistant.saveBehavior(body);
      setBehavior(updated);
      setDraft(updated);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
      toast.success("Guardado", "Comportamiento del asistente");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Comportamiento"
        description="Cómo habla tu asistente. Tus reglas propias van en Situaciones."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <form
        className="space-y-6"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        {/* Four independent cards: two columns on a wide screen, so the page is read across
            instead of being a long strip down the middle. */}
        <div className="grid items-start gap-6 xl:grid-cols-2">
          <Card title="Estilo">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Nombre del asistente" value={draft.agent_name} onChange={(e) => set({ agent_name: e.target.value })} />
              <Select label="Tono" options={TONES} value={draft.tone} onChange={(e) => set({ tone: e.target.value })} />
              <Select label="Ritmo" options={PACES} value={draft.ritmo} onChange={(e) => set({ ritmo: e.target.value })} />
              <Select label="Detalle" options={DETAILS} value={draft.detalle} onChange={(e) => set({ detalle: e.target.value })} />
              <Select
                label="Trato al cliente"
                options={[
                  { value: "tu", label: "De tú" },
                  { value: "usted", label: "De usted" },
                ]}
                value={draft.tratamiento}
                onChange={(e) => set({ tratamiento: e.target.value })}
              />
              <Select
                label="Emojis"
                options={[
                  { value: "no", label: "Ninguno" },
                  { value: "pocos", label: "Pocos (uno de vez en cuando)" },
                  { value: "si", label: "Con naturalidad" },
                ]}
                value={draft.emojis}
                onChange={(e) => set({ emojis: e.target.value })}
              />
            </div>
            <div className="mt-4">
              <Field
                label="Saludo (opcional)"
                maxLength={400}
                placeholder="¡Hola! Bienvenido a ..."
                hint="Lo usa en su primer mensaje."
                value={draft.saludo ?? ""}
                onChange={(e) => set({ saludo: e.target.value })}
              />
            </div>
          </Card>

          <Card title="Cuándo pasa a una persona">
            <p className="text-sm text-ink-soft">
              Si vende, cotiza o solo atiende lo eliges en{" "}
              <Link href="/asistente/capacidades" className="font-semibold text-accent underline">
                Qué hará tu agente
              </Link>
              .
            </p>
            <fieldset className="mt-4 space-y-3">
              <legend className="text-sm font-medium text-ink">Pasa la conversación a una persona cuando…</legend>
              {ESCALATIONS.map((rule) => (
                <Checkbox
                  key={rule.value}
                  label={rule.label}
                  checked={draft.escalation_rules.includes(rule.value)}
                  onChange={(e) =>
                    set({
                      escalation_rules: e.target.checked
                        ? [...draft.escalation_rules, rule.value]
                        : draft.escalation_rules.filter((r) => r !== rule.value),
                    })
                  }
                />
              ))}
            </fieldset>
          </Card>

          <Card title="Inteligencia y seguimiento">
            <div className="grid gap-4 sm:grid-cols-2">
              <Select
                label="Nivel de inteligencia"
                options={draft.niveles_permitidos.map((level) => ({ value: level, label: LEVELS[level] ?? level }))}
                value={draft.nivel_inteligencia}
                onChange={(e) => set({ nivel_inteligencia: e.target.value })}
              />
              <Field
                label="Situaciones por mensaje"
                type="number"
                min={1}
                max={10}
                value={draft.max_situaciones}
                onChange={(e) => set({ max_situaciones: Number(e.target.value) })}
                hint="Cuántas de tus situaciones puede tener en cuenta a la vez."
              />
              <Field
                label="Pausa al pasar a una persona (horas)"
                type="number"
                min={1}
                max={72}
                value={draft.derivacion_pausa_horas}
                onChange={(e) => set({ derivacion_pausa_horas: Number(e.target.value) })}
              />
              <Select
                label="A quiénes atiende el asistente"
                options={PILOT}
                value={String(draft.piloto_pct)}
                onChange={(e) => set({ piloto_pct: Number(e.target.value) })}
                hint="Para empezar con calma: el asistente atiende solo a una parte de tus clientes y tu equipo al resto. Cada cliente es siempre de un mismo lado."
              />
            </div>
            <div className="mt-4 space-y-3">
              <Checkbox
                label="Hacer seguimiento si el cliente deja de responder"
                checked={draft.seguimiento.activo}
                onChange={(e) => set({ seguimiento: { ...draft.seguimiento, activo: e.target.checked } })}
              />
              {draft.seguimiento.activo ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field
                    label="Horas de espera"
                    type="number"
                    min={1}
                    max={20}
                    value={draft.seguimiento.horas}
                    onChange={(e) => set({ seguimiento: { ...draft.seguimiento, horas: Number(e.target.value) } })}
                  />
                  <Field
                    label="Intentos como máximo"
                    type="number"
                    min={1}
                    max={3}
                    value={draft.seguimiento.max_intentos}
                    onChange={(e) =>
                      set({ seguimiento: { ...draft.seguimiento, max_intentos: Number(e.target.value) } })
                    }
                  />
                </div>
              ) : null}
            </div>
          </Card>

          <Card
            title="Mensajes del sistema"
            description="Los envía el sistema, no el modelo. Puedes usar {nombre_cliente}, {numero}, {motivo} y {siguiente_paso}. Déjalo vacío para volver al texto por defecto."
          >
            <div className="space-y-4">
              {Object.entries(draft.mensajes_sistema).map(([key, text]) => (
                <TextArea
                  key={key}
                  label={MESSAGES[key] ?? key}
                  rows={2}
                  value={text}
                  onChange={(e) => set({ mensajes_sistema: { ...draft.mensajes_sistema, [key]: e.target.value } })}
                />
              ))}
            </div>
          </Card>
        </div>

        <div className="flex items-center gap-3">
          <Button type="submit" loading={saving}>
            Guardar comportamiento
          </Button>
          <Saved show={saved} />
        </div>
      </form>
    </div>
  );
}
