"use client";

/**
 * The connection queue - the operator's daily work.
 *
 * In v1 a person does the provider work by hand (Clarification 007), so this
 * screen is the seam between a customer waiting and Giannier in the YCloud
 * console. Two things it has to do well:
 *
 * **Refusing has to be as easy as accepting**, and it has to carry a reason.
 * A rejection the customer cannot act on becomes a support ticket asking what
 * to change, so the reason field is required and the copy says who reads it.
 *
 * **Credentials go in and never come back out.** There is no endpoint that
 * returns them decrypted, so this form is the only place they are ever
 * visible - typed once, sent, gone.
 */

import { useCallback, useEffect, useState } from "react";

import { ChannelHealthCard } from "@/components/admin/channel-health";
import { AlertIcon, CheckIcon, ClockIcon, PhoneIcon } from "@/components/icons";
import { Alert, Button, Card, Field, Skeleton, TextArea, cx } from "@/components/ui";
import { api, type AdminRequest } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const FILTERS = [
  { value: "", label: "Todas" },
  { value: "requested", label: "Nuevas" },
  { value: "in_progress", label: "En trámite" },
  { value: "connected", label: "Conectadas" },
  { value: "rejected", label: "Rechazadas" },
] as const;

const STATUS_LABELS: Record<string, string> = {
  requested: "Nueva",
  in_progress: "En trámite",
  connected: "Conectada",
  rejected: "Rechazada",
};

const STATUS_STYLES: Record<string, string> = {
  requested: "bg-info-soft text-info",
  in_progress: "bg-warning-soft text-warning",
  connected: "bg-success-soft text-success",
  rejected: "bg-danger-soft text-danger",
};

export default function AdminQueuePage() {
  const [filter, setFilter] = useState<string>("requested");
  const [requests, setRequests] = useState<AdminRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRequests(await api.admin.queue(filter || undefined));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Solicitudes de conexión</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Cada una es un negocio esperando. Las más viejas van primero.
        </p>
      </header>

      <ChannelHealthCard />

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setFilter(option.value)}
            aria-pressed={filter === option.value}
            className={cx(
              "min-h-11 cursor-pointer rounded-full px-3.5 text-sm transition-colors duration-150",
              filter === option.value
                ? "bg-brand font-semibold text-brand-fg"
                : "border border-line-strong text-ink-soft hover:bg-surface-muted",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>

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

      {requests === null && !error ? <Skeleton className="h-40 w-full" /> : null}

      {requests?.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line-strong p-10 text-center">
          <CheckIcon className="mx-auto size-8 text-success" />
          <p className="mt-2 text-sm font-medium text-ink">Nada pendiente aquí</p>
          <p className="mt-1 text-xs text-ink-faint">
            {filter === "requested"
              ? "No hay solicitudes nuevas esperando."
              : "Ninguna solicitud con este estado."}
          </p>
        </div>
      ) : null}

      <div className="space-y-4">
        {requests?.map((request) => (
          <RequestCard key={request.id} request={request} onChanged={load} />
        ))}
      </div>
    </div>
  );
}

function RequestCard({
  request,
  onChanged,
}: {
  request: AdminRequest;
  onChanged: () => Promise<void>;
}) {
  const [action, setAction] = useState<"connect" | "reject" | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const open = request.status === "requested" || request.status === "in_progress";

  async function move(body: Parameters<typeof api.admin.transition>[1]) {
    setBusy(true);
    setFailure(null);
    try {
      await api.admin.transition(request.id, body);
      setAction(null);
      await onChanged();
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="tabular text-lg font-semibold text-ink">{request.phone_e164}</p>
          <p className="text-sm text-ink-soft">{request.display_name}</p>
          <p className="mt-1 text-xs text-ink-faint">
            Pedida el{" "}
            {new Date(request.created_at).toLocaleDateString("es-PE", {
              day: "numeric",
              month: "long",
              year: "numeric",
            })}
          </p>
        </div>
        <span
          className={cx(
            "shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold",
            STATUS_STYLES[request.status] ?? STATUS_STYLES.requested,
          )}
        >
          {STATUS_LABELS[request.status] ?? request.status}
        </span>
      </div>

      {request.business_description ? (
        <p className="mt-3 rounded-lg bg-surface-muted p-3 text-sm text-ink-soft">
          {request.business_description}
        </p>
      ) : null}

      {request.rejection_reason ? (
        <p className="mt-3 flex items-start gap-1.5 text-sm text-danger">
          <AlertIcon className="mt-0.5 size-4 shrink-0" />
          {request.rejection_reason}
        </p>
      ) : null}

      {failure ? (
        <div className="mt-3">
          <Alert tone="danger">{failure}</Alert>
        </div>
      ) : null}

      {open && action === null ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {request.status === "requested" ? (
            <Button
              variant="secondary"
              loading={busy}
              onClick={() => void move({ status: "in_progress" })}
            >
              <ClockIcon className="size-4" />
              Tomar
            </Button>
          ) : null}
          <Button onClick={() => setAction("connect")}>
            <PhoneIcon className="size-4" />
            Conectar
          </Button>
          <Button variant="ghost" onClick={() => setAction("reject")}>
            Rechazar
          </Button>
        </div>
      ) : null}

      {action === "connect" ? (
        <ConnectForm busy={busy} onCancel={() => setAction(null)} onSubmit={move} />
      ) : null}

      {action === "reject" ? (
        <RejectForm busy={busy} onCancel={() => setAction(null)} onSubmit={move} />
      ) : null}
    </Card>
  );
}

/* --------------------------------------------------------------- connect */

function ConnectForm({
  busy,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  onCancel: () => void;
  onSubmit: (body: Parameters<typeof api.admin.transition>[1]) => Promise<void>;
}) {
  const [waba, setWaba] = useState("");
  const [phoneId, setPhoneId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [notes, setNotes] = useState("");

  const complete = waba.trim() && phoneId.trim() && apiKey.trim();

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void onSubmit({
          status: "connected",
          admin_notes: notes.trim() || null,
          credentials: {
            provider: "ycloud",
            values: {
              waba_id: waba.trim(),
              phone_number_id: phoneId.trim(),
              api_key: apiKey.trim(),
            },
          },
        });
      }}
      className="mt-4 space-y-4 rounded-lg border border-line bg-surface-muted p-4"
    >
      <div>
        <p className="text-sm font-medium text-ink">Credenciales de YCloud</p>
        <p className="mt-0.5 text-xs text-ink-faint">
          Se guardan cifradas y no vuelven a mostrarse nunca: no hay pantalla que las devuelva.
          Si te equivocas, hay que volver a cargarlas.
        </p>
      </div>

      <Field
        label="WABA ID"
        name="waba_id"
        required
        autoComplete="off"
        spellCheck={false}
        value={waba}
        onChange={(e) => setWaba(e.target.value)}
      />
      <Field
        label="Phone number ID"
        name="phone_number_id"
        required
        autoComplete="off"
        spellCheck={false}
        value={phoneId}
        onChange={(e) => setPhoneId(e.target.value)}
      />
      <Field
        label="API key"
        name="api_key"
        // A password field, so it is masked on a screen somebody may be
        // sharing, and never offered to a password manager.
        type="password"
        required
        autoComplete="off"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
      />
      <TextArea
        label="Notas internas"
        name="admin_notes"
        rows={2}
        hint="Solo las ves tú. El cliente nunca las lee."
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
      />

      <div className="flex flex-wrap gap-2">
        <Button type="submit" loading={busy} disabled={!complete}>
          Conectar el número
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

/* ---------------------------------------------------------------- reject */

function RejectForm({
  busy,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  onCancel: () => void;
  onSubmit: (body: Parameters<typeof api.admin.transition>[1]) => Promise<void>;
}) {
  const [reason, setReason] = useState("");

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void onSubmit({ status: "rejected", rejection_reason: reason.trim() });
      }}
      className="mt-4 space-y-4 rounded-lg border border-danger/25 bg-danger-soft p-4"
    >
      <TextArea
        label="¿Por qué la rechazas?"
        name="rejection_reason"
        rows={3}
        required
        // The customer reads this verbatim and re-files against it. A reason
        // they cannot act on just becomes a support ticket asking what to fix.
        hint="El cliente lee esto tal cual. Dile qué corregir para que pueda reenviarla."
        placeholder="El nombre no cumple las políticas de Meta: no puede incluir la palabra 'oficial'."
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />

      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="danger" loading={busy} disabled={reason.trim().length < 3}>
          Rechazar solicitud
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}
