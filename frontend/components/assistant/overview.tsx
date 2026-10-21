"use client";

/** The activation steps and the progress bar, on Asistente and on Inicio (TASK-441). */

import Link from "next/link";

import { CheckIcon, CircleIcon } from "@/components/icons";
import { cx } from "@/components/ui";
import type { Overview } from "@/lib/api/client";

export const STEP_LABELS: Record<string, string> = {
  negocio: "Completa los datos de tu negocio",
  agente: "Dale nombre y tono a tu agente",
  productos: "Carga lo que vendes",
  metodos_pago: "Agrega cómo te pagan",
  conocimiento: "Enséñale tu negocio (tu web o documentos)",
  informacion: "Cuéntale horarios, entregas y políticas",
  contacto: "Agrega un contacto para los casos que requieren una persona",
  agenda: "Define qué se agenda y tus horarios",
  primera_situacion: "Activa tu primera situación",
  probar_simulador: "Prueba a tu asistente en el simulador",
  probar_situacion: "Prueba una situación en el simulador",
  conectar_whatsapp: "Conecta tu número de WhatsApp",
};

const GROUPS: { key: string; title: string }[] = [
  { key: "probar", title: "Para probar tu agente" },
  { key: "clientes", title: "Para atender a tus clientes" },
  { key: "mejorar", title: "Para mejorarlo" },
];

/** The one list of setup steps, in the order of the story: try it, serve customers, improve it. */
export function ActivationSteps({ overview }: { overview: Overview }) {
  return (
    <div className="space-y-5">
      {GROUPS.map((group) => {
        const steps = overview.activacion.filter((step) => (step.grupo ?? "probar") === group.key);
        if (steps.length === 0) return null;
        return (
          <section key={group.key} aria-label={group.title}>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">{group.title}</h3>
            <ol className="space-y-2">
              {steps.map((step) => {
                const next = step.paso === overview.siguiente_paso;
                return (
                  <li key={step.paso}>
                    <Link
                      href={step.accion}
                      className={cx(
                        "flex min-h-11 items-center gap-3 rounded-lg border px-3 text-sm",
                        next ? "border-accent bg-accent/5 font-semibold" : "border-line",
                      )}
                    >
                      {step.hecho ? (
                        <CheckIcon className="size-5 shrink-0 text-success" aria-label="Hecho" />
                      ) : (
                        <CircleIcon className="size-5 shrink-0 text-ink-faint" aria-label="Pendiente" />
                      )}
                      <span className={step.hecho ? "text-ink-soft line-through" : "text-ink"}>
                        {STEP_LABELS[step.paso] ?? step.paso}
                      </span>
                      {!step.requerido && !step.hecho ? (
                        <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-normal text-ink-faint">
                          Recomendado
                        </span>
                      ) : null}
                      {next ? <span className="ml-auto text-xs text-accent">Siguiente</span> : null}
                    </Link>
                  </li>
                );
              })}
            </ol>
          </section>
        );
      })}
    </div>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-medium text-ink">Tu asistente está listo al</span>
        <span className="font-semibold text-ink">{value} %</span>
      </div>
      <div
        className="mt-2 h-2 overflow-hidden rounded-full bg-surface-muted"
        role="progressbar"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

