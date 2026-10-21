"use client";

/**
 * The questions the assistant asks to get to know a customer (feature 019, phase E): up to five, one at a
 * time and never an interrogation. They start as the ones of the business's rubro; the owner may write their
 * own. Saved, the card collapses into a list with an "Editar" button.
 */

import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/toast";
import { Alert, Button, Field, Skeleton } from "@/components/ui";
import { api } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const MAX_QUESTIONS = 5;

export function QualificationQuestions({ canEdit }: { canEdit: boolean }) {
  const toast = useToast();
  const [suggested, setSuggested] = useState<string[]>([]);
  const [own, setOwn] = useState<string[] | null | undefined>(undefined); // undefined: loading
  const [draft, setDraft] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [config, rubro] = await Promise.all([api.agentConfig.get(), api.assistant.rubro()]);
      setSuggested(rubro.preguntas_calificacion ?? []);
      setOwn(config.preguntas_calificacion ?? null);
    } catch (caught) {
      setError(humanMessage(caught));
      setOwn(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = own ?? suggested;

  async function save(next: string[]) {
    setBusy(true);
    setError(null);
    try {
      const cleaned = next.map((q) => q.trim()).filter(Boolean);
      const config = await api.agentConfig.save({ preguntas_calificacion: cleaned });
      setOwn(config.preguntas_calificacion ?? null);
      setEditing(false);
      toast.success("Guardado", "Preguntas para conocer a tu cliente");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (own === undefined) return <Skeleton className="h-24 w-full" />;

  return (
    <div className="space-y-3 rounded-lg border border-line p-3" data-testid="qualification-questions">
      <div>
        <h3 className="font-semibold text-ink">Preguntas para conocer a tu cliente</h3>
        <p className="text-xs text-ink-soft">
          El asistente las hace de a una, entre sus respuestas, y solo las que falten.
          {own === null && suggested.length ? " Son las sugeridas para tu rubro." : ""}
        </p>
      </div>
      {error ? <Alert tone="danger">{error}</Alert> : null}

      {!editing ? (
        <>
          {shown.length ? (
            <ol className="list-decimal space-y-0.5 pl-5 text-sm text-ink">
              {shown.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-ink-faint">Todavía no hay preguntas.</p>
          )}
          {canEdit ? (
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setDraft(shown.length ? [...shown] : [""]);
                setEditing(true);
              }}
            >
              Editar
            </Button>
          ) : null}
        </>
      ) : (
        <div className="space-y-3">
          {draft.map((question, index) => (
            <div key={index} className="flex items-end gap-2">
              <div className="flex-1">
                <Field
                  label={`Pregunta ${index + 1}`}
                  value={question}
                  maxLength={120}
                  onChange={(e) => setDraft(draft.map((q, i) => (i === index ? e.target.value : q)))}
                />
              </div>
              <Button
                type="button"
                variant="ghost"
                aria-label={`Quitar la pregunta ${index + 1}`}
                onClick={() => setDraft(draft.filter((_, i) => i !== index))}
              >
                Quitar
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            {draft.length < MAX_QUESTIONS ? (
              <Button type="button" variant="secondary" onClick={() => setDraft([...draft, ""])}>
                Agregar pregunta
              </Button>
            ) : null}
            {suggested.length ? (
              <Button type="button" variant="ghost" onClick={() => setDraft([...suggested])}>
                Usar las sugeridas
              </Button>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" loading={busy} onClick={() => void save(draft)}>
              Guardar
            </Button>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
