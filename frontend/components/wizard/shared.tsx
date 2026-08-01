"use client";

/** Pieces every wizard step needs. */

import { CheckIcon, SpinnerIcon } from "@/components/icons";
import { Alert, cx } from "@/components/ui";
import { saveLabel, type SaveState } from "@/lib/use-autosave";

export interface StepProps {
  /** Re-read the checklist, so the step bar updates as things get done. */
  onSaved: () => void;
  goTo: (step: number) => void;
}

/**
 * The autosave indicator.
 *
 * Quiet when there is nothing to say. A permanent "Guardado" badge is a badge
 * nobody reads, and then the one time it says "Error" nobody reads that
 * either - so it only appears once something has actually happened.
 */
export function SaveStatus({ state }: { state: SaveState }) {
  const label = saveLabel(state);
  if (label === null) return null;

  if (state.status === "error") {
    return (
      <Alert tone="danger" title="No pudimos guardar">
        {state.message} Lo que escribiste sigue aquí; vuelve a intentarlo.
      </Alert>
    );
  }

  if (state.status === "saved" && state.overwroteNewer) {
    return <Alert tone="warning">{label}</Alert>;
  }

  return (
    <p
      // `polite`, so a screen reader hears it after the field the person is
      // typing in rather than interrupting them mid-word.
      aria-live="polite"
      className={cx(
        "flex items-center gap-1.5 text-xs",
        state.status === "saved" ? "text-success" : "text-ink-faint",
      )}
    >
      {state.status === "saved" ? (
        <CheckIcon className="size-3.5" />
      ) : (
        <SpinnerIcon className="size-3.5" />
      )}
      {label}
    </p>
  );
}

/** A step's explanatory line, in the customer's own terms. */
export function StepIntro({ children }: { children: React.ReactNode }) {
  return <p className="mb-5 text-sm text-ink-soft">{children}</p>;
}
