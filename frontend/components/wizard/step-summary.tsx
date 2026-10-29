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
import { useEffect, useState } from "react";

import { ActivationSteps, ProgressBar } from "@/components/assistant/overview";
import { RubroTemplateCard } from "@/components/assistant/rubro-template";
import { Alert, Skeleton } from "@/components/ui";
import { api, type Checklist, type Overview } from "@/lib/api/client";
import type { StepProps } from "./shared";


export function StepSummary({
  checklist,
}: StepProps & { checklist: Checklist | null }) {
  // The same list and the same percentage as the Asistente's Resumen: one progress, not two.
  const [overview, setOverview] = useState<Overview | null>(null);
  useEffect(() => {
    api.assistant
      .overview()
      .then(setOverview)
      .catch(() => setOverview(null));
  }, [checklist]);

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

      {overview ? (
        <div className="space-y-4">
          <ProgressBar value={overview.progreso} />
          <ActivationSteps overview={overview} />
        </div>
      ) : (
        <Skeleton className="h-40 w-full" />
      )}

      {/* The rubro's template (feature 020): a starting point the business applies and then owns. */}
      <RubroTemplateCard canConfigure compact onChanged={() => undefined} />

      {checklist.activation_ready ? (
        <Alert tone="success" title="Todo listo para activar">
          Puedes encender tu agente desde el panel para que empiece a atender en WhatsApp.
        </Alert>
      ) : null}
    </div>
  );
}
