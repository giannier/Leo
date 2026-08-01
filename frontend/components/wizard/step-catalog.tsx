"use client";

/**
 * Step 3: what the business sells.
 *
 * Two ways in, because the two customers are different. Somebody with six
 * pastries types them; somebody with four hundred SKUs has a spreadsheet and
 * will abandon the product rather than retype it.
 *
 * The import always previews before it writes (HU-02): "WHEN importo un CSV
 * válido, THEN veo un preview y confirmo antes de crear productos." An import
 * that silently creates four hundred rows is one nobody dares try twice.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { AlertIcon, BoxIcon, CloseIcon } from "@/components/icons";
import { Alert, Button, Field, Skeleton, TextArea, cx } from "@/components/ui";
import { api, type ImportPreview, type Product } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { StepIntro, type StepProps } from "./shared";

const ACCEPTED = ".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/** Rows shown in the preview before the list is truncated. */
const PREVIEW_ERRORS_SHOWN = 8;

export function StepCatalog({ onSaved }: StepProps) {
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await api.products.list({ limit: 50 });
      setProducts(page.items);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const afterChange = useCallback(async () => {
    await load();
    onSaved();
  }, [load, onSaved]);

  return (
    <div className="space-y-5">
      <StepIntro>
        Tu agente solo puede vender lo que esté acá. Con uno alcanza para empezar; puedes
        agregar el resto después.
      </StepIntro>

      {error ? (
        <Alert tone="danger">
          {error}{" "}
          <button
            type="button"
            onClick={() => void load()}
            className="cursor-pointer font-semibold text-accent underline"
          >
            Reintentar
          </button>
        </Alert>
      ) : null}

      <ProductList products={products} onChanged={afterChange} />
      <AddProduct onAdded={afterChange} />
      <ImportProducts onImported={afterChange} />
    </div>
  );
}

/* ------------------------------------------------------------------ list */

function ProductList({
  products,
  onChanged,
}: {
  products: Product[] | null;
  onChanged: () => Promise<void>;
}) {
  const [removing, setRemoving] = useState<string | null>(null);

  if (products === null) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    );
  }

  if (products.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-line-strong p-6 text-center">
        <BoxIcon className="mx-auto size-8 text-line-strong" />
        <p className="mt-2 text-sm font-medium text-ink">Todavía no cargaste nada</p>
        <p className="mt-1 text-xs text-ink-faint">
          Agrega tu primer producto abajo, o sube tu lista en Excel.
        </p>
      </div>
    );
  }

  async function remove(id: string) {
    setRemoving(id);
    try {
      await api.products.remove(id);
      await onChanged();
    } finally {
      setRemoving(null);
    }
  }

  return (
    <ul className="divide-y divide-line rounded-lg border border-line">
      {products.map((product) => (
        <li key={product.id} className="flex items-center gap-3 p-3">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-ink">{product.name}</p>
            {product.description ? (
              <p className="truncate text-xs text-ink-faint">{product.description}</p>
            ) : null}
          </div>
          {/* Tabular figures, so a column of prices lines up on the decimal
           * instead of wandering. */}
          <span className="tabular shrink-0 text-sm font-semibold text-ink">
            {Number(product.price).toFixed(2)}
          </span>
          <button
            type="button"
            onClick={() => void remove(product.id)}
            disabled={removing === product.id}
            className="flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-lg text-ink-faint hover:bg-surface-muted hover:text-danger disabled:opacity-50"
            aria-label={`Quitar ${product.name}`}
          >
            <CloseIcon className="size-4" />
          </button>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------- add one */

function AddProduct({ onAdded }: { onAdded: () => Promise<void> }) {
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const [description, setDescription] = useState("");
  const [errors, setErrors] = useState<{ name?: string; price?: string }>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);

    const found: typeof errors = {};
    if (!name.trim()) found.name = "Ponle un nombre.";
    const amount = Number(price.replace(",", "."));
    if (!price.trim() || Number.isNaN(amount) || amount < 0) {
      found.price = "Escribe un precio válido, por ejemplo 12.50";
    }
    if (Object.keys(found).length > 0) {
      setErrors(found);
      return;
    }
    setErrors({});

    setBusy(true);
    try {
      await api.products.create({
        name: name.trim(),
        description: description.trim() || null,
        // Two decimals, as a string, because the column is NUMERIC(12,2) and
        // the whole codebase treats money as exact.
        price: amount.toFixed(2),
        is_active: true,
      });
      setName("");
      setPrice("");
      setDescription("");
      await onAdded();
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4 rounded-lg border border-line p-4">
      <p className="text-sm font-medium text-ink">Agregar un producto</p>

      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      <div className="grid gap-4 sm:grid-cols-[2fr_1fr]">
        <Field
          label="Nombre"
          name="product_name"
          required
          value={name}
          error={errors.name}
          onChange={(e) => setName(e.target.value)}
        />
        <Field
          label="Precio"
          name="product_price"
          required
          // `decimal`, so Android shows a keypad with a separator on it.
          inputMode="decimal"
          placeholder="12.50"
          value={price}
          error={errors.price}
          onChange={(e) => setPrice(e.target.value)}
        />
      </div>

      <TextArea
        label="Descripción"
        name="product_description"
        rows={2}
        hint="Opcional. Es lo que el agente le cuenta al cliente."
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />

      <Button type="submit" loading={busy}>
        Agregar
      </Button>
    </form>
  );
}

/* --------------------------------------------------------------- import */

function ImportProducts({ onImported }: { onImported: () => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<number | null>(null);

  async function pick(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    setFailure(null);
    setCreated(null);
    setPreview(null);
    setFileName(file.name);
    setBusy(true);
    try {
      setPreview(await api.products.preview(file));
    } catch (caught) {
      setFailure(humanMessage(caught));
      setFileName(null);
    } finally {
      setBusy(false);
      // Cleared so choosing the *same* file again still fires `change`.
      if (input.current) input.current.value = "";
    }
  }

  async function confirm() {
    if (!preview) return;
    setBusy(true);
    setFailure(null);
    try {
      const result = await api.products.confirmImport(preview.preview_id);
      setCreated(result.created);
      setPreview(null);
      setFileName(null);
      await onImported();
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const invalid = preview?.invalid_rows ?? [];

  return (
    <div className="space-y-4 rounded-lg border border-line p-4">
      <div>
        <p className="text-sm font-medium text-ink">¿Tienes tu lista en Excel?</p>
        <p className="mt-0.5 text-xs text-ink-faint">
          Sube el archivo y te mostramos qué se va a crear antes de crear nada. Acepta .csv y
          .xlsx con columnas <span className="font-mono">nombre</span>,{" "}
          <span className="font-mono">precio</span> y, si quieres,{" "}
          <span className="font-mono">descripcion</span>, <span className="font-mono">stock</span>{" "}
          y <span className="font-mono">categoria</span>.
        </p>
      </div>

      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      {created !== null ? (
        <Alert tone="success" title={`Se crearon ${created} productos`}>
          Ya están disponibles para tu agente.
        </Alert>
      ) : null}

      <input
        ref={input}
        type="file"
        accept={ACCEPTED}
        onChange={pick}
        className="sr-only"
        id="catalog-import"
      />
      <label
        htmlFor="catalog-import"
        className={cx(
          "inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-line-strong px-4 text-sm font-semibold text-ink",
          "hover:bg-surface-muted",
          busy && "pointer-events-none opacity-50",
        )}
      >
        {fileName ?? "Elegir archivo"}
      </label>

      {preview ? (
        <div className="space-y-3 rounded-lg bg-surface-muted p-3">
          <p className="text-sm font-medium text-ink">
            Encontramos <span className="tabular">{preview.valid_rows}</span>{" "}
            {preview.valid_rows === 1 ? "producto válido" : "productos válidos"}
            {invalid.length > 0 ? (
              <>
                {" y "}
                <span className="tabular">{invalid.length}</span>{" "}
                {invalid.length === 1 ? "fila con problemas" : "filas con problemas"}
              </>
            ) : null}
            .
          </p>

          {invalid.length > 0 ? (
            <div className="rounded-lg border border-warning/30 bg-warning-soft p-3">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-ink">
                <AlertIcon className="size-3.5 text-warning" />
                Estas filas se van a saltar
              </p>
              {/* The row number and the reason, both. "Some rows failed" sends
               * somebody hunting through four hundred lines. */}
              <ul className="mt-2 space-y-1 text-xs text-ink-soft">
                {invalid.slice(0, PREVIEW_ERRORS_SHOWN).map((row) => (
                  <li key={row.row}>
                    <span className="tabular font-semibold">Fila {row.row}:</span> {row.reason}
                  </li>
                ))}
              </ul>
              {invalid.length > PREVIEW_ERRORS_SHOWN ? (
                <p className="mt-2 text-xs text-ink-faint">
                  y {invalid.length - PREVIEW_ERRORS_SHOWN} más.
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button onClick={confirm} loading={busy} disabled={preview.valid_rows === 0}>
              Crear {preview.valid_rows}{" "}
              {preview.valid_rows === 1 ? "producto" : "productos"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setPreview(null);
                setFileName(null);
              }}
            >
              Cancelar
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
