"use client";

/**
 * The panel home: what is left to do before the agent can go live.
 *
 * The checklist is computed by the backend on every read, never stored
 * (Clarification 009), so this is always the truth rather than a cached
 * opinion. Each unfinished item deep-links to the screen that fixes it -
 * a list that tells you what is missing but not where to fix it just moves
 * the hunting to the user.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { CheckIcon, CircleIcon } from "@/components/icons";
import { Alert, Button, Card, Skeleton, cx } from "@/components/ui";
import { UsageMeter } from "@/components/usage-meter";
import { ActivationSteps, ProgressBar } from "@/components/assistant/overview";
import { api, type Checklist, type Overview } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole, useSession } from "@/lib/session";

/** Copy and destination for each checklist key the backend can return. */
const ITEMS: Record<string, { label: string; href: string; help: string }> = {
  email_verified: {
    label: "Confirmar tu correo",
    href: "/onboarding",
    help: "Abre el enlace que te enviamos al registrarte.",
  },
  business_profile_complete: {
    label: "Completar los datos de tu negocio",
    href: "/onboarding",
    help: "Nombre y rubro. Es lo que el agente usa para presentarse.",
  },
  agent_config_complete: {
    label: "Configurar la personalidad del agente",
    href: "/onboarding",
    help: "Cómo se llama, cómo habla y qué no debe hacer.",
  },
  has_active_product: {
    label: "Cargar al menos un producto o servicio",
    href: "/catalog",
    help: "Sin catálogo el agente no puede responder qué vendes.",
  },
  has_payment_method: {
    label: "Indicar cómo te pueden pagar",
    href: "/onboarding",
    help: "Yape, Plin, transferencia. El agente lo explica tal cual lo escribas.",
  },
  phone_verified: {
    label: "Verificar tu teléfono de contacto",
    href: "/verify-phone",
    help: "Lo pedimos antes de conectar tu número de WhatsApp.",
  },
};

export default function PanelHome() {
  const { user } = useSession();
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);

  const [checklist, setChecklist] = useState<Checklist | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setChecklist(await api.tenant.checklist());
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const firstName = user?.full_name?.split(" ")[0] ?? "";

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">
          {firstName ? `Hola, ${firstName}` : "Hola"}
        </h1>
        <p className="mt-1 text-sm text-ink-soft">
          Esto es lo que falta para que tu agente empiece a atender.
        </p>
      </header>

      {/* Above the checklist on purpose: once the agent is live, "how many
          messages have I got left" is the question somebody opens this page
          to answer, and the checklist below it is finished. */}
      <UsageMeter />

      {error ? (
        <Alert tone="danger" title="No pudimos cargar tu configuración">
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

      {!checklist && !error ? (
        <Card>
          <div className="space-y-3">
            <Skeleton className="h-5 w-48" />
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
          </div>
        </Card>
      ) : null}

      {checklist ? <ChecklistCard checklist={checklist} canConfigure={canConfigure} /> : null}

      {canConfigure ? <AssistantWidget /> : null}
    </div>
  );
}

/** Where the assistant's configuration stands (TASK-441): progress and the next step. */
function AssistantWidget() {
  const [overview, setOverview] = useState<Overview | null>(null);
  useEffect(() => {
    api.assistant
      .overview()
      .then(setOverview)
      .catch(() => setOverview(null));
  }, []);
  if (!overview) return null;
  return (
    <Card
      title="Tu asistente"
      footer={
        <Link href="/asistente" className="text-sm font-semibold text-accent">
          Ver todo
        </Link>
      }
    >
      <div className="space-y-4" data-testid="assistant-widget">
        <ProgressBar value={overview.progreso} />
        <ActivationSteps overview={overview} />
      </div>
    </Card>
  );
}

function ChecklistCard({
  checklist,
  canConfigure,
}: {
  checklist: Checklist;
  canConfigure: boolean;
}) {
  const required = checklist.items.filter((item) => item.required);
  const done = required.filter((item) => item.done).length;
  const total = required.length;
  const complete = done === total;

  return (
    <Card
      title="Lista de activación"
      description={
        complete
          ? "Todo listo. Tu agente puede salir a atender."
          : `${done} de ${total} pasos completos.`
      }
    >
      {/* A bar plus the numbers above it: the bar is scannable, the numbers
       * are what somebody reads out loud to support. */}
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-label={`${done} de ${total} pasos completos`}
        className="mb-5 h-2 w-full overflow-hidden rounded-full bg-surface-muted"
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-300"
          style={{ width: `${total === 0 ? 0 : (done / total) * 100}%` }}
        />
      </div>

      <ul className="divide-y divide-line">
        {checklist.items.map((item) => {
          const copy = ITEMS[item.key];
          if (!copy) return null;

          return (
            <li key={item.key} className="py-3 first:pt-0 last:pb-0">
              <div className="flex items-start gap-3">
                {/* Shape and colour together. A tick inside a filled circle
                 * versus an empty outline reads even in greyscale. */}
                <span
                  className={cx(
                    "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full",
                    item.done ? "bg-success text-white" : "text-line-strong",
                  )}
                >
                  {item.done ? <CheckIcon className="size-4" /> : <CircleIcon className="size-5" />}
                </span>

                <div className="min-w-0 flex-1">
                  <p
                    className={cx(
                      "text-sm font-medium",
                      item.done ? "text-ink-faint line-through" : "text-ink",
                    )}
                  >
                    {copy.label}
                    {!item.required ? (
                      <span className="ml-2 rounded-full bg-surface-muted px-2 py-0.5 text-xs font-normal text-ink-faint">
                        No aplica
                      </span>
                    ) : null}
                  </p>
                  {!item.done && item.required ? (
                    <p className="mt-0.5 text-xs text-ink-faint">{copy.help}</p>
                  ) : null}
                </div>

                {!item.done && item.required && canConfigure ? (
                  <Link
                    href={copy.href}
                    className="shrink-0 self-center rounded-lg border border-line-strong px-3 py-2 text-xs font-semibold text-ink hover:bg-surface-muted"
                  >
                    Completar
                  </Link>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>

      {checklist.sandbox_ready ? (
        <div className="mt-5 border-t border-line pt-4">
          <Alert tone="success" title="Ya puedes probar tu agente">
            <div className="mt-2">
              <Link
                href="/sandbox"
                className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
              >
                Abrir modo prueba
              </Link>
            </div>
          </Alert>
        </div>
      ) : null}

      {checklist.activation_ready && canConfigure ? (
        <div className="mt-4">
          <ActivateButton />
        </div>
      ) : null}
    </Card>
  );
}

function ActivateButton() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  async function activate() {
    setBusy(true);
    setResult(null);
    try {
      await api.agent.activate();
      setResult({ tone: "success", text: "Tu agente ya está atendiendo en WhatsApp." });
    } catch (caught) {
      setResult({ tone: "danger", text: humanMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {result ? <Alert tone={result.tone}>{result.text}</Alert> : null}
      <Button onClick={activate} loading={busy} fullWidth>
        Activar mi agente
      </Button>
    </div>
  );
}
