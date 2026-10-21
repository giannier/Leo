"use client";

/**
 * Catálogo (Feature 018, TASK-1802): everything the business sells, in one place.
 *
 * Search by name or tag, filter by category, tag and state, and act on each row:
 * edit, duplicate, switch on or off, remove. New products and imports come from
 * the same screen. Every product shows what the sales engine does with it:
 * price or "se cotiza", package of N, stock.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ProductEditor } from "@/components/catalog/product-editor";
import { BoxIcon } from "@/components/icons";
import { Alert, Button, Skeleton, cx } from "@/components/ui";
import { ImportProducts } from "@/components/wizard/step-catalog";
import { api, type Category, type Product } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

const PAGE = 50;
type StateFilter = "todos" | "activos" | "inactivos";

function money(value: string): string {
  return `S/ ${Number(value).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const PERIOD_SUFFIX = { unico: "", mes: " al mes", ano: " al año" } as const;

function priceOf(product: Product): string {
  const period = PERIOD_SUFFIX[product.periodo];
  if (product.modo_venta !== "cotizacion") return `${money(product.price)}${period}`;
  return Number(product.price) > 0 ? `Desde ${money(product.price)}${period} · se cotiza` : "Se cotiza";
}

export default function CatalogPage() {
  const canEdit = useHasRole(...MANAGES_CONFIGURATION);
  const [items, setItems] = useState<Product[] | null>(null);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [state, setState] = useState<StateFilter>("todos");
  const [categories, setCategories] = useState<Category[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Product | "nuevo" | null>(null);
  const [importing, setImporting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  // The search waits until typing stops, so each letter is not a request.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const filters = useMemo(
    () => ({
      search: query || undefined,
      category: category || undefined,
      tag: tag || undefined,
      is_active: state === "todos" ? undefined : state === "activos",
    }),
    [query, category, tag, state],
  );

  const load = useCallback(
    async (offset = 0) => {
      setError(null);
      try {
        const page = await api.products.list({ ...filters, limit: PAGE, offset });
        setItems((current) => (offset && current ? [...current, ...page.items] : page.items));
        setTotal(page.total);
      } catch (caught) {
        setError(humanMessage(caught));
      }
    },
    [filters],
  );

  const loadCategories = useCallback(async () => {
    if (!canEdit) return;
    try {
      setCategories((await api.assistant.categories()).items);
    } catch {
      // The filter still works by typing; the list is a convenience.
    }
  }, [canEdit]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadCategories();
  }, [loadCategories]);

  async function act(product: Product, action: () => Promise<unknown>) {
    setBusy(product.id);
    setError(null);
    try {
      await action();
      await load();
      await loadCategories();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  const filtered = Boolean(query || category || tag || state !== "todos");
  const categoryNames = categories.map((c) => c.nombre);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-ink">Catálogo</h1>
          <p className="text-sm text-ink-soft">
            Lo que vendes. Tu asistente solo ofrece lo que está aquí y activo.
          </p>
        </div>
        {canEdit ? (
          <div className="flex flex-wrap gap-2">
            <Link
              href="/catalog/categorias"
              className="inline-flex min-h-11 items-center rounded-lg border border-line-strong bg-surface px-4 text-sm font-semibold text-ink hover:bg-surface-muted"
            >
              Categorías
            </Link>
            <Button variant="secondary" onClick={() => setImporting((on) => !on)}>
              Importar Excel o CSV
            </Button>
            <Button onClick={() => setEditing("nuevo")}>Agregar producto</Button>
          </div>
        ) : null}
      </header>

      {importing ? (
        <ImportProducts
          onImported={async () => {
            await load();
            await loadCategories();
          }}
        />
      ) : null}

      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-56 flex-1 flex-col gap-1.5 text-sm font-medium text-ink">
          Buscar
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Nombre o etiqueta"
            className="min-h-11 rounded-lg border border-line-strong bg-surface px-3 text-base text-ink placeholder:text-ink-faint"
          />
        </label>
        {categoryNames.length ? (
          <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
            Categoría
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="min-h-11 cursor-pointer rounded-lg border border-line-strong bg-surface px-3 text-base text-ink"
            >
              <option value="">Todas</option>
              {categoryNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <div className="flex gap-1" role="group" aria-label="Estado">
          {(["todos", "activos", "inactivos"] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={state === value}
              onClick={() => setState(value)}
              className={cx(
                "min-h-11 rounded-full border px-3 text-xs font-medium capitalize",
                state === value ? "border-brand bg-brand text-brand-fg" : "border-line bg-surface text-ink-soft",
              )}
            >
              {value}
            </button>
          ))}
        </div>
      </div>

      {tag ? (
        <p className="text-sm text-ink-soft">
          Con la etiqueta <span className="font-semibold text-ink">{tag}</span>{" "}
          <button type="button" onClick={() => setTag("")} className="cursor-pointer text-accent underline">
            quitar
          </button>
        </p>
      ) : null}

      {error ? <Alert tone="danger">{error}</Alert> : null}

      {items === null ? (
        <div className="space-y-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line-strong p-8 text-center">
          <BoxIcon className="mx-auto size-8 text-line-strong" />
          <p className="mt-2 font-medium text-ink">
            {filtered ? "Nada coincide con tu búsqueda" : "Aún no tienes productos"}
          </p>
          <p className="mt-1 text-sm text-ink-soft">
            {filtered
              ? "Prueba con otra palabra o quita los filtros."
              : "Agrega tu primer producto o importa tu lista desde Excel."}
          </p>
          {!filtered && canEdit ? (
            <div className="mt-4 flex justify-center gap-2">
              <Button onClick={() => setEditing("nuevo")}>Agregar mi primer producto</Button>
            </div>
          ) : null}
        </div>
      ) : (
        <>
          <p className="text-xs text-ink-faint" aria-live="polite">
            {items.length} de {total} productos
          </p>
          <ul className="divide-y divide-line rounded-xl border border-line bg-surface" data-testid="catalog-list">
            {items.map((product) => (
              <li key={product.id} className="flex flex-wrap items-center gap-3 p-3 sm:flex-nowrap">
                {product.foto_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={product.foto_url}
                    alt=""
                    className="size-14 shrink-0 rounded-lg border border-line object-cover"
                  />
                ) : (
                  <div className="flex size-14 shrink-0 items-center justify-center rounded-lg bg-surface-muted">
                    <BoxIcon className="size-6 text-line-strong" />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">
                    <span className="truncate">{product.name}</span>
                    {!product.is_active ? (
                      <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] text-ink-soft">
                        Inactivo
                      </span>
                    ) : null}
                    {product.destacado ? (
                      <span className="rounded-full bg-warning-soft px-2 py-0.5 text-[11px] text-warning">
                        Destacado
                      </span>
                    ) : null}
                    {product.bundle_size ? (
                      <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent">
                        Paquete de {product.bundle_size}
                      </span>
                    ) : null}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-faint">
                    {product.category ?? "Sin categoría"}
                    {product.stock !== null ? ` · ${product.stock} en stock` : ""}
                    {product.es_digital ? " · Digital" : ""}
                  </p>
                  {product.tags.length ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {product.tags.map((t) => (
                        <button
                          key={t}
                          type="button"
                          onClick={() => setTag(t)}
                          className="cursor-pointer rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-soft hover:border-accent hover:text-accent"
                        >
                          #{t}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
                <span className="tabular shrink-0 text-sm font-semibold text-ink">
                  {priceOf(product)}
                </span>
                {canEdit ? (
                  <div className="flex w-full shrink-0 flex-wrap gap-1 sm:w-auto">
                    <Button variant="secondary" onClick={() => setEditing(product)}>
                      Editar
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={busy === product.id}
                      onClick={() => void act(product, () => api.products.duplicate(product.id))}
                    >
                      Duplicar
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={busy === product.id}
                      onClick={() =>
                        void act(product, () =>
                          api.products.update(product.id, { is_active: !product.is_active }),
                        )
                      }
                    >
                      {product.is_active ? "Desactivar" : "Activar"}
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={busy === product.id}
                      aria-label={`Borrar ${product.name}`}
                      onClick={() => {
                        if (window.confirm(`¿Borrar "${product.name}"? Tu asistente dejará de ofrecerlo.`)) {
                          void act(product, () => api.products.remove(product.id));
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
          {items.length < total ? (
            <div className="flex justify-center">
              <Button variant="secondary" onClick={() => void load(items.length)}>
                Ver más
              </Button>
            </div>
          ) : null}
        </>
      )}

      {editing ? (
        <ProductEditor
          product={editing === "nuevo" ? null : editing}
          categories={categoryNames}
          onClose={() => {
            // Photos change without a save, so the list is read again on close.
            setEditing(null);
            void load();
          }}
          onSaved={() => {
            void load();
            void loadCategories();
          }}
        />
      ) : null}
    </div>
  );
}
