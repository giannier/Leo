"use client";

/**
 * Plataforma · Catálogo de plantillas y rubros (017, C-04, TASK-466).
 *
 * A change to a template or a rubro reaches every business that runs it, so it
 * goes through a draft: edit, run the evals, publish only when they passed on
 * the current content. A published version never changes.
 */

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, Field, Skeleton, TextArea, cx } from "@/components/ui";
import {
  api,
  type CatalogContent,
  type CatalogKind,
  type CatalogList,
  type CatalogVersion,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

type Entry = CatalogList["items"][number];

function contentOf(version: CatalogVersion): CatalogContent {
  return {
    reglas: version.reglas.map((r) => ({ rule_key: r.rule_key, etapa: r.etapa ?? null, texto: r.texto })),
    faqs: version.faqs,
    objeciones: version.objeciones,
    situaciones_sugeridas: version.situaciones_sugeridas,
    notas: version.notas ?? null,
  };
}

function Editor({
  version,
  onChanged,
}: {
  version: CatalogVersion;
  onChanged: (next: CatalogVersion) => void;
}) {
  const kind = version.kind as CatalogKind;
  const draft = version.estado === "borrador";
  const [content, setContent] = useState<CatalogContent>(contentOf(version));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => setContent(contentOf(version)), [version]);

  async function act(name: string, run: () => Promise<CatalogVersion | void>) {
    setBusy(name);
    setError(null);
    try {
      const next = await run();
      if (next) onChanged(next);
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  const setRule = (index: number, texto: string) =>
    setContent({ ...content, reglas: content.reglas.map((r, i) => (i === index ? { ...r, texto } : r)) });
  const evals = version.evals as
    | { ok: boolean; corrido_en: string; casos: { nombre: string; ok: boolean; detalle: string }[] }
    | null;

  return (
    <Card
      title={`${kind === "proceso" ? "Plantilla" : "Rubro"} ${version.key} · versión ${version.version}`}
      description={`Estado: ${version.estado}${version.publicada_en ? ` · publicada ${new Date(version.publicada_en).toLocaleString()}` : ""}`}
    >
      <div className="space-y-4">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {evals ? (
          <Alert
            tone={evals.ok && version.evals_vigentes ? "success" : evals.ok ? "warning" : "danger"}
            title={
              evals.ok
                ? version.evals_vigentes
                  ? "Evals en verde sobre este contenido"
                  : "Los evals pasaron, pero el contenido cambió después: córrelos de nuevo"
                : "Los evals no pasaron"
            }
          >
            <ul className="mt-1 space-y-0.5">
              {evals.casos.map((c) => (
                <li key={c.nombre} className={c.ok ? "text-ink-soft" : "text-danger"}>
                  {c.ok ? "✓" : "✗"} {c.nombre}
                  {!c.ok && c.detalle ? `: ${c.detalle}` : ""}
                </li>
              ))}
            </ul>
          </Alert>
        ) : null}

        <div className="space-y-2">
          <p className="text-sm font-semibold text-ink">Reglas ({content.reglas.length})</p>
          {content.reglas.map((rule, index) => (
            <div key={rule.rule_key} className="rounded-lg border border-line p-3">
              <p className="font-mono text-xs text-ink-faint">
                {rule.rule_key}
                {rule.etapa ? ` · ${rule.etapa}` : ""}
              </p>
              {draft ? (
                <TextArea label="Texto" rows={2} value={rule.texto} onChange={(e) => setRule(index, e.target.value)} />
              ) : (
                <p className="mt-1 whitespace-pre-line text-sm text-ink">{rule.texto}</p>
              )}
            </div>
          ))}
          {draft && kind === "rubro" ? (
            <Button
              type="button"
              variant="secondary"
              onClick={() =>
                setContent({
                  ...content,
                  reglas: [...content.reglas, { rule_key: `rubro.nueva_${content.reglas.length + 1}`, etapa: null, texto: "" }],
                })
              }
            >
              Agregar regla
            </Button>
          ) : null}
        </div>

        {kind === "rubro" ? (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <p className="text-sm font-semibold text-ink">Preguntas frecuentes ({content.faqs?.length ?? 0})</p>
              {content.faqs?.map((faq, index) => (
                <div key={index} className="rounded-lg border border-line p-2 text-sm">
                  {draft ? (
                    <>
                      <Field
                        label="Pregunta"
                        value={faq.pregunta}
                        onChange={(e) =>
                          setContent({
                            ...content,
                            faqs: content.faqs?.map((f, i) => (i === index ? { ...f, pregunta: e.target.value } : f)),
                          })
                        }
                      />
                      <TextArea
                        label="Cómo responder"
                        rows={2}
                        value={faq.respuesta}
                        onChange={(e) =>
                          setContent({
                            ...content,
                            faqs: content.faqs?.map((f, i) => (i === index ? { ...f, respuesta: e.target.value } : f)),
                          })
                        }
                      />
                    </>
                  ) : (
                    <>
                      <p className="font-medium text-ink">{faq.pregunta}</p>
                      <p className="text-ink-soft">{faq.respuesta}</p>
                    </>
                  )}
                </div>
              ))}
            </div>
            <div className="space-y-2">
              <p className="text-sm font-semibold text-ink">Objeciones ({content.objeciones?.length ?? 0})</p>
              {content.objeciones?.map((o, index) => (
                <div key={index} className="rounded-lg border border-line p-2 text-sm">
                  <p className="font-medium text-ink">«{o.objecion}»</p>
                  <p className="text-ink-soft">{o.manejo}</p>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {kind === "rubro" && content.situaciones_sugeridas?.length ? (
          <div className="space-y-2">
            <p className="text-sm font-semibold text-ink">
              Situaciones sugeridas ({content.situaciones_sugeridas.length})
            </p>
            <p className="text-xs text-ink-faint">
              La clave identifica cada una entre versiones: es lo que le dice a un negocio que aplicó la plantilla si
              es nueva o mejorada. Se conservan al guardar el borrador.
            </p>
            <div className="grid gap-2 md:grid-cols-2">
              {content.situaciones_sugeridas.map((s) => (
                <div key={s.clave ?? s.titulo} className="rounded-lg border border-line p-3 text-sm">
                  <p className="font-mono text-xs text-ink-faint">
                    {s.clave ?? "sin clave"}
                    {s.etapas?.length ? ` · solo en ${s.etapas.join(", ")}` : ""}
                    {s.siempre ? " · siempre" : ""}
                  </p>
                  <p className="mt-1 font-medium text-ink">{s.titulo}</p>
                  <p className="text-ink-soft">Cuándo: {s.cuando_aplica}</p>
                  <p className="text-ink-soft">Qué hace: {s.que_hace}</p>
                  {Object.keys(s.campos ?? {}).length ? (
                    <p className="mt-1 text-xs text-ink-faint">
                      Pide al negocio: {Object.values(s.campos ?? {}).join("; ")}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {kind === "rubro" && version.plantilla ? (
          <div className="space-y-1">
            <p className="text-sm font-semibold text-ink">Lo que la plantilla sugiere para el proceso</p>
            <pre className="overflow-auto rounded-lg bg-surface-muted p-3 text-xs text-ink">
              {JSON.stringify(version.plantilla, null, 2)}
            </pre>
          </div>
        ) : null}

        {draft ? (
          <TextArea
            label="Notas de la versión"
            rows={2}
            value={content.notas ?? ""}
            onChange={(e) => setContent({ ...content, notas: e.target.value })}
          />
        ) : null}

        {draft ? (
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              loading={busy === "save"}
              onClick={() => act("save", () => api.catalog.save(kind, version.key, version.version, content))}
            >
              Guardar borrador
            </Button>
            <Button
              type="button"
              variant="secondary"
              loading={busy === "evals"}
              onClick={() =>
                act("evals", async () => {
                  await api.catalog.runEvals(kind, version.key, version.version);
                  // The worker stores the result; read it again in a moment.
                  await new Promise((resolve) => window.setTimeout(resolve, 4000));
                  return api.catalog.get(kind, version.key, version.version);
                })
              }
            >
              Correr evals
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => act("reload", () => api.catalog.get(kind, version.key, version.version))}
            >
              Actualizar resultado
            </Button>
            <Button
              type="button"
              variant="danger"
              loading={busy === "publish"}
              disabled={!version.evals_vigentes}
              onClick={() => {
                if (!window.confirm("Se publicará para todos los negocios que la usan. ¿Seguir?")) return;
                void act("publish", () => api.catalog.publish(kind, version.key, version.version));
              }}
            >
              Publicar
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export default function CatalogAdminPage() {
  const [catalog, setCatalog] = useState<CatalogList | null>(null);
  const [selected, setSelected] = useState<CatalogVersion | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCatalog(await api.catalog.list());
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(entry: Entry, version: number) {
    setError(null);
    try {
      setSelected(await api.catalog.get(entry.kind as CatalogKind, entry.key, version));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  async function newDraft(entry: Entry) {
    setError(null);
    try {
      setSelected(await api.catalog.draft(entry.kind as CatalogKind, entry.key));
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Catálogo de plantillas y rubros</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Una versión nueva llega a todos los negocios que la usan: se publica solo con los evals en verde.
        </p>
      </header>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {!catalog ? <Skeleton className="h-40 w-full" /> : null}
      <div className="grid gap-5 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <ul className="space-y-2">
          {catalog?.items.map((entry) => {
            const draft = entry.versiones.find((v) => v.estado === "borrador");
            return (
              <li key={`${entry.kind}:${entry.key}`} className="rounded-lg border border-line bg-surface p-3 text-sm">
                <p className="font-semibold text-ink">
                  {entry.nombre} <span className="font-normal text-ink-faint">({entry.kind})</span>
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {entry.versiones.map((v) => (
                    <button
                      key={v.version}
                      type="button"
                      onClick={() => void open(entry, v.version)}
                      className={cx(
                        "rounded-full px-2.5 py-1 text-xs font-medium",
                        v.estado === "publicada"
                          ? "bg-success-soft text-success"
                          : v.estado === "borrador"
                            ? "bg-warning-soft text-warning"
                            : "bg-surface-muted text-ink-faint",
                      )}
                    >
                      v{v.version} · {v.estado}
                    </button>
                  ))}
                  {!draft ? (
                    <button
                      type="button"
                      onClick={() => void newDraft(entry)}
                      className="rounded-full border border-line-strong px-2.5 py-1 text-xs font-medium text-ink"
                    >
                      + Borrador
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
        <div>
          {selected ? (
            <Editor
              version={selected}
              onChanged={(next) => {
                setSelected(next);
                void load();
              }}
            />
          ) : (
            <p className="text-sm text-ink-faint">Elige una versión para verla o editarla.</p>
          )}
        </div>
      </div>
    </div>
  );
}
