"use client";

/**
 * Asistente · Proceso y rubro (TASK-440, 017 C-01 and C-04).
 *
 * The two layers the platform writes: the **process template** (how it sells)
 * and the **rubro** (what is known about businesses like this one). A business
 * chooses the template and switches rules off; it never edits their text. New
 * versions arrive with their rules on, and the news says which.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { RubroTemplateCard } from "@/components/assistant/rubro-template";
import { AssistantHeader } from "@/components/assistant/section-nav";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Select, Skeleton, TextArea, cx } from "@/components/ui";
import {
  api,
  type CatalogKind,
  type ProcessTemplate,
  type ProcessView,
  type PromptPreview,
  type RubroView,
  type RuleView,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

const STAGES: Record<string, string> = {
  INICIO: "Saludo",
  CALIFICAR: "Calificar",
  ANUNCIO: "Llegó por un anuncio",
  IDENTIFICAR: "Qué busca",
  PEDIDO: "Arma el pedido",
  DATOS: "Datos del cliente",
  COBRO: "Cobro",
  VERIFICACION: "Verificación del pago",
  CERRADO: "Venta cerrada",
  ASESORIA: "Asesoría",
  DERIVADO: "Atiende una persona",
};

/** How each closing reads to the owner (feature 020, the prompt's «CÓMO SE CIERRA»). */
const CLOSINGS: Record<string, { title: string; detail: string }> = {
  pedido: {
    title: "Pedido",
    detail: "Lo que tiene precio fijo: lo agrega al pedido, pide los datos, confirma y envía cómo pagar.",
  },
  cotizacion: {
    title: "Cotización",
    detail: "Lo que se cotiza: junta los datos que pusiste en el producto y lo pasa a tu equipo.",
  },
  cita: { title: "Cita", detail: "Lo que se agenda: ofrece horarios libres y reserva la cita." },
  informar: {
    title: "Lo arma una persona",
    detail: "Lo explica, entiende qué necesita y lo pasa a un asesor de tu equipo.",
  },
};

/** The universal process's COBRO names the way of payment with a placeholder the prompt fills; the
 * owner reads it in words. */
function readable(text: string): string {
  return text.replaceAll("{pago_previo}", "Pago previo").replaceAll("{pago_contra_entrega}", "Pago contra entrega");
}

function Switch({
  rule,
  kind,
  onChange,
  canWrite = false,
}: {
  rule: RuleView;
  kind: CatalogKind;
  onChange: (rule: RuleView) => void;
  /** A rubro rule the business may write in its own words (feature 020, C-01). */
  canWrite?: boolean;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const on = !rule.apagada;
  const own = rule.texto_propio ?? null;

  async function rewrite(texto: string | null) {
    setBusy(true);
    setFailure(null);
    try {
      onChange(await api.assistant.rewriteRule(rule.rule_key, texto));
      setEditing(null);
      toast.success(texto ? "Regla guardada con tus palabras" : "Volvió al texto de la plantilla");
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }
  return (
    <li className="flex items-start gap-3 py-2.5" data-testid="rule">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={on ? "Apagar esta regla" : "Encender esta regla"}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            onChange(await api.assistant.switchRule(kind, rule.rule_key, on));
          } finally {
            setBusy(false);
          }
        }}
        className={cx(
          "mt-0.5 inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors",
          on ? "bg-accent" : "bg-line-strong",
          busy && "opacity-50",
        )}
      >
        <span className={cx("size-5 rounded-full bg-white shadow transition-transform", on ? "translate-x-5" : "translate-x-0.5")} />
      </button>
      <div className="min-w-0 flex-1">
        {editing !== null ? (
          <div className="space-y-2">
            <TextArea
              label="Con tus palabras"
              rows={3}
              maxLength={600}
              value={editing}
              hint={`Texto de la plantilla: ${rule.texto}`}
              onChange={(e) => setEditing(e.target.value)}
            />
            {failure ? <p className="text-sm text-danger">{failure}</p> : null}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                loading={busy}
                disabled={!editing.trim()}
                onClick={() => void rewrite(editing.trim())}
              >
                Guardar
              </Button>
              <Button type="button" variant="secondary" onClick={() => setEditing(null)}>
                Cancelar
              </Button>
            </div>
          </div>
        ) : (
          <>
            {/* A rule may hold a long call without spaces (gestionar_pedido(accion="elegir", …)): it
                breaks anywhere rather than widen the page on a phone. */}
            <p
              className={cx(
                "whitespace-pre-line text-sm [overflow-wrap:anywhere]",
                on ? "text-ink" : "text-ink-faint line-through",
              )}
            >
              {readable(own ?? rule.texto)}
            </p>
            {own ? <p className="mt-1 text-xs text-accent">Con tus palabras</p> : null}
            {canWrite ? (
              <div className="mt-1 flex flex-wrap gap-3 text-xs font-semibold">
                <button type="button" className="text-accent" onClick={() => setEditing(own ?? rule.texto)}>
                  {own ? "Editar mi texto" : "Escribir con mis palabras"}
                </button>
                {own ? (
                  <button
                    type="button"
                    className="text-ink-soft"
                    disabled={busy}
                    onClick={() => void rewrite(null)}
                  >
                    Volver al texto de la plantilla
                  </button>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </div>
    </li>
  );
}

function News({
  kind,
  news,
  onSeen,
}: {
  kind: CatalogKind;
  news: NonNullable<ProcessView["novedades"]>;
  onSeen: () => void;
}) {
  return (
    <Alert
      tone="info"
      title={`Novedades de ${kind === "proceso" ? "tu proceso de venta" : "tu rubro"} (versión ${news.version})`}
      action={
        <Button
          type="button"
          variant="secondary"
          onClick={async () => {
            await api.assistant.newsSeen(kind);
            onSeen();
          }}
        >
          Visto
        </Button>
      }
    >
      {news.reglas.length ? (
        <>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {news.reglas.map((rule) => (
              <li key={rule.rule_key}>{rule.texto}</li>
            ))}
          </ul>
          <p className="mt-1">Ya están activas; puedes apagar las que no quieras abajo.</p>
        </>
      ) : null}
      {news.mejoradas?.length ? (
        <>
          <p className="mt-2 font-medium">Reglas con un texto mejorado:</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {news.mejoradas.map((rule) => (
              <li key={rule.rule_key}>
                {rule.texto}
                {rule.texto_propio ? " (tú la escribiste con tus palabras: se queda tu texto hasta que vuelvas al de la plantilla)" : ""}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Alert>
  );
}

export default function ProcessPage() {
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);
  const [templates, setTemplates] = useState<ProcessTemplate[]>([]);
  const [process, setProcess] = useState<ProcessView | null>(null);
  const [rubro, setRubro] = useState<RubroView | null>(null);
  const [preview, setPreview] = useState<PromptPreview | null>(null);
  const [previewStage, setPreviewStage] = useState("COBRO");
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"proceso" | "rubro">("proceso");

  const load = useCallback(async () => {
    setError(null);
    try {
      const [p, r] = await Promise.all([api.assistant.process(), api.assistant.rubro()]);
      setProcess(p);
      setRubro(r);
      if (canConfigure) setTemplates((await api.assistant.templates()).items);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [canConfigure]);

  useEffect(() => {
    void load();
  }, [load]);

  const replaceRule = (rule: RuleView) => {
    if (process) {
      setProcess({
        ...process,
        etapas: process.etapas.map((stage) => ({
          ...stage,
          reglas: stage.reglas.map((r) => (r.rule_key === rule.rule_key ? rule : r)),
        })),
      });
    }
    if (rubro) {
      setRubro({ ...rubro, reglas: rubro.reglas.map((r) => (r.rule_key === rule.rule_key ? rule : r)) });
    }
    setPreview(null);
  };

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Proceso y rubro"
        description="Cómo vende tu asistente y lo que sabe de los negocios como el tuyo. Puedes apagar cualquier regla; si algo choca, mandan tus situaciones y tu información."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {!process ? <Skeleton className="h-64 w-full" /> : null}

      {process ? (
        <div role="tablist" aria-label="Capa" className="flex gap-2">
          {(["proceso", "rubro"] as const).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={cx(
                "min-h-11 rounded-lg px-4 text-sm font-semibold",
                tab === key ? "bg-ink text-surface" : "bg-surface-muted text-ink-soft",
              )}
            >
              {key === "proceso" ? "Proceso de venta" : `Rubro${rubro?.nombre ? `: ${rubro.nombre}` : ""}`}
            </button>
          ))}
        </div>
      ) : null}

      {process && tab === "proceso" ? (
        <div className="space-y-6">
          {process.novedades ? <News kind="proceso" news={process.novedades} onSeen={() => void load()} /> : null}
          {process.template_key === "universal" ? (
            <Card
              title="Cómo vende tu agente"
              description="Un solo proceso para todo tu catálogo: cada producto o servicio dice cómo se cierra. Lo cambias en el catálogo, no aquí."
            >
              {process.cierres?.length ? (
                <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {process.cierres.map((closing) => (
                    <li key={closing} className="rounded-lg border border-line p-4">
                      <p className="font-semibold text-ink">{CLOSINGS[closing]?.title ?? closing}</p>
                      <p className="mt-1 text-sm text-ink-soft">{CLOSINGS[closing]?.detail}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-ink-soft">
                  Todavía no hay productos ni servicios activos.{" "}
                  <Link href="/catalog" className="font-semibold text-accent">
                    Agrega tu catálogo
                  </Link>
                  .
                </p>
              )}
            </Card>
          ) : null}
          {canConfigure && templates.length && process.template_key !== "universal" ? (
            <Card
              title="Plantilla de proceso (avanzado)"
              description={
                process.plantilla_automatica
                  ? "Leo la elige según lo que hace tu agente. Solo cámbiala si sabes que necesitas otra."
                  : "La elegiste tú: lo que haga tu agente ya no la cambia."
              }
            >
              {!process.plantilla_automatica ? (
                <div className="mb-3">
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={async () => {
                      try {
                        setProcess(await api.assistant.automaticProcess());
                        setPreview(null);
                      } catch (caught) {
                        setError(humanMessage(caught));
                      }
                    }}
                  >
                    Volver a la automática
                  </Button>
                </div>
              ) : null}
              <div className="grid gap-3 md:grid-cols-3">
                {templates.map((template) => {
                  const current = template.key === process.template_key;
                  return (
                    <button
                      key={template.key}
                      type="button"
                      aria-pressed={current}
                      onClick={async () => {
                        if (current) return;
                        try {
                          setProcess(await api.assistant.saveProcess(template.key));
                          setPreview(null);
                        } catch (caught) {
                          setError(humanMessage(caught));
                        }
                      }}
                      className={cx(
                        "rounded-lg border p-4 text-left",
                        current ? "border-accent bg-accent/5" : "border-line hover:border-ink-faint",
                      )}
                    >
                      <p className="font-semibold text-ink">{template.nombre}</p>
                      <p className="mt-1 text-sm text-ink-soft">{template.descripcion}</p>
                      {template.sugerida_por_rubro ? (
                        <p className="mt-2 text-xs font-medium text-accent">Sugerida para tu rubro</p>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </Card>
          ) : null}
          {/* The rules by stage on the left and what the model receives on the right, side by side. */}
          <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
            <Card
              title="Etapas y sus reglas"
              description={`Versión ${process.template_version ?? "—"} de la plataforma. Apaga una regla y deja de llegar al asistente desde el siguiente mensaje.`}
            >
              <ol className="space-y-4">
                {process.etapas
                  .filter((stage) => stage.reglas.length)
                  .map((stage, index) => (
                    <li key={stage.key} className="rounded-lg border border-line p-4">
                      <p className="text-sm font-semibold text-ink">
                        {index + 1}. {STAGES[stage.key] ?? stage.key}{" "}
                        <span className="font-normal text-ink-faint">· embudo {stage.embudo.toLowerCase()}</span>
                      </p>
                      <ul className="mt-2 divide-y divide-line">
                        {stage.reglas.map((rule) => (
                          <Switch key={rule.rule_key} kind="proceso" rule={rule} onChange={replaceRule} />
                        ))}
                      </ul>
                    </li>
                  ))}
              </ol>
            </Card>
            {canConfigure ? (
              <Card title="Vista previa del prompt" description="Lo que recibe el modelo en una etapa, con datos de ejemplo.">
                <div className="flex flex-wrap items-end gap-3">
                  <div className="w-56">
                    <Select
                      label="Etapa"
                      options={Object.entries(STAGES).map(([value, label]) => ({ value, label }))}
                      value={previewStage}
                      onChange={(e) => setPreviewStage(e.target.value)}
                    />
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={async () => setPreview(await api.assistant.preview(previewStage))}
                  >
                    Ver prompt
                  </Button>
                </div>
                {preview ? (
                  <div className="mt-4">
                    <p className="text-xs text-ink-faint">≈ {preview.tokens_estimados} tokens</p>
                    <pre
                      data-testid="prompt-preview"
                      className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-muted p-3 text-xs text-ink"
                    >
                      {preview.prompt}
                    </pre>
                  </div>
                ) : null}
              </Card>
            ) : null}
          </div>
        </div>
      ) : null}

      {rubro && tab === "rubro" ? (
        <div className="space-y-6">
          {!rubro.key ? (
            <Alert tone="warning">
              Tu negocio no tiene rubro. Elígelo en la <Link href="/onboarding" className="underline">configuración</Link>.
            </Alert>
          ) : null}
          {rubro.key ? <RubroTemplateCard canConfigure={canConfigure} onChanged={() => void load()} /> : null}
          {rubro.novedades ? <News kind="rubro" news={rubro.novedades} onSeen={() => void load()} /> : null}
          {rubro.reglas.length ? (
            <Card
              title="Reglas del rubro"
              description="Lo que la plataforma sabe de los negocios como el tuyo. Puedes apagarlas o escribirlas con tus palabras."
            >
              <ul className="divide-y divide-line">
                {rubro.reglas.map((rule) => (
                  <Switch
                    key={rule.rule_key}
                    kind="rubro"
                    rule={rule}
                    onChange={replaceRule}
                    canWrite={canConfigure}
                  />
                ))}
              </ul>
            </Card>
          ) : null}
          {rubro.faqs.length || rubro.objeciones.length ? (
            <Card
              title="Preguntas y objeciones típicas"
              description="El asistente las usa como guía de cómo responder, siempre con tus datos."
            >
              <div className="grid gap-4 md:grid-cols-2">
                <ul className="space-y-2 text-sm">
                  {rubro.faqs.map((faq) => (
                    <li key={faq.pregunta}>
                      <p className="font-medium text-ink">{faq.pregunta}</p>
                      <p className="text-ink-soft">{faq.respuesta}</p>
                    </li>
                  ))}
                </ul>
                <ul className="space-y-2 text-sm">
                  {rubro.objeciones.map((o) => (
                    <li key={o.objecion}>
                      <p className="font-medium text-ink">«{o.objecion}»</p>
                      <p className="text-ink-soft">{o.manejo}</p>
                    </li>
                  ))}
                </ul>
              </div>
            </Card>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
