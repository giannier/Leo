"use client";

/**
 * Step 6: what is done, what is missing, and where to fix it.
 *
 * The deep-link on each unfinished item is the point. A checklist that says
 * "falta el método de pago" and leaves the person to find the step is a
 * checklist that gets abandoned - which is precisely the state the reminder
 * email exists to rescue, so it is worth not creating in the first place.
 */

import Link from "next/link";

import { CheckIcon, CircleIcon } from "@/components/icons";
import { Alert, Skeleton, cx } from "@/components/ui";
import type { Checklist } from "@/lib/api/client";
import type { StepProps } from "./shared";
import { SuggestedSituations } from "./suggested-situations";

/** Where each checklist item is fixed. Steps for the wizard's own items,
 * routes for the ones that live elsewhere. */
const FIX: Record<string, { label: string; step?: number; href?: string; why: string }> = {
  email_verified: {
    label: "Confirmar tu correo",
    href: "/verify-email",
    why: "Abre el enlace que te enviamos al registrarte.",
  },
  business_profile_complete: {
    label: "Los datos de tu negocio",
    step: 1,
    why: "Nombre y rubro. Es cómo se presenta tu agente.",
  },
  agent_config_complete: {
    label: "La personalidad de tu agente",
    step: 2,
    why: "Cómo habla y cuándo te pasa la conversación.",
  },
  has_active_product: {
    label: "Al menos un producto",
    step: 3,
    why: "Sin catálogo no puede responder qué vendes.",
  },
  has_payment_method: {
    label: "Cómo te pueden pagar",
    step: 5,
    why: "Sin esto no puede cerrar una venta.",
  },
  phone_verified: {
    label: "Tu teléfono de contacto",
    href: "/verify-phone",
    why: "Lo pedimos antes de conectar tu número de WhatsApp.",
  },
};

export function StepSummary({
  checklist,
  goTo,
}: StepProps & { checklist: Checklist | null }) {
  if (checklist === null) return <Skeleton className="h-40 w-full" />;

  const required = checklist.items.filter((item) => item.required);
  const pending = required.filter((item) => !item.done);

  return (
    <div className="space-y-5">
      {checklist.sandbox_ready ? (
        <Alert tone="success" title="Tu agente está listo para probar">
          Escríbele en modo prueba y mira cómo responde. Es gratis e ilimitado, ahora y después.
          <div className="mt-3">
            <Link
              href="/sandbox"
              className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
            >
              Probar mi agente
            </Link>
          </div>
        </Alert>
      ) : (
        <Alert tone="info" title={`Te faltan ${pending.length} cosas`}>
          Cuando las termines vas a poder probar tu agente antes de conectarlo a WhatsApp.
        </Alert>
      )}

      <ul className="divide-y divide-line rounded-lg border border-line">
        {checklist.items.map((item) => {
          const fix = FIX[item.key];
          if (!fix) return null;

          return (
            <li key={item.key} className="flex items-start gap-3 p-3">
              <span
                className={cx(
                  "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full",
                  item.done ? "bg-success text-white" : "text-line-strong",
                )}
              >
                {item.done ? <CheckIcon className="size-4" /> : <CircleIcon className="size-5" />}
              </span>

              <div className="min-w-0 flex-1">
                <p
                  className={cx(
                    "text-sm font-medium",
                    item.done ? "text-ink-faint line-through" : "text-ink",
                  )}
                >
                  {fix.label}
                  {!item.required ? (
                    <span className="ml-2 rounded-full bg-surface-muted px-2 py-0.5 text-xs font-normal text-ink-faint">
                      No aplica
                    </span>
                  ) : null}
                </p>
                {!item.done && item.required ? (
                  <p className="mt-0.5 text-xs text-ink-faint">{fix.why}</p>
                ) : null}
              </div>

              {!item.done && item.required ? (
                fix.step !== undefined ? (
                  <button
                    type="button"
                    onClick={() => goTo(fix.step as number)}
                    className="shrink-0 cursor-pointer self-center rounded-lg border border-line-strong px-3 py-2 text-xs font-semibold text-ink hover:bg-surface-muted"
                  >
                    Completar
                  </button>
                ) : (
                  <Link
                    href={fix.href ?? "/"}
                    className="shrink-0 self-center rounded-lg border border-line-strong px-3 py-2 text-xs font-semibold text-ink hover:bg-surface-muted"
                  >
                    Completar
                  </Link>
                )
              ) : null}
            </li>
          );
        })}
      </ul>

      <SuggestedSituations />

      {checklist.activation_ready ? (
        <Alert tone="success" title="Todo listo para activar">
          Puedes encender tu agente desde el panel para que empiece a atender en WhatsApp.
        </Alert>
      ) : null}
    </div>
  );
}
