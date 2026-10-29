"use client";

/**
 * One business, and the single action an operator can take on it.
 *
 * Extending a trial is a commercial concession, so it asks for a reason and
 * records both in the audit log (RF-012). A trial extended without one is
 * indistinguishable, months later, from an account somebody quietly kept
 * free - and this is the only surface where that decision is ever visible,
 * because the customer never sees it.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, Field, Skeleton, TextArea } from "@/components/ui";
import { api, type AdminTenantDetail } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

/** Mirrors `MAX_EXTENSION_DAYS` in `app/api/v1/platform_admin.py`. */
const MAX_DAYS = 90;

const STATUS_LABELS: Record<string, string> = {
  trial: "En prueba",
  active: "Pagando",
  paused: "Pausado",
  archived: "Archivado",
};

export default function AdminTenantPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [tenant, setTenant] = useState<AdminTenantDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setTenant(await api.admin.tenant(id));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <Alert tone="danger">{error}</Alert>;
  if (tenant === null) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-5">
      <div>
        <Link href="/admin/tenants" className="text-sm text-accent hover:underline">
          Todos los negocios
        </Link>
        <h1 className="mt-2 font-heading text-2xl font-semibold text-ink">{tenant.name}</h1>
        <p className="mt-1 text-sm text-ink-soft">{tenant.owner_email ?? "Sin dueno asignado"}</p>
      </div>

      <Card title="Estado de la cuenta">
        <dl className="grid gap-4 sm:grid-cols-2">
          <Stat label="Estado" value={STATUS_LABELS[tenant.status] ?? tenant.status} />
          <Stat
            label="La prueba vence"
            value={
              tenant.trial_ends_at
                ? new Date(tenant.trial_ends_at).toLocaleDateString("es-PE", {
                    day: "numeric",
                    month: "long",
                    year: "numeric",
                  })
                : "-"
            }
          />
          <Stat label="Personas en el equipo" value={String(tenant.members)} />
          <Stat label="Productos" value={String(tenant.products)} />
          <Stat
            label="Conocimiento"
            value={`${tenant.knowledge_chars.toLocaleString("es-PE")} caracteres`}
          />
          <Stat label="Numero conectado" value={tenant.has_number ? "Si" : "No"} />
          <Stat label="Rubro" value={tenant.industry ?? "Sin rubro"} />
        </dl>
      </Card>

      <SalesProcess id={id} current={tenant.process_template_key ?? null} onChanged={load} />

      <ExtendTrial id={id} onExtended={load} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-ink-faint">{label}</dt>
      <dd className="tabular mt-0.5 text-sm font-medium text-ink">{value}</dd>
    </div>
  );
}

/** Feature 020: the universal process stays hidden until it passes the real-model evals; the
 * operator moves one business at a time, audited, and can move it back. */
function SalesProcess({
  id,
  current,
  onChanged,
}: {
  id: string;
  current: string | null;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const universal = current === "universal";

  async function move(to: boolean) {
    setBusy(true);
    setResult(null);
    try {
      const { template_key } = await api.admin.setUniversalProcess(id, to);
      setResult({ tone: "success", text: `Ahora usa el proceso «${template_key}».` });
      await onChanged();
    } catch (caught) {
      setResult({ tone: "danger", text: humanMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Proceso de venta"
      description="El proceso universal vende todo el catálogo con un solo proceso. Está oculto para los negocios hasta pasar los evals con el modelo real; muévelos de a uno. Queda en el registro de auditoría."
    >
      <div className="space-y-3">
        {result ? <Alert tone={result.tone}>{result.text}</Alert> : null}
        <p className="text-sm text-ink">
          Proceso actual: <span className="font-semibold">{current ?? "sin configurar"}</span>
        </p>
        <Button
          type="button"
          variant={universal ? "secondary" : "primary"}
          loading={busy}
          onClick={() => {
            const question = universal
              ? "¿Volver a las plantillas de antes? Las conversaciones abiertas siguen con su pedido."
              : "¿Pasar este negocio al proceso universal? Sus conversaciones abiertas siguen con su pedido.";
            if (window.confirm(question)) void move(!universal);
          }}
        >
          {universal ? "Volver a las plantillas" : "Pasar al proceso universal"}
        </Button>
      </div>
    </Card>
  );
}

function ExtendTrial({ id, onExtended }: { id: string; onExtended: () => Promise<void> }) {
  const [days, setDays] = useState("7");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      await api.admin.extendTrial(id, Number(days), reason.trim());
      setResult({ tone: "success", text: `Prueba extendida ${days} dias.` });
      setReason("");
      await onExtended();
    } catch (caught) {
      setResult({ tone: "danger", text: humanMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  const valid = reason.trim().length >= 3 && Number(days) >= 1 && Number(days) <= MAX_DAYS;

  return (
    <Card
      title="Extender la prueba"
      description="Queda en el registro de auditoria, con el motivo y con tu nombre."
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        {result ? <Alert tone={result.tone}>{result.text}</Alert> : null}

        <Field
          label="Dias"
          name="days"
          inputMode="numeric"
          required
          hint={`Hasta ${MAX_DAYS}. Si la prueba ya vencio, cuenta desde hoy.`}
          value={days}
          onChange={(e) => setDays(e.target.value.replace(/\D/g, "").slice(0, 3))}
        />

        <TextArea
          label="Motivo"
          name="reason"
          rows={2}
          required
          hint="Para poder responder despues por que esta cuenta tuvo mas tiempo."
          placeholder="Demora nuestra en conectar el numero."
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />

        <Button type="submit" loading={busy} disabled={!valid}>
          Extender
        </Button>
      </form>
    </Card>
  );
}
