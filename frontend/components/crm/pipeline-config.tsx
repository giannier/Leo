"use client";

/**
 * Configure the board (Feature 012, HU-01): rename the stages, add up to three of the business's
 * own between the fixed ones, switch some off, and edit the reasons a lead is lost. The five
 * fixed stages stay and in their order (the sales process moves leads through them).
 */

import { useState } from "react";

import { Alert, Button, Card } from "@/components/ui";
import { api, type Pipeline } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

type Row = { key: string | null; label: string; is_active: boolean; semantic: string | null };

export function PipelineConfig({
  pipeline,
  onSaved,
  onClose,
}: {
  pipeline: Pipeline;
  onSaved: (next: Pipeline) => void;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<Row[]>(
    pipeline.stages.map((s) => ({ key: s.key, label: s.label, is_active: s.is_active, semantic: s.semantic })),
  );
  const [reasons, setReasons] = useState(pipeline.lost_reasons.map((r) => ({ ...r })));
  const [newReason, setNewReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (index: number, patch: Partial<Row>) =>
    setRows((list) => list.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  function addAfter(index: number) {
    setRows((list) => [
      ...list.slice(0, index + 1),
      { key: null, label: "Nueva etapa", is_active: true, semantic: null },
      ...list.slice(index + 1),
    ]);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.crm.saveStages(rows.map(({ key, label, is_active }) => ({ key, label, is_active })));
      const next = await api.crm.saveReasons([
        ...reasons.map(({ key, label, is_active }) => ({ key, label, is_active })),
        ...(newReason.trim() ? [{ label: newReason.trim() }] : []),
      ]);
      setNewReason("");
      onSaved(next);
      onClose();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Configurar el embudo">
      <div className="space-y-5 text-sm">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="space-y-2">
          <p className="text-ink-soft">
            Las etapas con 🔒 las mueve tu asistente según la venta; renómbralas si quieres. Puedes agregar
            hasta {pipeline.max_stages - 5} propias, que mueve tu equipo.
          </p>
          <ul className="space-y-2" data-testid="stage-rows">
            {rows.map((row, index) => (
              <li key={`${row.key ?? "new"}-${index}`} className="flex flex-wrap items-center gap-2">
                <span className="w-5 text-center" aria-hidden="true">
                  {row.semantic ? "🔒" : "•"}
                </span>
                <input
                  aria-label={`Nombre de la etapa ${index + 1}`}
                  value={row.label}
                  maxLength={40}
                  onChange={(e) => set(index, { label: e.target.value })}
                  className="min-h-11 flex-1 rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
                />
                {row.semantic === "interesado" || row.semantic === "por_pagar" || !row.semantic ? (
                  <label className="flex items-center gap-1 text-xs text-ink-soft">
                    <input
                      type="checkbox"
                      checked={row.is_active}
                      onChange={(e) => set(index, { is_active: e.target.checked })}
                    />
                    Activa
                  </label>
                ) : null}
                {!row.semantic ? (
                  <Button type="button" variant="ghost" onClick={() => setRows((l) => l.filter((_, i) => i !== index))}>
                    Quitar
                  </Button>
                ) : null}
                {row.semantic !== "ganado" && row.semantic !== "perdido" && rows.length < pipeline.max_stages ? (
                  <Button type="button" variant="ghost" onClick={() => addAfter(index)}>
                    + después
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>

        <div className="space-y-2">
          <p className="font-medium text-ink">Motivos de pérdida</p>
          <ul className="space-y-2">
            {reasons.map((r, index) => (
              <li key={r.key} className="flex items-center gap-2">
                <input
                  aria-label={`Motivo ${index + 1}`}
                  value={r.label}
                  maxLength={60}
                  onChange={(e) =>
                    setReasons((list) => list.map((x, i) => (i === index ? { ...x, label: e.target.value } : x)))
                  }
                  className="min-h-11 flex-1 rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
                />
                <label className="flex items-center gap-1 text-xs text-ink-soft">
                  <input
                    type="checkbox"
                    checked={r.is_active}
                    onChange={(e) =>
                      setReasons((list) => list.map((x, i) => (i === index ? { ...x, is_active: e.target.checked } : x)))
                    }
                  />
                  Activo
                </label>
              </li>
            ))}
          </ul>
          <input
            aria-label="Nuevo motivo"
            value={newReason}
            maxLength={60}
            placeholder="Agregar un motivo, ej.: Compró en otro lado"
            onChange={(e) => setNewReason(e.target.value)}
            className="min-h-11 w-full rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
          />
        </div>

        <div className="flex gap-2">
          <Button type="button" loading={busy} onClick={() => void save()}>
            Guardar
          </Button>
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
        </div>
      </div>
    </Card>
  );
}
