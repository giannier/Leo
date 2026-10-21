"use client";

/**
 * Mi número de WhatsApp (HU-04 and HU-05).
 *
 * Each business connects its own YCloud account by pasting its API key: Leo checks
 * the key, creates the webhook in that account and keeps the secret it returns.
 * Nobody on the platform's side has to do anything by hand. Requests filed before
 * this existed still show their progress and history below.
 *
 * The activation switch lives here too, next to the number it switches on.
 * Splitting them would mean somebody connects a number, sees nothing happen,
 * and goes looking for a toggle on another screen.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { SocialLinks } from "@/components/channel/social-links";
import { WebChatCard } from "@/components/channel/web-chat-card";
import { AlertIcon, CheckIcon, ClockIcon, PhoneIcon } from "@/components/icons";
import { PhoneField, isCompletePhone } from "@/components/phone-field";
import { Alert, Button, Card, Field, Skeleton, cx } from "@/components/ui";
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
        <ConnectForm
          canConfigure={canConfigure}
          onConnected={load}
        />
      ) : null}

      {requests && requests.length > 0 ? <History requests={requests} /> : null}

      {canConfigure ? <WebChatCard /> : null}
      {canConfigure ? <SocialLinks phone={number?.phone_e164 ?? null} /> : null}
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

function ConnectForm({
  canConfigure,
  onConnected,
}: {
  canConfigure: boolean;
  onConnected: () => Promise<void>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [phone, setPhone] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [errors, setErrors] = useState<{ apiKey?: string; phone?: string; displayName?: string }>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!canConfigure) {
    return (
      <Alert tone="info" title="Solo lectura">
        Tu rol no puede conectar el número. Pídeselo al dueño de la cuenta.
      </Alert>
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);

    const found: typeof errors = {};
    if (apiKey.trim().length < 10) found.apiKey = "Pega la clave API completa de YCloud.";
    if (!isCompletePhone(phone)) found.phone = "Elige el país y escribe el número completo.";
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
      await api.connection.connect({
        api_key: apiKey.trim(),
        phone_e164: phone,
        display_name: displayName.trim(),
      });
      setApiKey("");
      await onConnected();
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "phone_already_connected") {
        setErrors({ phone: "Ese número ya está conectado. Escríbenos si es tuyo." });
      } else if (caught instanceof ApiError && caught.code === "invalid_api_key") {
        setErrors({ apiKey: "YCloud no aceptó esa clave. Cópiala de nuevo en Desarrolladores → Clave API." });
      } else if (caught instanceof ApiError && caught.code === "number_not_in_account") {
        setErrors({ phone: "Ese número no está en la cuenta de YCloud de esa clave." });
      } else {
        setFailure(humanMessage(caught));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Conecta tu número de WhatsApp"
      description="Usas tu propia cuenta de YCloud. Tú pegas la clave y nosotros creamos el webhook por ti: no tienes que copiar ninguna dirección."
    >
      <ol className="mb-4 list-decimal space-y-1 rounded-lg bg-surface-muted p-3 pl-7 text-xs text-ink-soft">
        <li>En YCloud, entra a Desarrolladores → Clave API y copia tu clave.</li>
        <li>Pégala aquí con el número de WhatsApp que ya tienes conectado en esa cuenta.</li>
        <li>Listo: Leo verifica la clave, crea el webhook en tu cuenta y empieza a recibir tus mensajes.</li>
      </ol>

      <form onSubmit={submit} noValidate className="space-y-4">
        {failure ? <Alert tone="danger">{failure}</Alert> : null}

        <Field
          label="Clave API de YCloud"
          name="api_key"
          type="password"
          autoComplete="off"
          required
          hint="Se guarda cifrada y no se vuelve a mostrar."
          value={apiKey}
          error={errors.apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />

        <PhoneField
          label="Número de WhatsApp"
          name="phone_e164"
          required
          hint="El número tal como aparece en tu cuenta de YCloud."
          value={phone}
          error={errors.phone}
          onChange={setPhone}
        />

        <Field
          label="Nombre que verán tus clientes"
          name="display_name"
          required
          maxLength={MAX_DISPLAY_NAME}
          hint={`Hasta ${MAX_DISPLAY_NAME} caracteres. Es el nombre de tu negocio en Leo.`}
          value={displayName}
          error={errors.displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />

        <Button type="submit" loading={busy}>
          Conectar mi número
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
