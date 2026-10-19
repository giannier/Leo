"use client";

/**
 * Contacts for campaigns (Feature 011, HU-02): who agreed to promotions and how, their
 * tags, and importing a list the owner declares consented (at most 200 a week).
 */

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, Checkbox, Field, Select, Skeleton, cx } from "@/components/ui";
import { api, type ContactPage, type ImportInfo, type ImportResult, type TagCount } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const PERMISSION: Record<string, { label: string; tone: string }> = {
  granted: { label: "Acepta promociones", tone: "bg-success-soft text-success" },
  asked: { label: "Preguntado", tone: "bg-info-soft text-info" },
  declined: { label: "No quiere", tone: "bg-surface-muted text-ink-soft" },
  revoked: { label: "Se dio de baja", tone: "bg-danger-soft text-danger" },
};
const ORIGIN: Record<string, string> = { conversation: "por WhatsApp", import: "importado", form: "formulario" };

function TagEditor({ id, tags, onSaved }: { id: string; tags: string[]; onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function save(next: string[]) {
    setError(null);
    try {
      await api.contacts.setTags(id, next);
      onSaved();
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
      {tags.map((tag) => (
        <span key={tag} className="inline-flex items-center gap-1 rounded-full bg-surface-muted px-2 py-0.5 text-xs text-ink">
          {tag}
          <button
            type="button"
            aria-label={`Quitar etiqueta ${tag}`}
            onClick={() => void save(tags.filter((t) => t !== tag))}
            className="cursor-pointer text-ink-faint hover:text-danger"
          >
            ×
          </button>
        </span>
      ))}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const tag = value.trim().toLowerCase();
          if (!tag) return;
          setValue("");
          void save([...new Set([...tags, tag])]);
        }}
      >
        <input
          aria-label="Nueva etiqueta"
          value={value}
          maxLength={30}
          onChange={(e) => setValue(e.target.value)}
          placeholder="+ etiqueta"
          className="h-8 w-24 rounded-md border border-line bg-surface px-2 text-sm text-ink"
        />
      </form>
      {error ? <span className="text-xs text-danger">{error}</span> : null}
    </div>
  );
}

function Importer({ onDone }: { onDone: () => void }) {
  const [info, setInfo] = useState<ImportInfo | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [tags, setTags] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.contacts.importInfo().then(setInfo).catch(() => setInfo(null));
  }, [result]);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.contacts.importCsv(file, accepted, tags));
      setFile(null);
      setAccepted(false);
      onDone();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Importar contactos que ya aceptaron"
      description="Un archivo CSV con la columna 'telefono' (y opcional 'nombre'). Solo personas que te dieron permiso."
    >
      <div className="space-y-4 text-sm">
        {info ? (
          <p className="text-ink-soft">
            Puedes importar {info.disponibles} de {info.limite_semanal} contactos esta semana.
          </p>
        ) : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {result ? (
          <Alert tone="success" title={`${result.importados} contacto(s) importados`}>
            {result.ya_tenian_permiso ? `${result.ya_tenian_permiso} ya tenían permiso. ` : ""}
            {result.rechazados ? `${result.rechazados} se omitieron porque dijeron que no o se dieron de baja. ` : ""}
            {result.invalidos.length ? `${result.invalidos.length} fila(s) con teléfono no válido.` : ""}
          </Alert>
        ) : null}
        <label className="flex flex-col gap-1.5 font-medium text-ink">
          Archivo
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm"
          />
        </label>
        <Field label="Etiquetas para estos contactos (opcional)" value={tags} onChange={(e) => setTags(e.target.value)} hint="Separadas por coma, ej.: feria, mayoristas" />
        <Checkbox
          checked={accepted}
          onChange={(e) => setAccepted(e.target.checked)}
          label="Declaro que estas personas aceptaron recibir mensajes de mi negocio por WhatsApp"
          hint={info?.declaracion}
        />
        <Button type="button" loading={busy} disabled={!file || !accepted} onClick={() => void upload()}>
          Importar
        </Button>
      </div>
    </Card>
  );
}

export function ContactsPanel({ canImport }: { canImport: boolean }) {
  const [page, setPage] = useState<ContactPage | null>(null);
  const [tags, setTags] = useState<TagCount[]>([]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [permission, setPermission] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    try {
      const [list, all] = await Promise.all([
        api.contacts.list({ q: query || undefined, tag: tag || undefined, permiso: permission || undefined }),
        api.contacts.tags(),
      ]);
      setPage(list);
      setTags(all);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [query, tag, permission]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-5">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Alert tone="info">
        Las campañas solo llegan a quien aceptó recibir promociones. Leo se lo pregunta a tus clientes con un botón
        después de una venta confirmada (puedes apagarlo en Ajustes).
      </Alert>
      {page ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-line bg-surface p-4">
            <p className="text-sm text-ink-soft">Aceptan promociones</p>
            <p className="font-heading text-2xl font-semibold text-ink" data-testid="optin-count">
              {page.con_permiso}
            </p>
          </div>
          <div className="rounded-xl border border-line bg-surface p-4">
            <p className="text-sm text-ink-soft">Contactos</p>
            <p className="font-heading text-2xl font-semibold text-ink">{page.total}</p>
          </div>
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Buscar" value={search} placeholder="Nombre o teléfono" onChange={(e) => setSearch(e.target.value)} />
        <Select
          label="Permiso"
          value={permission}
          onChange={(e) => setPermission(e.target.value)}
          options={[
            { value: "", label: "Todos" },
            { value: "granted", label: "Aceptan promociones" },
            { value: "none", label: "Sin preguntar" },
            { value: "declined", label: "No quieren" },
            { value: "revoked", label: "Se dieron de baja" },
          ]}
        />
        <Select
          label="Etiqueta"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          options={[{ value: "", label: "Todas" }, ...tags.map((t) => ({ value: t.tag, label: `${t.tag} (${t.total})` }))]}
        />
      </div>
      {!page ? (
        <Skeleton className="h-40 w-full" />
      ) : page.items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-ink-soft">
          No hay contactos con este filtro.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line bg-surface" data-testid="contact-list">
          {page.items.map((contact) => {
            const state = contact.permiso ? PERMISSION[contact.permiso] : null;
            return (
              <li key={contact.id} className="flex min-w-0 flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="truncate font-medium text-ink">{contact.nombre ?? contact.telefono ?? "Sin nombre"}</p>
                  <p className="text-xs text-ink-faint">
                    {contact.telefono}
                    {contact.baja ? " · escribió BAJA" : ""}
                  </p>
                </div>
                <div className="flex min-w-0 flex-col gap-1.5 sm:items-end">
                  <span className={cx("w-fit rounded-full px-2.5 py-0.5 text-xs font-semibold", state?.tone ?? "bg-surface-muted text-ink-faint")}>
                    {state?.label ?? "Sin preguntar"}
                    {contact.permiso === "granted" && contact.permiso_origen ? ` · ${ORIGIN[contact.permiso_origen] ?? ""}` : ""}
                  </span>
                  <TagEditor id={contact.id} tags={contact.etiquetas} onSaved={() => void load()} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {canImport ? <Importer onDone={() => void load()} /> : null}
    </div>
  );
}
