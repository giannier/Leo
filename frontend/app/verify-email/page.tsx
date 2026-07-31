"use client";

/**
 * Where the link in the verification email lands.
 *
 * The path is fixed by the backend: `VERIFICATION_PATH` in
 * `app/services/auth.py` builds `{frontend_base_url}/verify-email?token=...`.
 * Renaming this route silently breaks every email already sent.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { Alert, AuthShell, Button, Field } from "@/components/ui";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { SpinnerIcon } from "@/components/icons";

type State = "checking" | "ok" | "expired" | "invalid" | "missing";

function Verifier() {
  const router = useRouter();
  const token = useSearchParams().get("token");
  const [state, setState] = useState<State>(token ? "checking" : "missing");
  const [email, setEmail] = useState("");
  const [resent, setResent] = useState(false);
  const [busy, setBusy] = useState(false);

  // Guarded: React runs effects twice in development, and the token is
  // single-use. Without this the second call consumes nothing and reports
  // "already used" on a link that worked perfectly.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;

    api.auth
      .verifyEmail(token)
      .then(() => {
        setState("ok");
        // Straight to login rather than dropping them on a dead end. The
        // banner there tells them it worked.
        setTimeout(() => router.replace("/login?verified=1"), 1200);
      })
      .catch((caught) => {
        setState(caught instanceof ApiError && caught.code === "token_expired" ? "expired" : "invalid");
      });
  }, [token, router]);

  async function resend(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.auth.resendVerification(email.trim());
    } catch {
      // Same answer either way, so the endpoint cannot be used to find out
      // which addresses have accounts.
    }
    setResent(true);
    setBusy(false);
  }

  if (state === "checking") {
    return (
      <AuthShell title="Confirmando tu correo" subtitle="Esto toma un segundo.">
        <div className="flex justify-center py-4 text-accent">
          <SpinnerIcon className="size-8" />
        </div>
      </AuthShell>
    );
  }

  if (state === "ok") {
    return (
      <AuthShell title="Listo" subtitle="Tu correo quedó confirmado.">
        <Alert tone="success" title="Cuenta activada">
          Te estamos llevando a la pantalla de ingreso.
        </Alert>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={state === "expired" ? "El enlace venció" : "No pudimos confirmar tu correo"}
      subtitle={
        state === "expired"
          ? "Los enlaces duran 24 horas. Pide uno nuevo y listo."
          : "El enlace no es válido o ya se usó. Pide uno nuevo."
      }
      footer={
        <Link href="/login" className="font-semibold text-accent hover:underline">
          Volver a entrar
        </Link>
      }
    >
      {resent ? (
        <Alert tone="success" title="Enlace enviado">
          Si esa dirección tiene una cuenta, va a recibir un correo en unos segundos.
        </Alert>
      ) : (
        <form onSubmit={resend} className="flex flex-col gap-4">
          <Field
            label="Tu correo electrónico"
            name="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Button type="submit" loading={busy} fullWidth>
            Enviarme un enlace nuevo
          </Button>
        </form>
      )}
    </AuthShell>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <Verifier />
    </Suspense>
  );
}
