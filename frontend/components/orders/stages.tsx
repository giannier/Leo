/**
 * The business's work on a confirmed order (feature 016): the steps it goes through, where it is,
 * and what the next tap does.
 *
 * One place for the three screens that talk about it (the detail, the comanda and the list), so
 * they agree on the words. A delivery has one more step than a pickup: `en_camino`.
 */

import { CheckIcon } from "@/components/icons";
import { cx } from "@/components/ui";
import type { OrderStage } from "@/lib/api/client";

export type StepKey = "confirmado" | "preparando" | "listo" | "en_camino" | "completado";

export interface Step {
  key: StepKey;
  label: string;
}

/** The steps of an order, by how it is delivered. */
export function stepsFor(tipo: string | null | undefined): Step[] {
  const steps: Step[] = [
    { key: "confirmado", label: "Confirmado" },
    { key: "preparando", label: "Preparando" },
    { key: "listo", label: tipo === "recojo" ? "Listo para recoger" : "Listo" },
  ];
  if (tipo === "delivery") steps.push({ key: "en_camino", label: "En camino" });
  steps.push({ key: "completado", label: tipo === "recojo" ? "Retirado" : "Entregado" });
  return steps;
}

/** Where a sold order is, or null when it is not one (waiting for review, drafted, discarded). */
export function stepOf(estado: string, etapa: string | null | undefined): StepKey | null {
  if (estado === "completado") return "completado";
  if (estado === "en_curso") return (etapa as StepKey | null) ?? "preparando";
  if (estado === "confirmado") return "confirmado";
  return null;
}

export interface NextAction {
  label: string;
  /** Set for a move inside the work; `complete` closes the order instead. */
  stage?: OrderStage;
  complete?: true;
  /** Whether the customer is told by WhatsApp (when the business has that notice on). */
  tells: boolean;
}

/** What the one primary button of an order does next, or null when there is nothing left. */
export function nextAction(
  estado: string,
  etapa: string | null | undefined,
  tipo: string | null | undefined,
): NextAction | null {
  const step = stepOf(estado, etapa);
  switch (step) {
    case "confirmado":
      return { label: "Empezar a preparar", stage: "preparando", tells: false };
    case "preparando":
      return {
        label: tipo === "recojo" ? "Listo para recoger" : "Marcar listo",
        stage: "listo",
        tells: tipo === "recojo",
      };
    case "listo":
      return tipo === "delivery"
        ? { label: "Salió en camino", stage: "en_camino", tells: true }
        : { label: tipo === "recojo" ? "Marcar retirado" : "Marcar entregado", complete: true, tells: false };
    case "en_camino":
      return { label: "Marcar entregado", complete: true, tells: false };
    default:
      return null;
  }
}

export const STAGE_LABELS: Record<string, string> = {
  preparando: "Preparando",
  listo: "Listo",
  en_camino: "En camino",
};

/** The steps as a row: the ones behind with a check, the current one filled, the rest empty. */
export function StageStepper({
  tipo,
  estado,
  etapa,
}: {
  tipo: string | null | undefined;
  estado: string;
  etapa: string | null | undefined;
}) {
  const steps = stepsFor(tipo);
  const current = stepOf(estado, etapa);
  const at = steps.findIndex((step) => step.key === current);
  if (at < 0) return null;
  return (
    <ol aria-label="Avance del pedido" className="flex flex-wrap items-center gap-x-1 gap-y-2">
      {steps.map((step, index) => {
        const done = index < at || (index === at && step.key === "completado");
        const active = index === at && !done;
        return (
          <li key={step.key} className="flex items-center gap-1">
            <span
              aria-current={active ? "step" : undefined}
              className={cx(
                "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
                done && "bg-success-soft text-success",
                active && "bg-accent text-accent-fg",
                !done && !active && "bg-surface-muted text-ink-faint",
              )}
            >
              {done ? <CheckIcon className="size-3.5" /> : null}
              {step.label}
            </span>
            {index < steps.length - 1 ? (
              <span aria-hidden="true" className="h-px w-3 bg-line-strong" />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
