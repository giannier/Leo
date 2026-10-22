"use client";

/**
 * Asistente · Situaciones (TASK-467, 017 RF-006 and RF-009).
 *
 * The business's own rules, as cards: when it applies, what the assistant does,
 * and optional files, a hand-over, an example answer, the stages and "always".
 * A card is born as a draft: try it in the simulator, then activate it. Only an
 * active card reaches real customers. If two instructions clash, the situation
 * wins over everything but the platform's rules.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AssistantHeader } from "@/components/assistant/section-nav";
import { Alert, Button, Card, Checkbox, Field, Select, Skeleton, TextArea, cx } from "@/components/ui";
import { api, type CardDraft, type MediaAsset, type RubroView, type Situation, type SituationBody } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const STAGES = [
  ["INICIO", "Saludo"],
  ["IDENTIFICAR", "Qué busca"],
  ["PEDIDO", "Arma el pedido"],
  ["DATOS", "Datos del cliente"],
  ["COBRO", "Cobro"],
  ["VERIFICACION", "Verificación del pago"],
  ["CERRADO", "Venta cerrada"],
] as const;

const STATE = {
  borrador: { label: "Borrador", tone: "bg-warning-soft text-warning" },
  activa: { label: "Activa", tone: "bg-success-soft text-success" },
  inactiva: { label: "Inactiva", tone: "bg-surface-muted text-ink-faint" },
} as const;
const ORIGIN: Record<string, string> = {
  manual: "Escrita por ti",
  sugerida_rubro: "Sugerida por tu rubro",
  migrada: "De tus reglas anteriores",
  aprendida: "Aprendida",
};

const EMPTY: SituationBody = {
  titulo: "",
  cuando_aplica: "",
  que_hace: "",
  media_asset_ids: [],
  derivar: false,
  ejemplo_respuesta: null,
  etapas: null,
  siempre: false,
};

function SituationForm({
  initial,
  seed,
  media,
  onSaved,
  onCancel,
}: {
  initial: Situation | null;
  /** A new situation started from the simulator's "¿Respondió bien?" (Feature 018). */
  seed?: Partial<SituationBody> | null;
  media: MediaAsset[];
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<SituationBody>(
    initial
      ? {
          titulo: initial.titulo,
          cuando_aplica: initial.cuando_aplica,
          que_hace: initial.que_hace,
          media_asset_ids: initial.media_asset_ids,
          derivar: initial.derivar,
          ejemplo_respuesta: initial.ejemplo_respuesta,
          etapas: initial.etapas,
          siempre: initial.siempre,
        }
      : { ...EMPTY, ...(seed ?? {}) },
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = (patch: Partial<SituationBody>) => setForm({ ...form, ...patch });
  const stages = form.etapas ?? [];

  return (
    <form
      className="space-y-4"
      onSubmit={async (event) => {
        event.preventDefault();
        setSaving(true);
        setError(null);
        try {
          const body = { ...form, ejemplo_respuesta: form.ejemplo_respuesta?.trim() || null };
          if (initial) await api.assistant.updateSituation(initial.id, body);
          else await api.assistant.createSituation(body);
          onSaved();
        } catch (caught) {
          setError(humanMessage(caught));
        } finally {
          setSaving(false);
        }
      }}
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Field label="Título" maxLength={80} placeholder="Si piden factura" value={form.titulo} onChange={(e) => set({ titulo: e.target.value })} />
      <TextArea
        label="Cuándo aplica"
        rows={2}
        maxLength={300}
        placeholder="El cliente pide factura o boleta con RUC"
        hint="Escríbelo como lo diría el cliente: así el asistente la reconoce en el mensaje."
        value={form.cuando_aplica}
        onChange={(e) => set({ cuando_aplica: e.target.value })}
      />
      <TextArea
        label="Qué hace el asistente"
        rows={3}
        maxLength={1000}
        placeholder="Pide su RUC y razón social, y dile que la factura llega con la confirmación del pago."
        value={form.que_hace}
        onChange={(e) => set({ que_hace: e.target.value })}
      />
      <TextArea
        label="Ejemplo de respuesta (opcional)"
        rows={2}
        maxLength={600}
        value={form.ejemplo_respuesta ?? ""}
        onChange={(e) => set({ ejemplo_respuesta: e.target.value })}
      />
      {media.length ? (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-ink">Archivos que envía (opcional)</legend>
          {media.map((asset) => (
            <Checkbox
              key={asset.id}
              label={`${asset.titulo} (${asset.clave})`}
              checked={(form.media_asset_ids ?? []).includes(asset.id)}
              onChange={(e) =>
                set({
                  media_asset_ids: e.target.checked
                    ? [...(form.media_asset_ids ?? []), asset.id]
                    : (form.media_asset_ids ?? []).filter((id) => id !== asset.id),
                })
              }
            />
          ))}
        </fieldset>
      ) : null}
      <Checkbox
        label="Después, pasar la conversación a una persona"
        checked={Boolean(form.derivar)}
        onChange={(e) => set({ derivar: e.target.checked })}
      />
      <Checkbox
        label="Tenerla en cuenta siempre"
        hint="Para reglas generales. Las demás entran solo cuando el mensaje se parece a «cuándo aplica»."
        checked={Boolean(form.siempre)}
        onChange={(e) => set({ siempre: e.target.checked })}
      />
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-ink">Etapas (vacío: todas)</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {STAGES.map(([key, label]) => (
            <Checkbox
              key={key}
              label={label}
              checked={stages.includes(key)}
              onChange={(e) => {
                const next = e.target.checked ? [...stages, key] : stages.filter((s) => s !== key);
                set({ etapas: next.length ? next : null });
              }}
            />
          ))}
        </div>
      </fieldset>
      <div className="flex gap-2">
        <Button type="submit" loading={saving} disabled={!form.titulo.trim() || !form.cuando_aplica.trim() || !form.que_hace.trim()}>
          Guardar
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

function FromSuggestion({
  suggestion,
  onDone,
}: {
  suggestion: RubroView["situaciones_sugeridas"][number];
  onDone: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Card title={`Usar «${suggestion.titulo}»`} description={suggestion.que_hace}>
      <form
        className="space-y-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          setError(null);
          try {
            await api.assistant.fromSuggested(suggestion.id, values);
            onDone();
          } catch (caught) {
            setError(humanMessage(caught));
          } finally {
            setBusy(false);
          }
        }}
      >
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {Object.entries(suggestion.campos).map(([key, label]) => (
          <Field key={key} label={label} value={values[key] ?? ""} onChange={(e) => setValues({ ...values, [key]: e.target.value })} />
        ))}
        <Button type="submit" loading={busy}>
          Crear como borrador
        </Button>
      </form>
    </Card>
  );
}

function Writer({ onCard }: { onCard: (card: CardDraft) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function prepare() {
    setBusy(true);
    setError(null);
    try {
      onCard(await api.assistant.draftCard(text.trim()));
      setText("");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Escríbela con tus palabras" description="Cuéntale la regla como se la dirías a un vendedor y Leo la convierte en tarjeta y te avisa si choca con otra.">
      <div className="space-y-3" data-testid="situation-writer">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <TextArea
          label="La regla"
          rows={2}
          maxLength={600}
          placeholder="Si piden factura, que me pasen su RUC y su razón social"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <Button type="button" variant="secondary" loading={busy} disabled={text.trim().length < 8} onClick={() => void prepare()}>
          Preparar la tarjeta
        </Button>
        <p className="text-xs text-ink-faint">Usa un análisis de tu tope semanal de aprendizaje (se enciende en Aprendizaje).</p>
      </div>
    </Card>
  );
}

export default function SituationsPage() {
  const [items, setItems] = useState<Situation[] | null>(null);
  const [media, setMedia] = useState<MediaAsset[]>([]);
  const [rubro, setRubro] = useState<RubroView | null>(null);
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<Situation | "new" | null>(null);
  const [suggested, setSuggested] = useState<string | null>(null);
  const [seed, setSeed] = useState<Partial<SituationBody> | null>(null);
  const [warnings, setWarnings] = useState<CardDraft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [list, library, rubroView] = await Promise.all([
        api.assistant.situations(),
        api.assistant.media(),
        api.assistant.rubro(),
      ]);
      setItems(list.items);
      setMedia(library.items);
      setRubro(rubroView);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
    const params = new URLSearchParams(window.location.search);
    const id = params.get("sugerida");
    if (id) setSuggested(id);
    // From the simulator: "Crear una situación para este caso" (Feature 018, HU-06).
    if (params.get("nueva")) {
      const cuando = params.get("cuando")?.trim();
      setSeed({
        cuando_aplica: cuando ? `Cuando el cliente diga algo como: "${cuando}"` : "",
        ejemplo_respuesta: params.get("ejemplo")?.trim() || null,
      });
      setEditing("new");
    }
  }, [load]);

  const shown = (items ?? []).filter((s) => !filter || s.estado === filter || s.origen === filter);
  const suggestion = rubro?.situaciones_sugeridas.find((s) => s.id === suggested) ?? null;

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Situaciones"
        description="Tus propias reglas, una por tarjeta. Si algo choca con el rubro o el proceso, manda la situación."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}

      {suggestion ? (
        <FromSuggestion
          suggestion={suggestion}
          onDone={() => {
            setSuggested(null);
            window.history.replaceState(null, "", "/asistente/situaciones");
            void load();
          }}
        />
      ) : null}

      {!editing ? (
        <div className="max-w-4xl">
          <Writer
            onCard={(card) => {
              setSeed({
                titulo: card.titulo,
                cuando_aplica: card.cuando_aplica,
                que_hace: card.que_hace,
                derivar: card.derivar,
                siempre: card.siempre,
              });
              setWarnings(card);
              setEditing("new");
            }}
          />
        </div>
      ) : null}

      {editing && warnings && (warnings.choques.length > 0 || warnings.avisos.length > 0) ? (
        <Alert tone="warning" title="Revisa antes de guardar">
          <ul className="list-disc pl-5">
            {warnings.choques.map((clash, index) => (
              <li key={`c${index}`}>
                Choca con {clash.tipo === "situación" ? "tu situación" : clash.tipo}: «{clash.con}». {clash.motivo}
              </li>
            ))}
            {warnings.avisos.map((notice, index) => (
              <li key={`a${index}`}>{notice}</li>
            ))}
          </ul>
        </Alert>
      ) : null}

      {editing ? (
        <div className="max-w-4xl">
          <Card title={editing === "new" ? "Nueva situación" : "Editar situación"}>
            <SituationForm
              initial={editing === "new" ? null : editing}
              seed={editing === "new" ? seed : null}
              media={media}
              onCancel={() => {
                setEditing(null);
                setSeed(null);
                setWarnings(null);
              }}
              onSaved={() => {
                setEditing(null);
                setSeed(null);
                setWarnings(null);
                void load();
              }}
            />
          </Card>
        </div>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <Button type="button" onClick={() => setEditing("new")}>
            Nueva situación
          </Button>
          <div className="w-56">
            <Select
              label="Mostrar"
              options={[
                { value: "", label: "Todas" },
                { value: "activa", label: "Activas" },
                { value: "borrador", label: "Borradores" },
                { value: "inactiva", label: "Inactivas" },
                { value: "sugerida_rubro", label: "Sugeridas por el rubro" },
                { value: "migrada", label: "De reglas anteriores" },
              ]}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
        </div>
      )}

      {!items ? <Skeleton className="h-40 w-full" /> : null}
      {items && !items.length ? (
        <Alert tone="info" title="Aún no tienes situaciones">
          Empieza por una de las sugeridas de tu rubro, en{" "}
          <Link href="/asistente/proceso" className="underline">
            Proceso y rubro
          </Link>
          , o escribe la tuya.
        </Alert>
      ) : null}

      <ul className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {shown.map((situation) => {
          const state = STATE[situation.estado];
          return (
            <li key={situation.id} className="rounded-xl border border-line bg-surface p-4" data-testid="situation-card">
              <div className="flex items-start justify-between gap-2">
                <p className="font-semibold text-ink">{situation.titulo}</p>
                <span className={cx("shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium", state.tone)}>
                  {state.label}
                </span>
              </div>
              <p className="mt-1 text-sm text-ink-soft">
                <span className="font-medium text-ink">Cuándo:</span> {situation.cuando_aplica}
              </p>
              <p className="mt-1 line-clamp-3 text-sm text-ink-soft">
                <span className="font-medium text-ink">Qué hace:</span> {situation.que_hace}
              </p>
              <p className="mt-2 text-xs text-ink-faint">
                {ORIGIN[situation.origen]}
                {situation.siempre ? " · siempre" : ""}
                {situation.derivar ? " · deriva" : ""}
                {situation.etapas ? ` · ${situation.etapas.length} etapa(s)` : ""}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Link
                  href={`/sandbox?probar=${situation.id}`}
                  className="inline-flex min-h-11 items-center rounded-lg border border-line-strong px-3 text-sm font-semibold text-ink hover:bg-surface-muted"
                >
                  Probar en el simulador
                </Link>
                <Button
                  type="button"
                  variant={situation.estado === "activa" ? "secondary" : "primary"}
                  onClick={async () => {
                    await api.assistant.setSituation(situation.id, situation.estado !== "activa");
                    await load();
                  }}
                >
                  {situation.estado === "activa" ? "Desactivar" : "Activar"}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setEditing(situation)}>
                  Editar
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={async () => {
                    if (!window.confirm(`¿Borrar «${situation.titulo}»?`)) return;
                    await api.assistant.removeSituation(situation.id);
                    await load();
                  }}
                >
                  Borrar
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
