"use client";

/**
 * The end of the wizard (017, C-02 and RF-012): three to five situations the
 * rubro suggests, to activate as they are or after filling their fields. Nobody
 * starts the "own rules" layer with an empty screen.
 */

import { useEffect, useState } from "react";

import { Alert, Button, Field } from "@/components/ui";
import { api, type RubroView } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

type Suggestion = RubroView["situaciones_sugeridas"][number];

function Row({ suggestion }: { suggestion: Suggestion }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fields = Object.entries(suggestion.campos);
  const ready = fields.every(([key]) => values[key]?.trim());

  return (
    <li className="space-y-2 rounded-lg border border-line p-3" data-testid="suggested-situation">
      <p className="text-sm font-semibold text-ink">{suggestion.titulo}</p>
      <p className="text-sm text-ink-soft">{suggestion.que_hace}</p>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {done ? (
        <p className="text-sm text-success">Activada.</p>
      ) : (
        <>
          {fields.map(([key, label]) => (
            <Field key={key} label={label} value={values[key] ?? ""} onChange={(e) => setValues({ ...values, [key]: e.target.value })} />
          ))}
          <Button
            type="button"
            variant="secondary"
            loading={busy}
            disabled={!ready}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const created = await api.assistant.fromSuggested(suggestion.id, values);
                await api.assistant.setSituation(created.id, true);
                setDone(true);
              } catch (caught) {
                setError(humanMessage(caught));
              } finally {
                setBusy(false);
              }
            }}
          >
            Activar
          </Button>
        </>
      )}
    </li>
  );
}

export function SuggestedSituations() {
  const [rubro, setRubro] = useState<RubroView | null>(null);
  useEffect(() => {
    api.assistant
      .rubro()
      .then(setRubro)
      .catch(() => setRubro(null));
  }, []);
  const suggestions = rubro?.situaciones_sugeridas.slice(0, 5) ?? [];
  if (!suggestions.length) return null;
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-base font-semibold text-ink">Situaciones sugeridas para tu rubro</h3>
        <p className="text-sm text-ink-soft">
          Reglas que suelen servir a negocios como el tuyo. Activa las que apliquen; luego puedes
          editarlas en Asistente → Situaciones.
        </p>
      </div>
      <ul className="space-y-2">
        {suggestions.map((s) => (
          <Row key={s.id} suggestion={s} />
        ))}
      </ul>
    </section>
  );
}
