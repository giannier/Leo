"use client";

/**
 * Every business on the platform.
 *
 * The question an operator almost always has here is "is this account
 * healthy?", so each row leads with what answers it - when the trial ends and
 * what state the account is in - rather than with identifiers.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Alert, Field, Skeleton, cx } from "@/components/ui";
import { api, type AdminTenant } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const STATUS_LABELS: Record<string, string> = {
  trial: "En prueba",
  active: "Pagando",
  paused: "Pausado",
  archived: "Archivado",
};

const STATUS_STYLES: Record<string, string> = {
  trial: "bg-info-soft text-info",
  active: "bg-success-soft text-success",
  paused: "bg-warning-soft text-warning",
  archived: "bg-surface-muted text-ink-faint",
};

export default function AdminTenantsPage() {
  const [search, setSearch] = useState("");
  const [tenants, setTenants] = useState<AdminTenant[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (term: string) => {
    setError(null);
    try {
      setTenants(await api.admin.tenants(term ? { search: term } : undefined));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  // Debounced, so typing a name is one request rather than one per letter.
  useEffect(() => {
    const timer = setTimeout(() => void load(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search, load]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Negocios</h1>
        <p className="mt-1 text-sm text-ink-soft">Todas las cuentas de la plataforma.</p>
      </header>

      <Field
        label="Buscar por nombre"
        name="search"
        type="search"
        placeholder="Panaderia..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {error ? <Alert tone="danger">{error}</Alert> : null}
      {tenants === null && !error ? <Skeleton className="h-40 w-full" /> : null}

      {tenants?.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line-strong p-10 text-center text-sm text-ink-faint">
          Ningun negocio coincide con esa busqueda.
        </p>
      ) : null}

      {tenants && tenants.length > 0 ? (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
          {tenants.map((tenant) => (
            <li key={tenant.id}>
              <Link
                href={`/admin/tenants/${tenant.id}`}
                className="flex min-h-16 flex-wrap items-center gap-3 p-4 hover:bg-surface-muted"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{tenant.name}</p>
                  <p className="text-xs text-ink-faint">
                    Desde el{" "}
                    {new Date(tenant.created_at).toLocaleDateString("es-PE", {
                      day: "numeric",
                      month: "short",
                      year: "numeric",
                    })}
                  </p>
                </div>

                {tenant.trial_ends_at ? (
                  <span className="tabular shrink-0 text-xs text-ink-soft">
                    vence{" "}
                    {new Date(tenant.trial_ends_at).toLocaleDateString("es-PE", {
                      day: "numeric",
                      month: "short",
                    })}
                  </span>
                ) : null}

                <span
                  className={cx(
                    "shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold",
                    STATUS_STYLES[tenant.status] ?? STATUS_STYLES.archived,
                  )}
                >
                  {STATUS_LABELS[tenant.status] ?? tenant.status}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
