"use client";

/**
 * "¿Recomendarías Leo?" (Feature 013, C-107): once per cycle to owners and admins. Closing it
 * settles the cycle too, so it does not come back until the next one.
 */

import { useEffect, useState } from "react";

import { Button, cx } from "@/components/ui";
import { api } from "@/lib/api/client";

export function NpsCard() {
  const [open, setOpen] = useState(false);
  const [score, setScore] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    api.analytics
      .nps()
      .then((state) => setOpen(state.preguntar))
      .catch(() => setOpen(false));
  }, []);

  if (done) {
    return (
      <p role="status" className="rounded-xl border border-line bg-surface p-4 text-sm text-ink">
        ¡Gracias! Lo leemos todo.
      </p>
    );
  }
  if (!open) return null;

  async function answer(value: number | null) {
    try {
      await api.analytics.answerNps({ score: value, comment: comment.trim() || null });
    } finally {
      setOpen(false);
      if (value !== null) setDone(true);
    }
  }

  return (
    <section aria-label="Tu opinión sobre Leo" className="space-y-3 rounded-xl border border-line bg-surface p-4">
      <p className="font-semibold text-ink">¿Qué tan probable es que recomiendes Leo a otro negocio?</p>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Del 0 al 10">
        {Array.from({ length: 11 }, (_, n) => (
          <button
            key={n}
            type="button"
            aria-pressed={score === n}
            onClick={() => setScore(n)}
            className={cx(
              "size-10 cursor-pointer rounded-lg border text-sm font-semibold",
              score === n ? "border-brand bg-brand text-brand-fg" : "border-line bg-surface text-ink hover:bg-surface-muted",
            )}
          >
            {n}
          </button>
        ))}
      </div>
      <div className="flex justify-between text-xs text-ink-faint">
        <span>Nada probable</span>
        <span>Muy probable</span>
      </div>
      {score !== null ? (
        <textarea
          aria-label="¿Por qué?"
          value={comment}
          maxLength={1000}
          onChange={(e) => setComment(e.target.value)}
          placeholder="¿Por qué? (opcional)"
          className="min-h-20 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-base text-ink"
        />
      ) : null}
      <div className="flex gap-2">
        <Button type="button" disabled={score === null} onClick={() => void answer(score)}>
          Enviar
        </Button>
        <Button type="button" variant="ghost" onClick={() => void answer(null)}>
          Ahora no
        </Button>
      </div>
    </section>
  );
}
