"use client";

/**
 * WhatsApp templates (Feature 011, HU-01): the business writes them with named variables
 * ({{nombre}}, {{negocio}}, {{producto}}), sends them to Meta, and sees the verdict. A
 * rejected one says why and can be edited; an approved one is duplicated to change it.
 * The library offers ready-made ones for the business's industry.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Alert, Button, Card, Field, Select, Skeleton, TextArea, cx } from "@/components/ui";
import {
  api,
  type LibraryTemplate,
  type MediaAsset,
  type MessageTemplate,
  type TemplateBody,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

export const TEMPLATE_STATUS: Record<string, { label: string; tone: string }> = {
  draft: { label: "Borrador", tone: "bg-surface-muted text-ink-soft" },
  pending: { label: "En revisión de Meta", tone: "bg-warning-soft text-warning" },
  approved: { label: "Aprobada", tone: "bg-success-soft text-success" },
  rejected: { label: "Rechazada", tone: "bg-danger-soft text-danger" },
  paused: { label: "Pausada por Meta", tone: "bg-warning-soft text-warning" },
  disabled: { label: "Desactivada", tone: "bg-surface-muted text-ink-faint" },
};

const CATEGORY: Record<string, string> = { utility: "Utilidad", marketing: "Marketing" };
const VARIABLES = ["nombre", "negocio", "producto"] as const;
const EXAMPLES: Record<string, string> = { nombre: "Ana", negocio: "tu negocio", producto: "tu producto" };

export function StatusChip({ status }: { status: string }) {
  const style = TEMPLATE_STATUS[status] ?? { label: status, tone: "bg-surface-muted text-ink-soft" };
  return (
    <span className={cx("inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold", style.tone)}>
      {style.label}
    </span>
  );
}

export function preview(body: string, businessName?: string): string {
  return body.replace(/\{\{\s*([A-Za-z_]+)\s*\}\}/g, (match, name: string) => {
    const key = name.toLowerCase();
    if (key === "negocio" && businessName) return businessName;
    return EXAMPLES[key] ?? match;
  });
}

/** How the customer sees it on WhatsApp. */
export function Bubble({ body, footer, image }: { body: string; footer?: string | null; image?: string | null }) {
  return (
    <div className="rounded-xl bg-[#e7f7dc] p-3 text-sm text-ink shadow-sm" data-testid="template-preview">
      {image ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={image} alt="" className="mb-2 max-h-40 w-full rounded-lg object-cover" />
      ) : null}
      <p className="whitespace-pre-wrap break-words">{body || "Escribe el mensaje…"}</p>
      {footer ? <p className="mt-1.5 text-xs text-ink-faint">{footer}</p> : null}
    </div>
  );
}

function Editor({
  initial,
  images,
  onSaved,
  onCancel,
}: {
  initial: MessageTemplate | null;
  images: MediaAsset[];
  onSaved: (template: MessageTemplate) => void;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState(initial?.label ?? "");
  const [category, setCategory] = useState<"utility" | "marketing">(
    (initial?.category as "utility" | "marketing") ?? "marketing",
  );
  const [body, setBody] = useState(initial?.body ?? "Hola {{nombre}}, ");
  const [footer, setFooter] = useState(initial?.footer ?? "");
  const [image, setImage] = useState(initial?.header_media_asset_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);

  function insert(name: string) {
    const token = `{{${name}}}`;
    const element = area.current;
    if (!element) {
      setBody((value) => value + token);
      return;
    }
    const start = element.selectionStart ?? body.length;
    const end = element.selectionEnd ?? body.length;
    const next = body.slice(0, start) + token + body.slice(end);
    setBody(next);
    requestAnimationFrame(() => {
      element.focus();
      element.setSelectionRange(start + token.length, start + token.length);
    });
  }

  async function save() {
    setBusy(true);
    setError(null);
    const payload: TemplateBody = {
      label,
      category,
      body,
      footer: footer.trim() || null,
      header_media_asset_id: image || null,
    };
    try {
      onSaved(initial ? await api.templates.update(initial.id, payload) : await api.templates.create(payload));
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const picked = images.find((asset) => asset.id === image);
  return (
    <Card title={initial ? "Editar plantilla" : "Nueva plantilla"}>
      <div className="grid gap-5 lg:grid-cols-[1fr_18rem]">
        <div className="min-w-0 space-y-4">
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <Field label="Nombre" value={label} maxLength={60} onChange={(e) => setLabel(e.target.value)} required />
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-ink">Tipo</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {(
                [
                  ["marketing", "Marketing", "Promociones, novedades, invitar a comprar."],
                  ["utility", "Utilidad", "Avisos sobre un pedido o pago que ya existe."],
                ] as const
              ).map(([value, title, hint]) => (
                <label
                  key={value}
                  className={cx(
                    "flex cursor-pointer gap-2 rounded-lg border p-3 text-sm",
                    category === value ? "border-accent bg-info-soft" : "border-line",
                  )}
                >
                  <input
                    type="radio"
                    name="categoria"
                    value={value}
                    checked={category === value}
                    onChange={() => setCategory(value)}
                    className="mt-0.5 accent-accent"
                  />
                  <span>
                    <span className="font-semibold text-ink">{title}</span>
                    <span className="block text-xs text-ink-soft">{hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="space-y-2">
            <TextArea
              ref={area}
              label="Mensaje"
              value={body}
              maxLength={1024}
              rows={5}
              onChange={(e) => setBody(e.target.value)}
              hint="No empieces ni termines con una variable. Meta revisa cada plantilla (suele tardar minutos)."
            />
            <div className="flex flex-wrap gap-2">
              {VARIABLES.map((name) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => insert(name)}
                  className="min-h-9 cursor-pointer rounded-full border border-line-strong px-3 text-xs font-medium text-ink hover:bg-surface-muted"
                >
                  + {`{{${name}}}`}
                </button>
              ))}
            </div>
          </div>
          <Field
            label="Pie (opcional)"
            value={footer}
            maxLength={60}
            onChange={(e) => setFooter(e.target.value)}
            hint={category === "marketing" ? "Si lo dejas vacío, Leo agrega cómo darse de baja." : undefined}
          />
          <Select
            label="Imagen arriba (opcional)"
            value={image}
            onChange={(e) => setImage(e.target.value)}
            options={[
              { value: "", label: "Sin imagen" },
              ...images.map((asset) => ({ value: asset.id, label: asset.titulo })),
            ]}
            hint={images.length ? undefined : "Sube imágenes en Asistente → Archivos para usarlas aquí."}
          />
          <div className="flex flex-wrap gap-2">
            <Button type="button" loading={busy} onClick={() => void save()}>
              Guardar
            </Button>
            <Button type="button" variant="secondary" onClick={onCancel}>
              Cancelar
            </Button>
          </div>
        </div>
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">Así lo verá tu cliente</p>
          <Bubble
            body={preview(body)}
            footer={footer || (category === "marketing" ? "Escribe BAJA si no quieres recibir más mensajes" : null)}
            image={picked?.preview_url ?? null}
          />
        </div>
      </div>
    </Card>
  );
}

export function TemplatesPanel({ canEdit }: { canEdit: boolean }) {
  const [templates, setTemplates] = useState<MessageTemplate[] | null>(null);
  const [library, setLibrary] = useState<LibraryTemplate[]>([]);
  const [images, setImages] = useState<MediaAsset[]>([]);
  const [editing, setEditing] = useState<MessageTemplate | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, lib] = await Promise.all([api.templates.list(), api.templates.library()]);
      setTemplates(list);
      setLibrary(lib);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
    if (canEdit) {
      api.assistant
        .media()
        .then((media) => setImages(media.items.filter((asset) => asset.tipo === "imagen" && asset.is_active)))
        .catch(() => setImages([]));
    }
  }, [load, canEdit]);

  async function run(id: string, action: () => Promise<unknown>, done?: string) {
    setBusy(id);
    setError(null);
    setNotice(null);
    try {
      await action();
      if (done) setNotice(done);
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  if (editing) {
    return (
      <Editor
        initial={editing === "new" ? null : editing}
        images={images}
        onCancel={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          setNotice("Plantilla guardada. Envíala a Meta cuando esté lista.");
          void load();
        }}
      />
    );
  }

  return (
    <div className="space-y-5">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-soft">
          Para escribirle a un cliente fuera de las 24 horas, WhatsApp exige una plantilla aprobada por Meta.
        </p>
        {canEdit ? (
          <Button type="button" onClick={() => setEditing("new")}>
            Nueva plantilla
          </Button>
        ) : null}
      </div>

      {!templates ? (
        <Skeleton className="h-32 w-full" />
      ) : templates.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-ink-soft">
          Aún no tienes plantillas. Empieza con una de la biblioteca.
        </p>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2" data-testid="template-list">
          {templates.map((template) => (
            <li key={template.id} className="min-w-0 space-y-3 rounded-xl border border-line bg-surface p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-semibold text-ink">{template.label}</h3>
                <StatusChip status={template.status} />
              </div>
              <p className="text-xs text-ink-faint">{CATEGORY[template.category] ?? template.category}</p>
              <Bubble body={preview(template.body)} footer={template.footer} />
              {template.status === "rejected" && template.rejection_hint ? (
                <Alert tone="danger" title="Meta la rechazó">
                  {template.rejection_hint}
                </Alert>
              ) : null}
              {canEdit ? (
                <div className="flex flex-wrap gap-2">
                  {template.status === "draft" || template.status === "rejected" ? (
                    <>
                      <Button
                        type="button"
                        loading={busy === template.id}
                        onClick={() =>
                          void run(
                            template.id,
                            () => api.templates.submit(template.id),
                            "Enviada a Meta. Te avisamos aquí cuando la revisen.",
                          )
                        }
                      >
                        Enviar a Meta
                      </Button>
                      <Button type="button" variant="secondary" onClick={() => setEditing(template)}>
                        Editar
                      </Button>
                    </>
                  ) : null}
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => void run(template.id, () => api.templates.duplicate(template.id), "Copia creada.")}
                  >
                    Duplicar
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      if (window.confirm(`¿Borrar la plantilla «${template.label}»?`)) {
                        void run(template.id, () => api.templates.remove(template.id));
                      }
                    }}
                  >
                    Borrar
                  </Button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {canEdit && library.length ? (
        <Card title="Biblioteca" description="Plantillas listas para tu tipo de negocio. Úsala y ajústala a tu estilo.">
          <ul className="grid gap-3 md:grid-cols-2" data-testid="template-library">
            {library.map((entry) => (
              <li key={entry.key} className="flex min-w-0 flex-col gap-2 rounded-lg border border-line p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-ink">{entry.label}</span>
                  <span className="text-xs text-ink-faint">{CATEGORY[entry.category]}</span>
                </div>
                <p className="flex-1 text-sm text-ink-soft">{preview(entry.body)}</p>
                <Button
                  type="button"
                  variant="secondary"
                  loading={busy === entry.key}
                  onClick={() =>
                    void run(
                      entry.key,
                      () => api.templates.clone(entry.key),
                      `«${entry.label}» se agregó a tus plantillas.`,
                    )
                  }
                >
                  Usar
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
