"use client";

/** The activation steps and the progress bar, on Asistente and on Inicio (TASK-441). */

import Link from "next/link";

import { CheckIcon, CircleIcon } from "@/components/icons";
import { cx } from "@/components/ui";
import type { Overview } from "@/lib/api/client";

export const STEP_LABELS: Record<string, string> = {
  productos: "Carga tus productos",
  metodos_pago: "Agrega cómo te pagan",
  proceso_venta: "Elige tu proceso de venta",
  primera_situacion: "Activa tu primera situación",
  probar_simulador: "Prueba a tu asistente en el simulador",
  probar_situacion: "Prueba una situación en el simulador",
  conectar_whatsapp: "Conecta tu número de WhatsApp",
};

export function ActivationSteps({ overview }: { overview: Overview }) {
  return (
    <ol className="space-y-2">
      {overview.activacion.map((step) => {
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
              {next ? <span className="ml-auto text-xs text-accent">Siguiente</span> : null}
            </Link>
          </li>
        );
      })}
    </ol>
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

