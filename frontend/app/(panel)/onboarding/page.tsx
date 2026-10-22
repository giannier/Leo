"use client";

/**
 * The six-step onboarding wizard (HU-02).
 *
 * Re-entrant by construction: there is no wizard state on the server, and
 * none in this component either. Each step reads and writes its own resource
 * through the API, and which steps are done is *computed* from the activation
 * checklist (Clarification 009), never stored. Somebody who closes the tab on
 * step four and comes back tomorrow lands exactly where they left off, because
 * "where they left off" is derived from what actually exists.
 *
 * The step lives in the URL (`?paso=3`). That is what makes the reminder email
 * able to deep-link to the unfinished bit, what makes the browser's back
 * button behave, and what lets somebody send a colleague a link to the step
 * they are stuck on.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { CheckIcon } from "@/components/icons";
import { Alert, Button, Card, Skeleton, cx } from "@/components/ui";
import { StepAgent } from "@/components/wizard/step-agent";
import { StepCatalog } from "@/components/wizard/step-catalog";
import { StepIdentity } from "@/components/wizard/step-identity";
import { StepKnowledge } from "@/components/wizard/step-knowledge";
import { StepPayments } from "@/components/wizard/step-payments";
import { StepSummary } from "@/components/wizard/step-summary";
import { api, type Checklist } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

/** Which checklist key tells us a step is finished. `knowledge` and `summary` are not
 * checklist items: knowledge is optional, and the summary is done when the agent can be tried. */
const STEPS = [
  { title: "Tu negocio", short: "Negocio", key: "business_profile_complete" },
  { title: "Tu agente", short: "Agente", key: "agent_config_complete" },
  { title: "Qué vendes", short: "Catálogo", key: "has_active_product" },
  { title: "Lo que Leo debe saber", short: "Conocimiento", key: "knowledge" },
  { title: "Cómo te pagan", short: "Pagos", key: "has_payment_method" },
  { title: "Resumen", short: "Resumen", key: "summary" },
] as const;

function Wizard() {
  const router = useRouter();
  const params = useSearchParams();
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);

  const [checklist, setChecklist] = useState<Checklist | null>(null);
  const [hasKnowledge, setHasKnowledge] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const raw = Number(params.get("paso") ?? "1");
  const current = Number.isFinite(raw) ? Math.min(Math.max(Math.trunc(raw), 1), STEPS.length) : 1;
  const index = current - 1;

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [list, overview] = await Promise.all([
        api.tenant.checklist(),
        // Only to know whether the optional knowledge step has something in it.
        api.assistant.overview().catch(() => null),
      ]);
      setChecklist(list);
      setHasKnowledge(
        overview?.secciones.find((section) => section.clave === "conocimiento")?.estado === "completa",
      );
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const goTo = useCallback(
    (step: number) => {
      const clamped = Math.min(Math.max(step, 1), STEPS.length);
      // `push`, not `replace`: the back button should walk back through the
      // steps, which is what everybody expects it to do.
      router.push(`/onboarding?paso=${clamped}`, { scroll: true });
    },
    [router],
  );

  const done = useMemo(() => {
    const byKey = new Map(checklist?.items.map((item) => [item.key, item]) ?? []);
    return STEPS.map((step) => {
      // Optional steps tick when there is something in them and are otherwise neither
      // done nor pending, so they never look like a debt.
      if (step.key === "knowledge") return hasKnowledge ? true : null;
      if (step.key === "summary") return checklist?.sandbox_ready ? true : null;
      const item = byKey.get(step.key);
      // A step that does not apply to this business - payment methods for an agent that
      // only answers questions - is neither done nor pending... unless the business filled
      // it in anyway, which is worth a tick.
      if (item && !item.required) return item.done ? true : null;
      return item?.done ?? false;
    });
  }, [checklist, hasKnowledge]);

  if (!canConfigure) {
    return (
      <Alert tone="info" title="Solo lectura">
        Tu rol puede usar el agente pero no cambiar su configuración. Pídeselo al dueño de la
        cuenta.
      </Alert>
    );
  }

  const step = STEPS[index];
  const shared = { onSaved: refresh, goTo };

  return (
    // A step-by-step form is read down one column: fields as wide as the screen are hard to follow.
    <div className="max-w-4xl space-y-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Configura tu agente</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Cada paso se guarda solo. Puedes cerrar esto y volver cuando quieras.
        </p>
      </header>

      {error ? (
        <Alert tone="danger" title="No pudimos cargar tu progreso">
          {error}{" "}
          <button
            type="button"
            onClick={() => void refresh()}
            className="cursor-pointer font-semibold text-accent underline"
          >
            Reintentar
          </button>
        </Alert>
      ) : null}

      <StepBar current={index} done={done} onPick={goTo} />

      <Card title={`${current}. ${step.title}`}>
        {checklist === null && !error ? (
          <div className="space-y-3">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <>
            {index === 0 ? <StepIdentity {...shared} /> : null}
            {index === 1 ? <StepAgent {...shared} /> : null}
            {index === 2 ? <StepCatalog {...shared} /> : null}
            {index === 3 ? <StepKnowledge {...shared} /> : null}
            {index === 4 ? <StepPayments {...shared} /> : null}
            {index === 5 ? <StepSummary checklist={checklist} {...shared} /> : null}
          </>
        )}
      </Card>

      <nav className="flex items-center justify-between gap-3" aria-label="Pasos del wizard">
        <Button variant="secondary" onClick={() => goTo(current - 1)} disabled={current === 1}>
          Anterior
        </Button>

        {current < STEPS.length ? (
          <Button onClick={() => goTo(current + 1)}>Siguiente</Button>
        ) : (
          <Link
            href="/"
            className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
          >
            Ir a mi panel
          </Link>
        )}
      </nav>
    </div>
  );
}

function StepBar({
  current,
  done,
  onPick,
}: {
  current: number;
  done: (boolean | null)[];
  onPick: (step: number) => void;
}) {
  return (
    // Horizontally scrollable on a phone rather than wrapped or shrunk: six
    // labels do not fit in 375px, and truncating them to two letters each
    // makes the bar useless as a map of where you are.
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <ol className="flex min-w-max items-center gap-1 sm:min-w-0 sm:gap-2">
        {STEPS.map((step, i) => {
          const isCurrent = i === current;
          const isDone = done[i] === true;
          return (
            <li key={step.short} className="flex items-center gap-1 sm:gap-2">
              <button
                type="button"
                onClick={() => onPick(i + 1)}
                aria-current={isCurrent ? "step" : undefined}
                className={cx(
                  "flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-2.5 text-sm transition-colors duration-150 sm:px-3",
                  isCurrent
                    ? "bg-brand font-semibold text-brand-fg"
                    : "text-ink-soft hover:bg-surface-muted",
                )}
              >
                <span
                  className={cx(
                    "flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                    isCurrent
                      ? "bg-brand-fg/20 text-brand-fg"
                      : isDone
                        ? "bg-success text-white"
                        : "border border-line-strong text-ink-faint",
                  )}
                >
                  {/* A tick rather than the number once it is done: shape, not
                   * only colour, so it reads in greyscale too. */}
                  {isDone && !isCurrent ? <CheckIcon className="size-3" /> : i + 1}
                </span>
                {step.short}
              </button>
              {i < STEPS.length - 1 ? (
                <span className="h-px w-3 shrink-0 bg-line sm:w-4" aria-hidden="true" />
              ) : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-4">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-64 w-full" />
        </div>
      }
    >
      <Wizard />
    </Suspense>
  );
}
