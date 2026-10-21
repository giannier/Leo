"use client";

/**
 * The product editor of Catálogo (Feature 018, TASK-1802).
 *
 * A side panel on a computer and the whole screen on a phone. The fields are the
 * ones the sales engine reads (price or "cotizar", package of N, digital, stock),
 * plus photos and tags. Photos need the product to exist, so a new product is
 * saved first and the panel stays open on it to add them.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { CloseIcon } from "@/components/icons";
import { useToast } from "@/components/toast";
import { Alert, Button, Checkbox, Field, Select, TextArea, cx } from "@/components/ui";
import { api, type Photo, type Product, type ProductBody } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const MAX_PHOTOS = 10;
const MAX_TAGS = 10;
const MAX_TAG_CHARS = 30;
const MAX_ASK = 6;
const MAX_ASK_CHARS = 80;

interface Draft {
  name: string;
  description: string;
  price: string;
  modo_venta: "precio_fijo" | "cotizacion";
  periodo: "unico" | "mes" | "ano";
  category: string;
  tracksStock: boolean;
  stock: string;
  es_digital: boolean;
  isBundle: boolean;
  bundle_size: string;
  bundle_permite_libres: boolean;
  destacado: boolean;
  is_active: boolean;
  tags: string[];
  /** What to ask before a quote is handed over: one question per line. */
  datos_a_pedir: string;
  /** A service that can be booked (feature 019, phase D). */
  agendable: boolean;
  duracion_min: string;
  buffer_min: string;
}

function draftOf(product: Product | null): Draft {
  return {
    name: product?.name ?? "",
    description: product?.description ?? "",
    price: product ? Number(product.price).toFixed(2) : "",
    modo_venta: product?.modo_venta ?? "precio_fijo",
    periodo: product?.periodo ?? "unico",
    category: product?.category ?? "",
    tracksStock: product?.stock !== null && product?.stock !== undefined,
    stock: product?.stock != null ? String(product.stock) : "",
    es_digital: product?.es_digital ?? false,
    isBundle: product?.bundle_size != null,
    bundle_size: product?.bundle_size != null ? String(product.bundle_size) : "5",
    bundle_permite_libres: product?.bundle_permite_libres ?? true,
    destacado: product?.destacado ?? false,
    is_active: product?.is_active ?? true,
    tags: product?.tags ?? [],
    datos_a_pedir: (product?.datos_a_pedir ?? []).join("\n"),
    agendable: product?.agendable ?? false,
    duracion_min: product?.duracion_min != null ? String(product.duracion_min) : "60",
    buffer_min: product?.buffer_min != null ? String(product.buffer_min) : "0",
  };
}

type Errors = Partial<
  Record<"name" | "price" | "stock" | "bundle_size" | "datos_a_pedir" | "duracion_min" | "buffer_min", string>
>;

function validate(draft: Draft): { errors: Errors; body: ProductBody | null } {
  const errors: Errors = {};
  if (!draft.name.trim()) errors.name = "Ponle un nombre.";
  const amount = Number(draft.price.replace(",", "."));
  const quoted = draft.modo_venta === "cotizacion";
  if (!quoted && (!draft.price.trim() || Number.isNaN(amount) || amount < 0)) {
    errors.price = "Escribe un precio válido, por ejemplo 12.50";
  }
  const stock = Number(draft.stock);
  if (draft.tracksStock && (!draft.stock.trim() || !Number.isInteger(stock) || stock < 0)) {
    errors.stock = "Escribe cuántas unidades tienes (0 o más).";
  }
  const size = Number(draft.bundle_size);
  if (draft.isBundle && (!Number.isInteger(size) || size < 2 || size > 50)) {
    errors.bundle_size = "Un paquete lleva de 2 a 50 elecciones.";
  }
  const minutes = Number(draft.duracion_min);
  if (draft.agendable && (!Number.isInteger(minutes) || minutes < 15 || minutes > 480)) {
    errors.duracion_min = "Entre 15 y 480 minutos.";
  }
  const gap = Number(draft.buffer_min || "0");
  if (draft.agendable && (!Number.isInteger(gap) || gap < 0 || gap > 120)) {
    errors.buffer_min = "Entre 0 y 120 minutos.";
  }
  const ask = draft.datos_a_pedir
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (ask.length > MAX_ASK) errors.datos_a_pedir = `Máximo ${MAX_ASK} preguntas.`;
  else if (ask.some((line) => line.length > MAX_ASK_CHARS)) {
    errors.datos_a_pedir = `Cada pregunta, hasta ${MAX_ASK_CHARS} letras.`;
  }
  if (Object.keys(errors).length) return { errors, body: null };
  return {
    errors,
    body: {
      name: draft.name.trim(),
      description: draft.description.trim() || null,
      // Money as a two-decimal string: the column is NUMERIC(12,2).
      price: quoted && !draft.price.trim() ? "0.00" : amount.toFixed(2),
      modo_venta: draft.modo_venta,
      periodo: draft.periodo,
      category: draft.category.trim() || null,
      stock: draft.tracksStock ? stock : null,
      es_digital: draft.es_digital,
      bundle_size: draft.isBundle ? size : null,
      bundle_permite_libres: draft.isBundle ? draft.bundle_permite_libres : false,
      destacado: draft.destacado,
      is_active: draft.is_active,
      tags: draft.tags,
      datos_a_pedir: ask,
      agendable: draft.agendable,
      duracion_min: draft.agendable ? minutes : null,
      buffer_min: draft.agendable ? gap : 0,
    },
  };
}

export function ProductEditor({
  product,
  categories,
  onClose,
  onSaved,
}: {
  product: Product | null;
  categories: string[];
  onClose: () => void;
  onSaved: (product: Product) => void;
}) {
  const [current, setCurrent] = useState<Product | null>(product);
  const [draft, setDraft] = useState<Draft>(() => draftOf(product));
  const [errors, setErrors] = useState<Errors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setSaved(false);
    setDraft((d) => ({ ...d, [key]: value }));
  };

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);
    const { errors: found, body } = validate(draft);
    setErrors(found);
    if (!body) return;
    setBusy(true);
    try {
      const result = current
        ? await api.products.update(current.id, body)
        : await api.products.create(body);
      setCurrent(result);
      setSaved(true);
      toast.success("Guardado", result.name);
      onSaved(result);
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const title = current ? "Editar producto" : "Nuevo producto";

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-ink/30" onClick={onClose}>
      <div
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
        className="flex h-full w-full min-w-0 max-w-full flex-col bg-surface shadow-xl outline-none sm:max-w-xl"
      >
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="font-heading text-lg font-semibold text-ink">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar"
            className="flex size-11 cursor-pointer items-center justify-center rounded-lg text-ink-soft hover:bg-surface-muted"
          >
            <CloseIcon className="size-5" />
          </button>
        </header>

        <form onSubmit={save} noValidate className="flex min-h-0 flex-1 flex-col">
          <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
            {failure ? <Alert tone="danger">{failure}</Alert> : null}

            <Photos product={current} />

            <Field
              label="Nombre"
              required
              value={draft.name}
              error={errors.name}
              placeholder="Ej.: Hosting web básico, Corte de cabello o Zapatillas urbanas"
              onChange={(e) => set("name", e.target.value)}
            />
            <TextArea
              label="Descripción"
              rows={3}
              hint="Es lo que el asistente le cuenta al cliente."
              value={draft.description}
              onChange={(e) => set("description", e.target.value)}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <Select
                label="Cómo se vende"
                value={draft.modo_venta}
                onChange={(e) => set("modo_venta", e.target.value as Draft["modo_venta"])}
                options={[
                  { value: "precio_fijo", label: "Precio fijo" },
                  { value: "cotizacion", label: "Se cotiza (lo ve una persona)" },
                ]}
              />
              <Field
                label={draft.modo_venta === "cotizacion" ? "Precio referencial" : "Precio (S/)"}
                required={draft.modo_venta !== "cotizacion"}
                inputMode="decimal"
                placeholder="12.50"
                value={draft.price}
                error={errors.price}
                hint={draft.modo_venta === "cotizacion" ? "Opcional." : undefined}
                onChange={(e) => set("price", e.target.value)}
              />
            </div>

            {draft.modo_venta === "cotizacion" ? (
              <TextArea
                label="Qué le preguntas para cotizar"
                rows={3}
                placeholder={"Para cuántas personas\nPara cuándo lo necesita"}
                hint="Una pregunta por línea, hasta 6. El asistente las hace una por mensaje y le pasa las respuestas a tu equipo."
                value={draft.datos_a_pedir}
                error={errors.datos_a_pedir}
                onChange={(e) => set("datos_a_pedir", e.target.value)}
              />
            ) : null}

            <Select
              label="El precio se cobra"
              value={draft.periodo}
              onChange={(e) => set("periodo", e.target.value as Draft["periodo"])}
              hint="Una vez, o cada mes o cada año si es un plan o una suscripción. El asistente lo dice así."
              options={[
                { value: "unico", label: "Una sola vez" },
                { value: "mes", label: "Cada mes" },
                { value: "ano", label: "Cada año" },
              ]}
            />

            <div>
              <Field
                label="Categoría"
                list="catalog-categories"
                value={draft.category}
                placeholder="Ej.: Servicios, Planes o Calzado"
                hint="Elige una que ya tienes o escribe una nueva."
                onChange={(e) => set("category", e.target.value)}
              />
              <datalist id="catalog-categories">
                {categories.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>

            <TagInput tags={draft.tags} onChange={(tags) => set("tags", tags)} />

            <fieldset className="space-y-3 rounded-lg border border-line p-3">
              <legend className="px-1 text-sm font-medium text-ink">Agenda</legend>
              <Checkbox
                label="Se puede agendar (cita, visita o reunión)"
                hint="El asistente ofrece horarios libres y agenda solo, sin que dos clientes tomen la misma hora."
                checked={draft.agendable}
                onChange={(e) => set("agendable", e.target.checked)}
              />
              {draft.agendable ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field
                    label="Cuánto dura (minutos)"
                    inputMode="numeric"
                    value={draft.duracion_min}
                    error={errors.duracion_min}
                    onChange={(e) => set("duracion_min", e.target.value)}
                  />
                  <Field
                    label="Pausa después (minutos)"
                    inputMode="numeric"
                    value={draft.buffer_min}
                    error={errors.buffer_min}
                    hint="Para limpiar, preparar o desplazarte."
                    onChange={(e) => set("buffer_min", e.target.value)}
                  />
                </div>
              ) : null}
            </fieldset>

            <fieldset className="space-y-3 rounded-lg border border-line p-3">
              <legend className="px-1 text-sm font-medium text-ink">Paquete</legend>
              <Checkbox
                label="Es un paquete de varias elecciones"
                hint="Ej.: un paquete de 5 clases o de 3 servicios a elegir. El cliente elige y el asistente cuenta cuántos faltan."
                checked={draft.isBundle}
                onChange={(e) => set("isBundle", e.target.checked)}
              />
              {draft.isBundle ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field
                    label="Cuántas elecciones"
                    inputMode="numeric"
                    value={draft.bundle_size}
                    error={errors.bundle_size}
                    onChange={(e) => set("bundle_size", e.target.value)}
                  />
                  <Checkbox
                    label="Puede elegir opciones que no están en el catálogo"
                    checked={draft.bundle_permite_libres}
                    onChange={(e) => set("bundle_permite_libres", e.target.checked)}
                  />
                </div>
              ) : null}
            </fieldset>

            <fieldset className="space-y-3 rounded-lg border border-line p-3">
              <legend className="px-1 text-sm font-medium text-ink">Más opciones</legend>
              <Checkbox
                label="Controlar stock"
                hint="Si se acaba, el asistente no lo ofrece."
                checked={draft.tracksStock}
                onChange={(e) => set("tracksStock", e.target.checked)}
              />
              {draft.tracksStock ? (
                <Field
                  label="Unidades disponibles"
                  inputMode="numeric"
                  value={draft.stock}
                  error={errors.stock}
                  onChange={(e) => set("stock", e.target.value)}
                />
              ) : null}
              <Checkbox
                label="Es digital"
                hint="No se envía: no se pide dirección de entrega."
                checked={draft.es_digital}
                onChange={(e) => set("es_digital", e.target.checked)}
              />
              <Checkbox
                label="Destacado"
                hint="Sale primero cuando el asistente muestra productos."
                checked={draft.destacado}
                onChange={(e) => set("destacado", e.target.checked)}
              />
              <Checkbox
                label="Activo"
                hint="Si lo desactivas, el asistente deja de ofrecerlo."
                checked={draft.is_active}
                onChange={(e) => set("is_active", e.target.checked)}
              />
            </fieldset>
          </div>

          <footer className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
            <Button type="submit" loading={busy}>
              {current ? "Guardar cambios" : "Crear producto"}
            </Button>
            <Button type="button" variant="secondary" onClick={onClose}>
              Cerrar
            </Button>
            {saved ? (
              <span role="status" className="text-sm text-success">
                Guardado
              </span>
            ) : null}
          </footer>
        </form>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- tags */

export function TagInput({ tags, onChange }: { tags: string[]; onChange: (tags: string[]) => void }) {
  const [text, setText] = useState("");
  const [hint, setHint] = useState<string | null>(null);

  function add(raw: string) {
    const parts = raw
      .split(/[,;]/)
      .map((p) => p.replace(/^#/, "").trim().replace(/\s+/g, " "))
      .filter(Boolean);
    const next = [...tags];
    for (const tag of parts) {
      if (tag.length > MAX_TAG_CHARS) {
        setHint(`Cada etiqueta puede tener hasta ${MAX_TAG_CHARS} caracteres.`);
        return;
      }
      const key = tag.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
      if (next.some((t) => t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase() === key)) continue;
      if (next.length >= MAX_TAGS) {
        setHint(`Hasta ${MAX_TAGS} etiquetas por producto.`);
        break;
      }
      next.push(tag);
    }
    setText("");
    onChange(next);
  }

  return (
    <div className="space-y-1.5">
      <label htmlFor="product-tags" className="text-sm font-medium text-ink">
        Etiquetas
      </label>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line-strong p-2">
        {tags.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded-full bg-surface-muted py-1 pl-2.5 pr-1 text-xs text-ink"
          >
            {tag}
            <button
              type="button"
              aria-label={`Quitar etiqueta ${tag}`}
              onClick={() => onChange(tags.filter((t) => t !== tag))}
              className="flex size-6 cursor-pointer items-center justify-center rounded-full hover:bg-line"
            >
              <CloseIcon className="size-3" />
            </button>
          </span>
        ))}
        <input
          id="product-tags"
          value={text}
          onChange={(e) => {
            setHint(null);
            setText(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              if (text.trim()) add(text);
            } else if (e.key === "Backspace" && !text && tags.length) {
              onChange(tags.slice(0, -1));
            }
          }}
          onBlur={() => text.trim() && add(text)}
          placeholder={tags.length ? "" : "Ej.: oferta, nuevo, popular"}
          className="min-h-9 min-w-32 flex-1 bg-transparent px-1 text-base text-ink outline-none placeholder:text-ink-faint"
        />
      </div>
      <p className={cx("text-xs", hint ? "text-danger" : "text-ink-faint")}>
        {hint ?? "Ayudan al asistente a encontrar el producto. Enter o coma para agregar."}
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- photos */

function Photos({ product }: { product: Product | null }) {
  const [photos, setPhotos] = useState<Photo[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!product) return;
    try {
      setPhotos((await api.products.photos(product.id)).items);
    } catch (caught) {
      setFailure(humanMessage(caught));
    }
  }, [product]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!product) {
    return (
      <div className="rounded-lg border border-dashed border-line-strong p-4 text-sm text-ink-soft">
        Crea el producto y luego agrégale hasta {MAX_PHOTOS} fotos. El asistente las envía cuando el
        cliente quiere verlo.
      </div>
    );
  }

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    if (!product) return;
    const files = Array.from(event.target.files ?? []);
    if (input.current) input.current.value = "";
    setFailure(null);
    setBusy(true);
    try {
      for (const file of files) {
        setPhotos((await api.products.addPhoto(product.id, file)).items);
      }
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function move(index: number, step: -1 | 1) {
    if (!product || !photos) return;
    const ids = photos.map((p) => p.id);
    const target = index + step;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    try {
      setPhotos((await api.products.orderPhotos(product.id, ids)).items);
    } catch (caught) {
      setFailure(humanMessage(caught));
    }
  }

  async function remove(id: string) {
    if (!product) return;
    try {
      await api.products.removePhoto(product.id, id);
      setPhotos((list) => (list ?? []).filter((p) => p.id !== id));
    } catch (caught) {
      setFailure(humanMessage(caught));
    }
  }

  const count = photos?.length ?? 0;

  return (
    <section aria-label="Fotos" className="space-y-2">
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium text-ink">
          Fotos <span className="font-normal text-ink-faint">({count}/{MAX_PHOTOS})</span>
        </p>
        <p className="text-xs text-ink-faint">La primera es la principal.</p>
      </div>
      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4" data-testid="product-photos">
        {(photos ?? []).map((photo, index) => (
          <li key={photo.id} className="relative overflow-hidden rounded-lg border border-line">
            {photo.url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={photo.url} alt={`Foto ${index + 1}`} className="aspect-square w-full object-cover" />
            ) : (
              <div className="aspect-square w-full bg-surface-muted" />
            )}
            {index === 0 ? (
              <span className="absolute left-1 top-1 rounded bg-ink/70 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                Principal
              </span>
            ) : null}
            <div className="flex justify-between bg-surface/90 text-xs">
              <button
                type="button"
                aria-label={`Mover foto ${index + 1} antes`}
                disabled={index === 0}
                onClick={() => void move(index, -1)}
                className="min-h-8 flex-1 cursor-pointer hover:bg-surface-muted disabled:opacity-30"
              >
                ←
              </button>
              <button
                type="button"
                aria-label={`Quitar foto ${index + 1}`}
                onClick={() => void remove(photo.id)}
                className="min-h-8 flex-1 cursor-pointer text-danger hover:bg-surface-muted"
              >
                ✕
              </button>
              <button
                type="button"
                aria-label={`Mover foto ${index + 1} después`}
                disabled={index === count - 1}
                onClick={() => void move(index, 1)}
                className="min-h-8 flex-1 cursor-pointer hover:bg-surface-muted disabled:opacity-30"
              >
                →
              </button>
            </div>
          </li>
        ))}
        {count < MAX_PHOTOS ? (
          <li>
            <input
              ref={input}
              id="product-photo-upload"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              className="sr-only"
              onChange={upload}
            />
            <label
              htmlFor="product-photo-upload"
              className={cx(
                "flex aspect-square cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-line-strong text-xs text-ink-soft hover:bg-surface-muted",
                busy && "pointer-events-none opacity-50",
              )}
            >
              <span className="text-xl">+</span>
              {busy ? "Subiendo…" : "Agregar foto"}
            </label>
          </li>
        ) : null}
      </ul>
    </section>
  );
}
