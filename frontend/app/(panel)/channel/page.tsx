"use client";

/**
 * Mi número de WhatsApp (HU-04 and HU-05).
 *
 * Connecting a number is the one step of onboarding that a person does, not
 * software: Giannier does the YCloud work by hand (Clarification 007). So the
 * screen's real job is to make waiting bearable - show exactly which stage the
 * request is in, and when it is refused, say why and let them fix it and file
 * again in one move.
 *
 * The activation switch lives here too, next to the number it switches on.
 * Splitting them would mean somebody connects a number, sees nothing happen,
 * and goes looking for a toggle on another screen.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AlertIcon, CheckIcon, ClockIcon, PhoneIcon } from "@/components/icons";
import { Alert, Button, Card, Field, Skeleton, TextArea, cx } from "@/components/ui";
import {
  api,
  type ConnectionRequest,
  type Checklist,
  type WhatsAppNumber,
} from "@/lib/api/client";
import { ApiError, humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

/** WhatsApp caps the display name at 25 characters. */
const MAX_DISPLAY_NAME = 25;
const E164 = /^\+[1-9]\d{7,14}$/;

const STAGES = [
  { key: "requested", label: "Recibida", what: "Tenemos tu solicitud en la cola." },
  { key: "in_progress", label: "En trámite", what: "Estamos gestionando tu número con el proveedor." },
  { key: "connected", label: "Conectado", what: "Tu número está listo para atender." },
] as const;

export default function ChannelPage() {
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);

  const [number, setNumber] = useState<WhatsAppNumber | null>(null);
  const [requests, setRequests] = useState<ConnectionRequest[] | null>(null);
  const [checklist, setChecklist] = useState<Checklist | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [live, history, list] = await Promise.all([
        api.connection.number(),
        api.connection.list(),
        api.tenant.checklist(),
      ]);
      setNumber(live);
      setRequests(history);
      setChecklist(list);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const open = requests?.find((r) => r.status === "requested" || r.status === "in_progress");
  const lastRejected = requests?.find((r) => r.status === "rejected");

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Mi número de WhatsApp</h1>
        <p className="mt-1 text-sm text-ink-soft">
          El número por el que tu agente va a atender a tus clientes.
        </p>
      </header>

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

      {number ? (
        <>
          <ConnectedNumber number={number} />
          {checklist ? (
            <ActivationCard checklist={checklist} canConfigure={canConfigure} number={number} />
          ) : null}
        </>
      ) : null}

      {!number && open ? <Progress request={open} /> : null}

      {!number && !open && requests !== null ? (
        <RequestForm
          canConfigure={canConfigure}
          rejected={lastRejected}
          phoneVerified={
            checklist?.items.find((item) => item.key === "phone_verified")?.done ?? false
          }
          onFiled={load}
        />
      ) : null}

      {requests && requests.length > 0 ? <History requests={requests} /> : null}
    </div>
  );
}

/* ------------------------------------------------------------- connected */

function ConnectedNumber({ number }: { number: WhatsAppNumber }) {
  return (
    <Card>
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-success-soft text-success">
          <CheckIcon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink">Número conectado</p>
          <p className="tabular mt-0.5 text-lg font-semibold text-ink">{number.phone_e164}</p>
          <p className="mt-0.5 text-sm text-ink-soft">{number.display_name}</p>
          {number.quality_rating ? (
            <p className="mt-2 text-xs text-ink-faint">
              Calidad según WhatsApp: {number.quality_rating}
            </p>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------ activation */

function ActivationCard({
  checklist,
  canConfigure,
  number,
}: {
  checklist: Checklist;
  canConfigure: boolean;
  number: WhatsAppNumber;
}) {
  const [active, setActive] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [justActivated, setJustActivated] = useState(false);

  async function toggle(on: boolean) {
    setBusy(true);
    setResult(null);
    try {
      if (on) {
        await api.agent.activate();
        setActive(true);
        setJustActivated(true);
      } else {
        await api.agent.deactivate();
        setActive(false);
        setJustActivated(false);
      }
    } catch (caught) {
      setResult({ tone: "danger", text: humanMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  if (!canConfigure) return null;

  return (
    <Card
      title="Encender el agente"
      description={
        checklist.activation_ready
          ? "Cuando lo enciendas, tu agente empieza a responder a tus clientes reales."
          : "Te falta completar la configuración antes de poder encenderlo."
      }
    >
      {result ? (
        <div className="mb-4">
          <Alert tone={result.tone}>{result.text}</Alert>
        </div>
      ) : null}

      {justActivated ? (
        // HU-05: "WHEN activo por primera vez, THEN veo una guía de test real".
        // The first thing anybody wants after switching it on is proof that it
        // works, and the only proof is a message from a real phone.
        <div className="mb-4">
          <Alert tone="success" title="Tu agente ya está atendiendo">
            <p className="mt-1">Pruébalo ahora mismo, en dos minutos:</p>
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>
                Agenda <span className="tabular font-semibold">{number.phone_e164}</span> en tu
                celular.
              </li>
              <li>Escríbele por WhatsApp como si fueras un cliente.</li>
              <li>
                Si algo no te gusta, apágalo aquí mismo y ajústalo en{" "}
                <Link href="/onboarding" className="font-semibold text-accent underline">
                  la configuración
                </Link>
                .
              </li>
            </ol>
          </Alert>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        {active === true ? (
          <Button variant="secondary" onClick={() => void toggle(false)} loading={busy}>
            Apagar el agente
          </Button>
        ) : (
          <Button
            onClick={() => void toggle(true)}
            loading={busy}
            disabled={!checklist.activation_ready}
          >
            Encender el agente
          </Button>
        )}

        {!checklist.activation_ready ? (
          <Link href="/onboarding?paso=6" className="text-sm font-semibold text-accent underline">
            Ver qué falta
          </Link>
        ) : null}
      </div>

      {/* The stop button is never hidden behind a confirmation. Somebody
       * pressing it is watching their agent say something wrong to a real
       * customer right now. */}
      {active === true ? (
        <p className="mt-3 text-xs text-ink-faint">
          Apagarlo es inmediato. Los mensajes que te escriban se siguen guardando en tu bandeja,
          solo que nadie responde automáticamente.
        </p>
      ) : null}
    </Card>
  );
}

/* -------------------------------------------------------------- progress */

function Progress({ request }: { request: ConnectionRequest }) {
  const current = STAGES.findIndex((stage) => stage.key === request.status);

  return (
    <Card title="Tu solicitud está en camino">
      <p className="tabular mb-4 text-sm text-ink-soft">{request.phone_e164}</p>

      <ol className="space-y-3">
        {STAGES.map((stage, index) => {
          const done = index < current;
          const here = index === current;
          return (
            <li key={stage.key} className="flex items-start gap-3">
              <span
                className={cx(
                  "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                  done
                    ? "bg-success text-white"
                    : here
                      ? "bg-accent text-accent-fg"
                      : "border border-line-strong text-ink-faint",
                )}
              >
                {done ? <CheckIcon className="size-3.5" /> : here ? <ClockIcon className="size-3.5" /> : index + 1}
              </span>
              <div>
                <p className={cx("text-sm font-medium", here ? "text-ink" : "text-ink-soft")}>
                  {stage.label}
                </p>
                {here ? <p className="text-xs text-ink-faint">{stage.what}</p> : null}
              </div>
            </li>
          );
        })}
      </ol>

      <p className="mt-4 rounded-lg bg-surface-muted p-3 text-xs text-ink-soft">
        Una persona de nuestro equipo gestiona esto con el proveedor. Te avisamos por correo
        apenas quede listo; no necesitas quedarte esperando aquí.
      </p>
    </Card>
  );
}

/* ------------------------------------------------------------------ form */

function RequestForm({
  canConfigure,
  rejected,
  phoneVerified,
  onFiled,
}: {
  canConfigure: boolean;
  rejected?: ConnectionRequest;
  phoneVerified: boolean;
  onFiled: () => Promise<void>;
}) {
  const [phone, setPhone] = useState(rejected?.phone_e164 ?? "+51");
  const [displayName, setDisplayName] = useState(rejected?.display_name ?? "");
  const [description, setDescription] = useState(rejected?.business_description ?? "");
  const [errors, setErrors] = useState<{ phone?: string; displayName?: string }>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!canConfigure) {
    return (
      <Alert tone="info" title="Solo lectura">
        Tu rol no puede pedir la conexión del número. Pídeselo al dueño de la cuenta.
      </Alert>
    );
  }

  if (!phoneVerified) {
    // Clarification 005's anti-abuse gate. Explained rather than enforced
    // silently: a disabled form with no reason is the worst version of this.
    return (
      <Alert tone="warning" title="Primero verifica tu teléfono">
        Antes de pedir un número necesitamos confirmar el teléfono de tu cuenta. Es un código de
        seis dígitos y toma un minuto.
        <div className="mt-3">
          <Link
            href="/verify-phone"
            className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
          >
            Verificar mi teléfono
          </Link>
        </div>
      </Alert>
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);

    const found: typeof errors = {};
    if (!E164.test(phone.trim())) found.phone = "Usa el formato +51987654321.";
    if (!displayName.trim()) found.displayName = "Escribe el nombre que verán tus clientes.";
    if (displayName.trim().length > MAX_DISPLAY_NAME) {
      found.displayName = `WhatsApp permite hasta ${MAX_DISPLAY_NAME} caracteres.`;
    }
    if (Object.keys(found).length > 0) {
      setErrors(found);
      return;
    }
    setErrors({});

    setBusy(true);
    try {
      await api.connection.create({
        phone_e164: phone.trim(),
        display_name: displayName.trim(),
        business_description: description.trim() || null,
        // Chains the retry back to what it replaces, so the admin sees what
        // was corrected (Clarification 012).
        previous_request_id: rejected?.id ?? null,
      });
      await onFiled();
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "phone_already_connected") {
        setErrors({ phone: "Ese número ya está conectado. Escríbenos si es tuyo." });
      } else {
        setFailure(humanMessage(caught));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title={rejected ? "Corrige y vuelve a enviar" : "Pide la conexión de tu número"}
      description={
        rejected
          ? undefined
          : "Una persona de nuestro equipo lo gestiona con el proveedor oficial. Suele tomar entre uno y tres días hábiles."
      }
    >
      {rejected ? (
        <div className="mb-4">
          <Alert tone="warning" title="Tu solicitud anterior fue rechazada">
            {rejected.rejection_reason ?? "No se indicó un motivo."}
          </Alert>
        </div>
      ) : null}

      <form onSubmit={submit} noValidate className="space-y-4">
        {failure ? <Alert tone="danger">{failure}</Alert> : null}

        <Field
          label="Número de WhatsApp"
          name="phone_e164"
          type="tel"
          inputMode="tel"
          required
          hint="Con código de país. Debe ser un número que no esté usando WhatsApp personal."
          value={phone}
          error={errors.phone}
          onChange={(e) => setPhone(e.target.value)}
        />

        <Field
          label="Nombre que verán tus clientes"
          name="display_name"
          required
          maxLength={MAX_DISPLAY_NAME}
          hint={`Hasta ${MAX_DISPLAY_NAME} caracteres. Es el nombre que aparece arriba del chat.`}
          value={displayName}
          error={errors.displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />

        <TextArea
          label="¿A qué se dedica tu negocio?"
          name="business_description"
          rows={3}
          hint="El proveedor lo pide para aprobar la cuenta. Opcional, pero acelera el trámite."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />

        <Button type="submit" loading={busy}>
          {rejected ? "Reenviar solicitud" : "Pedir la conexión"}
        </Button>
      </form>
    </Card>
  );
}

/* --------------------------------------------------------------- history */

function History({ requests }: { requests: ConnectionRequest[] }) {
  return (
    <Card title="Historial de solicitudes">
      <ul className="divide-y divide-line">
        {requests.map((request) => (
          <li key={request.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
            <PhoneIcon className="mt-0.5 size-4 shrink-0 text-ink-faint" />
            <div className="min-w-0 flex-1">
              <p className="tabular text-sm font-medium text-ink">{request.phone_e164}</p>
              <p className="text-xs text-ink-faint">
                {new Date(request.created_at).toLocaleDateString("es-PE", {
                  day: "numeric",
                  month: "long",
                  year: "numeric",
                })}
              </p>
              {request.rejection_reason ? (
                <p className="mt-1 flex items-start gap-1.5 text-xs text-danger">
                  <AlertIcon className="mt-0.5 size-3 shrink-0" />
                  {request.rejection_reason}
                </p>
              ) : null}
            </div>
            <StatusBadge status={request.status} />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    requested: "bg-surface-muted text-ink-soft",
    in_progress: "bg-info-soft text-info",
    connected: "bg-success-soft text-success",
    rejected: "bg-danger-soft text-danger",
  };
  const labels: Record<string, string> = {
    requested: "Recibida",
    in_progress: "En trámite",
    connected: "Conectado",
    rejected: "Rechazada",
  };
  return (
    <span
      className={cx(
        "shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold",
        styles[status] ?? styles.requested,
      )}
    >
      {labels[status] ?? status}
    </span>
  );
}
