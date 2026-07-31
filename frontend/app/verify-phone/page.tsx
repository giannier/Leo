"use client";

/**
 * Six-digit phone verification (Clarification 005).
 *
 * Required before filing a number connection request. It sits behind the
 * session, not in the public auth flow, because the code goes to the phone on
 * the account.
 *
 * The input is one field, not six boxes. Six boxes look nicer in a
 * screenshot, and then somebody pastes a code from their SMS app and only the
 * first digit lands.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Alert, AuthShell, Button, Field } from "@/components/ui";
import { api } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { useSession } from "@/lib/session";

const RESEND_SECONDS = 60;

export default function VerifyPhonePage() {
  const router = useRouter();
  const { status } = useSession();

  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  // A visible countdown, so the resend button being disabled reads as "wait"
  // rather than "broken".
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  async function requestCode() {
    setError(null);
    setBusy(true);
    try {
      await api.auth.requestOtp();
      setSent(true);
      setCooldown(RESEND_SECONDS);
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.auth.verifyOtp(code);
      router.replace("/channel?verified=1");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Verifica tu teléfono"
      subtitle="Te enviamos un código de seis dígitos al teléfono de tu cuenta."
    >
      <div className="flex flex-col gap-4">
        {error ? <Alert tone="danger">{error}</Alert> : null}

        {!sent ? (
          <>
            <Alert tone="info">
              Necesitamos confirmar tu teléfono antes de conectar un número de WhatsApp.
            </Alert>
            <Button onClick={requestCode} loading={busy} fullWidth>
              Enviarme el código
            </Button>
          </>
        ) : (
          <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
            <Field
              label="Código de seis dígitos"
              name="code"
              // `numeric` rather than `tel`: a phone keypad here would offer
              // + and * on a field that only takes digits.
              inputMode="numeric"
              // Lets Android and iOS offer the code straight from the SMS.
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              required
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              className="tabular text-center text-lg tracking-[0.4em]"
            />

            <Button type="submit" loading={busy} disabled={code.length !== 6} fullWidth>
              Verificar
            </Button>

            <Button
              type="button"
              variant="ghost"
              onClick={requestCode}
              disabled={cooldown > 0 || busy}
              fullWidth
            >
              {cooldown > 0 ? `Reenviar en ${cooldown}s` : "Reenviar el código"}
            </Button>
          </form>
        )}
      </div>
    </AuthShell>
  );
}
