"use client";

/**
 * Asistente · Aprendizaje (feature 017, phase 3; 019, phase H).
 *
 * The assistant proposes what it could learn from the conversations; a person decides. Nothing here changes
 * what customers read until it is approved, and every change is in the history with what it was before, so
 * it can be undone. Learning costs model calls, so it starts switched off and has a weekly ceiling.
 */

import { useCallback, useEffect, useState } from "react";

import { AssistantHeader } from "@/components/assistant/section-nav";
import { ChatImport } from "@/components/learning/chat-import";
import { ExamplesList } from "@/components/learning/examples-list";
import { ENTITY_LABELS, FILTERS } from "@/components/learning/labels";
import { SuggestionCard } from "@/components/learning/suggestion-card";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Checkbox, Field, Skeleton, cx } from "@/components/ui";
import { api, type HistoryRow, type Learning, type SuggestionList } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

function Settings({ learning, onChanged }: { learning: Learning; onChanged: (next: Learning) => void }) {
  const toast = useToast();
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);
  const [ceiling, setCeiling] = useState(String(learning.tope_semanal));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(patch: { activo?: boolean; tope_semanal?: number; compartir_con_rubro?: boolean }) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api.assistant.saveLearning(patch));
      toast.success("Guardado", "Aprendizaje");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Cómo aprende" description="El asistente analiza tus conversaciones y te propone mejoras. Tú decides cuáles entran en vigor.">
      <div className="space-y-4" data-testid="learning-settings">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <Checkbox
          label="Aprendizaje encendido"
          hint="Apagado, no analiza nada y no cuesta nada. Encendido, usa llamadas al modelo hasta el tope de la semana."
          checked={learning.activo}
          disabled={busy || !canConfigure}
          onChange={(e) => void save({ activo: e.target.checked })}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Tope de análisis por semana"
            inputMode="numeric"
            value={ceiling}
            disabled={!canConfigure}
            hint="Cada conversación que analiza gasta uno. Al llegar al tope espera al lunes."
            onChange={(e) => setCeiling(e.target.value)}
          />
          <div className="text-sm">
            <p className="font-medium text-ink">Esta semana</p>
            <p className="mt-1 text-ink-soft">
              {learning.usadas} de {learning.tope_semanal} análisis · quedan {learning.quedan}
            </p>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-muted" aria-hidden="true">
              <div
                className="h-full bg-accent"
                style={{ width: `${learning.tope_semanal ? Math.min(100, (100 * learning.usadas) / learning.tope_semanal) : 0}%` }}
              />
            </div>
          </div>
        </div>
        {canConfigure ? (
          <Button
            type="button"
            variant="secondary"
            loading={busy}
            disabled={Number(ceiling) === learning.tope_semanal || Number.isNaN(Number(ceiling))}
            onClick={() => void save({ tope_semanal: Number(ceiling) })}
          >
            Guardar el tope
          </Button>
        ) : null}
        <Checkbox
          label="Permitir que Leo proponga mis situaciones a otros negocios de mi rubro"
          hint="Solo el texto de las situaciones que apruebas, nunca tus conversaciones ni datos de tus clientes. El equipo de Leo lo revisa a mano antes de publicar nada. Apagado por defecto."
          checked={learning.compartir_con_rubro}
          disabled={busy || !canConfigure}
          onChange={(e) => void save({ compartir_con_rubro: e.target.checked })}
        />
      </div>
    </Card>
  );
}

function History() {
  const toast = useToast();
  const [rows, setRows] = useState<HistoryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows((await api.assistant.history()).items);
    } catch (caught) {
      setError(humanMessage(caught));
      setRows([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function undo(id: string) {
    setBusy(id);
    setError(null);
    try {
      await api.assistant.undoChange(id);
      toast.success("Deshecho", "Volvió a como estaba");
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Historial de cambios" description="Lo que cambió en tus situaciones, reglas y perfil, con quién y cuándo. Puedes deshacer cualquier cambio.">
      <div className="space-y-3" data-testid="learning-history">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {rows === null ? <Skeleton className="h-24 w-full" /> : null}
        {rows && rows.length === 0 ? <p className="text-sm text-ink-faint">Todavía no hay cambios.</p> : null}
        {rows && rows.length > 0 ? (
          <ul className="divide-y divide-line">
            {rows.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-sm">
                <span className="rounded-full bg-surface-muted px-2 py-0.5 text-xs font-semibold text-ink-soft">
                  {ENTITY_LABELS[row.entidad] ?? row.entidad}
                </span>
                <span className={cx("min-w-0 flex-1", row.deshecho ? "text-ink-faint line-through" : "text-ink")}>
                  {row.resumen}
                  {row.por_sugerencia ? <span className="ml-2 text-xs text-accent">aprendido</span> : null}
                </span>
                <span className="text-xs text-ink-faint">
                  {new Date(row.cuando).toLocaleString("es-PE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
                </span>
                {row.se_puede_deshacer ? (
                  <Button type="button" variant="ghost" loading={busy === row.id} onClick={() => void undo(row.id)}>
                    Deshacer
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}

export default function LearningPage() {
  const [learning, setLearning] = useState<Learning | null>(null);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("pendiente");
  const [list, setList] = useState<SuggestionList | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped when a chat finishes or examples are approved, so the examples are read again.
  const [examplesVersion, setExamplesVersion] = useState(0);
  const [batching, setBatching] = useState(false);

  const loadLearning = useCallback(async () => {
    try {
      setLearning(await api.assistant.learning());
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  const loadList = useCallback(async () => {
    try {
      setList(await api.assistant.suggestions({ estado: filter }));
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [filter]);

  useEffect(() => {
    void loadLearning();
  }, [loadLearning]);

  useEffect(() => {
    setList(null);
    void loadList();
  }, [loadList]);

  const decided = () => {
    void loadList();
    void loadLearning();
    setExamplesVersion((n) => n + 1);
  };

  const pendingExamples = list?.items.filter((row) => row.tipo === "ejemplo" && row.estado === "pendiente") ?? [];

  async function approveExamples() {
    setBatching(true);
    try {
      await api.assistant.approveBatch(pendingExamples.map((row) => row.id));
      decided();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBatching(false);
    }
  }

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Aprendizaje"
        description="El asistente propone lo que podría aprender de tus conversaciones. Nada entra en vigor sin que lo apruebes, y todo se puede deshacer."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {learning ? <Settings learning={learning} onChanged={setLearning} /> : <Skeleton className="h-48 w-full" />}
      <ChatImport enabled={Boolean(learning?.activo)} onFinished={decided} />

      <section className="space-y-3" aria-label="Sugerencias">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-heading text-lg font-semibold text-ink">Sugerencias</h2>
          <nav aria-label="Filtro" className="flex flex-wrap gap-2">
            {FILTERS.map((option) => (
              <button
                key={option.key}
                type="button"
                aria-pressed={filter === option.key}
                onClick={() => setFilter(option.key)}
                className={cx(
                  "min-h-10 cursor-pointer rounded-full border px-3.5 text-sm transition-colors",
                  filter === option.key
                    ? "border-brand bg-brand font-semibold text-brand-fg"
                    : "border-line-strong bg-surface text-ink-soft hover:bg-surface-muted",
                )}
              >
                {option.label}
                {option.key === "pendiente" && learning?.total_pendientes ? ` (${learning.total_pendientes})` : ""}
              </button>
            ))}
          </nav>
        </div>
        {list === null && !error ? <Skeleton className="h-32 w-full" /> : null}
        {list && list.items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-line-strong bg-surface px-6 py-10 text-center">
            <p className="font-semibold text-ink">
              {filter === "pendiente" ? "No hay nada por revisar" : "No hay sugerencias aquí"}
            </p>
            <p className="mx-auto mt-1 max-w-md text-sm text-ink-soft">
              {learning?.activo
                ? "A medida que el asistente atienda, aquí aparecerán las preguntas que no supo responder y lo que una persona hizo mejor."
                : "Enciende el aprendizaje arriba para que empiece a proponerte mejoras."}
            </p>
          </div>
        ) : null}
        {pendingExamples.length > 1 ? (
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface p-3 text-sm">
            <span className="text-ink-soft">Hay {pendingExamples.length} ejemplos reales por revisar.</span>
            <Button type="button" variant="secondary" loading={batching} onClick={() => void approveExamples()}>
              Aprobar los {pendingExamples.length} ejemplos
            </Button>
          </div>
        ) : null}
        {list && list.items.length > 0 ? (
          <ul className="space-y-3">
            {list.items.map((row) => (
              <SuggestionCard key={row.id} row={row} onDecided={decided} />
            ))}
          </ul>
        ) : null}
      </section>

      <ExamplesList refreshKey={examplesVersion} />

      <History />
    </div>
  );
}
