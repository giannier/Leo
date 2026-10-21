"use client";

/**
 * "Qué hará tu agente" (feature 019): the owner says what the agent does and the platform works out the rest
 * (the process template, the objective, and what the setup asks for). Nobody has to understand a template.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { Alert, Checkbox, cx } from "@/components/ui";
import { api, type ProcessTemplate } from "@/lib/api/client";

export interface CapabilityInfo {
  key: "atender" | "vender" | "cotizar";
  titulo: string;
  detalle: string;
  pide: string;
  /** Always on: an agent that does anything else also answers questions. */
  siempre?: boolean;
}

export const CAPABILITIES: readonly CapabilityInfo[] = [
  {
    key: "atender",
    titulo: "Atender y resolver dudas",
    detalle: "Responde sobre tu negocio, horarios, ubicación y políticas con lo que le enseñes.",
    pide: "Te pediremos tu web o documentos y tus datos de contacto.",
    siempre: true,
  },
  {
    key: "vender",
    titulo: "Vender",
    detalle: "Muestra lo que vendes con precio fijo, arma el pedido y explica cómo pagar.",
    pide: "Te pediremos lo que vendes y cómo te pagan.",
  },
  {
    key: "cotizar",
    titulo: "Cotizar",
    detalle:
      "Para servicios a medida: entiende lo que necesita el cliente, da el precio referencial si lo hay y pasa el caso a una persona con un resumen.",
    pide: "Te pediremos los servicios que se cotizan y un contacto.",
  },
] as const;

const SOON = [
  { titulo: "Agendar citas y visitas", detalle: "Reservas con calendario y recordatorios." },
  { titulo: "Captar y calificar clientes", detalle: "Preguntas de calificación y aviso a tu equipo." },
];

export function CapabilityPicker({
  value,
  onChange,
  suggested = [],
  notice = null,
  disabled = false,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  /** What agents of the business's rubro usually do. */
  suggested?: string[];
  /** What the owner should know before turning something on, from the policy of the platform. */
  notice?: string | null;
  disabled?: boolean;
}) {
  function toggle(key: string, on: boolean) {
    const next = new Set(value);
    if (on) next.add(key);
    else next.delete(key);
    next.add("atender");
    onChange(CAPABILITIES.map((c) => c.key).filter((k) => next.has(k)));
  }

  const sameAsSuggested =
    suggested.length > 0 &&
    suggested.every((s) => value.includes(s)) &&
    value.every((v) => suggested.includes(v));

  return (
    <fieldset className="space-y-3" disabled={disabled}>
      <legend className="text-sm font-medium text-ink">¿Qué hará tu agente?</legend>
      <ul className="space-y-2">
        {CAPABILITIES.map((capability) => {
          const on = value.includes(capability.key);
          return (
            <li
              key={capability.key}
              className={cx(
                "rounded-lg border p-3 transition-colors",
                on ? "border-accent bg-accent/5" : "border-line",
              )}
            >
              <Checkbox
                label={capability.titulo}
                hint={capability.detalle}
                checked={on || Boolean(capability.siempre)}
                disabled={capability.siempre}
                onChange={(e) => toggle(capability.key, e.target.checked)}
              />
              <p className="mt-1 pl-7 text-xs text-ink-faint">
                {capability.pide}
                {suggested.includes(capability.key) && !capability.siempre ? (
                  <span className="ml-2 rounded-full bg-surface-muted px-2 py-0.5 font-semibold text-ink-soft">
                    Sugerido para tu rubro
                  </span>
                ) : null}
              </p>
            </li>
          );
        })}
      </ul>

      {notice ? (
        <Alert tone="warning" title="Ten en cuenta en tu rubro">
          {notice}
        </Alert>
      ) : null}

      {suggested.length > 0 && !sameAsSuggested ? (
        <button
          type="button"
          onClick={() => onChange(CAPABILITIES.map((c) => c.key).filter((k) => suggested.includes(k) || k === "atender"))}
          className="cursor-pointer text-sm font-semibold text-accent underline"
        >
          Usar lo sugerido para mi rubro
        </button>
      ) : null}

      <div className="rounded-lg border border-dashed border-line-strong p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Próximamente</p>
        <ul className="mt-1 space-y-0.5 text-sm text-ink-soft">
          {SOON.map((item) => (
            <li key={item.titulo}>
              <span className="font-medium text-ink">{item.titulo}.</span> {item.detalle}
            </li>
          ))}
        </ul>
      </div>
    </fieldset>
  );
}

/** One line under the picker: how it will sell, so the template is visible without being a choice. */
export function HowItSells({ refreshKey }: { refreshKey: string }) {
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([api.assistant.process(), api.assistant.templates()])
      .then(([process, templates]) => {
        if (!live) return;
        const template: ProcessTemplate | undefined = templates.items.find((t) => t.key === process.template_key);
        setText(
          `${template?.nombre ?? process.template_key}${process.plantilla_automatica ? " (elegida por Leo según lo que hace tu agente)" : " (la elegiste tú)"}`,
        );
      })
      .catch(() => live && setText(null));
    return () => {
      live = false;
    };
  }, [refreshKey]);

  if (!text) return null;
  return (
    <p className="text-xs text-ink-faint">
      Cómo venderá: <strong className="text-ink-soft">{text}</strong>.{" "}
      <Link href="/asistente/proceso" className="font-semibold text-accent underline">
        Ajustar a mano
      </Link>
    </p>
  );
}
