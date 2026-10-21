"use client";

/**
 * Categorías del catálogo (feature 019).
 *
 * Una categoría agrupa productos para que el asistente los encuentre y para filtrar en Catálogo. Se pueden crear
 * antes de tener productos, renombrar (también se renombra en sus productos), ordenar y apagar.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/toast";
import { Alert, Button, Card, Checkbox, Field, Skeleton } from "@/components/ui";
import { api, type Category } from "@/lib/api/client";
import { ApiError, humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

function Row({ category, onChanged }: { category: Category; onChanged: () => Promise<void> }) {
  const toast = useToast();
  const [name, setName] = useState(category.nombre);
  const [order, setOrder] = useState(String(category.orden));
  const [active, setActive] = useState(category.activa);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changed =
    name.trim() !== category.nombre || Number(order) !== category.orden || active !== category.activa;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.assistant.saveCategory(category.id, {
        nombre: name.trim(),
        orden: Math.max(0, Math.min(1000, Number(order) || 0)),
        activa: active,
      });
      toast.success("Guardado", `Categoría: ${name.trim()}`);
      await onChanged();
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.code === "category_exists"
          ? "Ya tienes una categoría con ese nombre."
          : humanMessage(caught),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="space-y-2 p-3">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <div className="grid gap-3 sm:grid-cols-[1fr_6rem_auto_auto] sm:items-end">
        <Field label="Nombre" value={name} onChange={(e) => setName(e.target.value)} />
        <Field
          label="Orden"
          inputMode="numeric"
          value={order}
          onChange={(e) => setOrder(e.target.value)}
        />
        <Checkbox
          label={`Activa · ${category.productos} ${category.productos === 1 ? "producto" : "productos"}`}
          checked={active}
          onChange={(e) => setActive(e.target.checked)}
        />
        <Button type="button" loading={busy} disabled={!changed || !name.trim()} onClick={() => void save()}>
          Guardar
        </Button>
      </div>
    </li>
  );
}

export default function CategoriesPage() {
  const canEdit = useHasRole(...MANAGES_CONFIGURATION);
  const toast = useToast();
  const [items, setItems] = useState<Category[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setItems((await api.assistant.categories()).items);
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (adding || !name.trim()) return;
    setAdding(true);
    setError(null);
    try {
      await api.assistant.createCategory({ nombre: name.trim(), orden: (items?.length ?? 0) + 1 });
      toast.success("Categoría creada", name.trim());
      setName("");
      await load();
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.code === "category_exists"
          ? "Ya tienes una categoría con ese nombre."
          : humanMessage(caught),
      );
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <Link href="/catalog" className="text-sm font-semibold text-accent underline">
          ← Volver al catálogo
        </Link>
        <h1 className="mt-2 font-heading text-2xl font-semibold text-ink">Categorías</h1>
        <p className="text-sm text-ink-soft">
          Agrupan tus productos y servicios para que el asistente los encuentre y tú los filtres. Al renombrar una, se
          renombra también en sus productos.
        </p>
      </header>

      {error ? <Alert tone="danger">{error}</Alert> : null}

      {canEdit ? (
        <Card title="Nueva categoría">
          <form onSubmit={add} className="flex flex-wrap items-end gap-3">
            <div className="min-w-56 flex-1">
              <Field
                label="Nombre"
                value={name}
                placeholder="Ej.: Servicios, Planes o Calzado"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <Button type="submit" loading={adding} disabled={!name.trim()}>
              Crear
            </Button>
          </form>
        </Card>
      ) : null}

      {items === null ? (
        <Skeleton className="h-32 w-full" />
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-ink-soft">
          Todavía no tienes categorías. Crea la primera arriba, o escribe una al agregar un producto.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line bg-surface">
          {items.map((category) => (
            <Row key={category.id} category={category} onChanged={load} />
          ))}
        </ul>
      )}
    </div>
  );
}
