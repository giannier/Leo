"use client";

/**
 * Step 5: how the business gets paid.
 *
 * The instructions are quoted to the customer by the agent, near-verbatim, so
 * the placeholder shows a complete one rather than a label. "Yape" alone
 * leaves the buyer asking "¿a qué número?", which is the conversation the
 * agent was supposed to save.
 *
 * Only required for a business that sells - an agent that answers questions
 * has nothing to charge for (`SELLING_OBJECTIVES` in `app/services/
 * activation.py`), and the checklist marks the item not-required in that case.
 */

import { useCallback, useEffect, useState } from "react";

import { CloseIcon } from "@/components/icons";
import { Alert, Button, Checkbox, Select, Skeleton, TextArea } from "@/components/ui";
import { api, type PaymentMethod, type PaymentMethodBody } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { StepIntro, type StepProps } from "./shared";

/** Mirrors `PAYMENT_METHODS` in `app/models/business.py`. */
const METHODS = [
  { value: "yape", label: "Yape" },
  { value: "plin", label: "Plin" },
  { value: "transferencia", label: "Transferencia bancaria" },
  { value: "tarjeta", label: "Tarjeta" },
  { value: "efectivo", label: "Efectivo" },
  { value: "link", label: "Link de pago" },
] as const;

const PLACEHOLDERS: Record<string, string> = {
  yape: "Yape al 987 654 321 a nombre de Ana Quispe. Manda la captura por aquí.",
  plin: "Plin al 987 654 321 a nombre de Ana Quispe.",
  transferencia: "BCP soles 191-1234567-0-12, a nombre de Panadería Quispe SAC. RUC 20123456789.",
  tarjeta: "Aceptamos Visa y Mastercard en la tienda.",
  efectivo: "Pago en efectivo al recoger el pedido.",
  link: "Te enviamos un link de pago por aquí mismo.",
};

// Keyed on `string`, not the literal union: the API returns `method` as a
// plain string, and narrowing the map would force a cast at every lookup.
const LABELS = new Map<string, string>(METHODS.map((m) => [m.value, m.label]));

export function StepPayments({ onSaved }: StepProps) {
  const [methods, setMethods] = useState<PaymentMethod[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setMethods(await api.payments.list());
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
        Tu agente le va a repetir esto a tus clientes tal como lo escribas, así que sé concreto:
        número, nombre y qué debe hacer después de pagar.
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

      <MethodList methods={methods} onChanged={afterChange} />
      <AddMethod existing={methods ?? []} onAdded={afterChange} />
    </div>
  );
}

function MethodList({
  methods,
  onChanged,
}: {
  methods: PaymentMethod[] | null;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  if (methods === null) return <Skeleton className="h-16 w-full" />;
  if (methods.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-line-strong p-6 text-center">
        <p className="text-sm font-medium text-ink">Todavía no indicaste cómo te pagan</p>
        <p className="mt-1 text-xs text-ink-faint">
          Sin esto, tu agente no puede cerrar una venta.
        </p>
      </div>
    );
  }

  async function act(id: string, run: () => Promise<unknown>) {
    setBusy(id);
    try {
      await run();
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  return (
    <ul className="divide-y divide-line rounded-lg border border-line">
      {methods.map((method) => (
        <li key={method.id} className="space-y-2 p-3">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">
                {LABELS.get(method.method) ?? method.method}
              </p>
              <p className="mt-0.5 text-xs text-ink-soft">{method.instructions}</p>
            </div>
            <button
              type="button"
              onClick={() => void act(method.id, () => api.payments.remove(method.id))}
              disabled={busy === method.id}
              className="flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-lg text-ink-faint hover:bg-surface-muted hover:text-danger disabled:opacity-50"
              aria-label={`Quitar ${LABELS.get(method.method) ?? method.method}`}
            >
              <CloseIcon className="size-4" />
            </button>
          </div>

          <Checkbox
            label="Validar comprobantes por foto"
            hint="El agente revisa la captura del pago antes de confirmar el pedido."
            checked={method.validate_receipts}
            disabled={busy === method.id}
            onChange={(e) =>
              void act(method.id, () =>
                api.payments.update(method.id, { validate_receipts: e.target.checked }),
              )
            }
          />
        </li>
      ))}
    </ul>
  );
}

function AddMethod({
  existing,
  onAdded,
}: {
  existing: PaymentMethod[];
  onAdded: () => Promise<void>;
}) {
  // One row per method, enforced by a unique constraint on the table. Hiding
  // the ones already added beats letting somebody submit and get a 409.
  const available = METHODS.filter((m) => !existing.some((e) => e.method === m.value));

  const [method, setMethod] = useState<string>(available[0]?.value ?? "");
  const [instructions, setInstructions] = useState("");
  const [validate, setValidate] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!available.some((m) => m.value === method)) {
      setMethod(available[0]?.value ?? "");
    }
  }, [available, method]);

  if (available.length === 0) {
    return (
      <Alert tone="success" title="Ya cargaste todos los métodos">
        Puedes quitar el que no uses.
      </Alert>
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!method || !instructions.trim()) return;

    setBusy(true);
    setFailure(null);
    try {
      await api.payments.create({
        method: method as PaymentMethodBody["method"],
        instructions: instructions.trim(),
        validate_receipts: validate,
        is_active: true,
      });
      setInstructions("");
      setValidate(false);
      await onAdded();
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-lg border border-line p-4">
      <p className="text-sm font-medium text-ink">Agregar un método</p>
      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      <Select
        label="Método"
        options={available}
        value={method}
        onChange={(e) => setMethod(e.target.value)}
      />

      <TextArea
        label="Instrucciones"
        name="payment_instructions"
        rows={3}
        required
        hint="Tal cual se lo va a decir el agente a tu cliente."
        placeholder={PLACEHOLDERS[method] ?? ""}
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
      />

      <Checkbox
        label="Validar comprobantes por foto"
        hint="El agente revisa la captura antes de confirmar."
        checked={validate}
        onChange={(e) => setValidate(e.target.checked)}
      />

      <Button type="submit" loading={busy} disabled={!instructions.trim()}>
        Agregar
      </Button>
    </form>
  );
}
