"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Alert, AuthShell, Button, Field } from "@/components/ui";
import { ApiError, humanMessage } from "@/lib/api/errors";
import { api } from "@/lib/api/client";
import { useSession } from "@/lib/session";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { signIn } = useSession();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [unverified, setUnverified] = useState(false);
  const [resent, setResent] = useState(false);
  const [busy, setBusy] = useState(false);

  // Set by the verification page, so somebody who just confirmed their
  // address arrives here knowing it worked rather than wondering.
  const justVerified = params.get("verified") === "1";

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setUnverified(false);
    setBusy(true);
    try {
      await signIn({ email, password });
      router.replace("/");
    } catch (caught) {
      // An unverified address is not a failed login - the password was right.
      // It gets its own path, with the resend button attached, because the
      // alternative is a person retyping a correct password over and over.
      if (caught instanceof ApiError && caught.code === "email_not_verified") {
        setUnverified(true);
      } else {
        setError(humanMessage(caught));
      }
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setResent(false);
    try {
      await api.auth.resendVerification(email);
    } catch {
      // Deliberately silent: the endpoint answers the same either way so it
      // cannot be used to discover which addresses have accounts.
    }
    setResent(true);
  }

  return (
    <AuthShell
      title="Entra a tu cuenta"
      subtitle="Administra tu agente de WhatsApp."
      footer={
        <>
          ¿Todavía no tienes cuenta?{" "}
          <Link href="/register" className="font-semibold text-accent hover:underline">
            Crea una gratis
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        {justVerified ? (
          <Alert tone="success" title="Correo confirmado">
            Ya puedes entrar con tu contraseña.
          </Alert>
        ) : null}

        {error ? <Alert tone="danger">{error}</Alert> : null}

        {unverified ? (
          <Alert tone="warning" title="Falta confirmar tu correo">
            {resent ? (
              <>Te enviamos un enlace nuevo a {email}. Revisa tu bandeja y el spam.</>
            ) : (
              <>
                Revisa tu bandeja.{" "}
                <button
                  type="button"
                  onClick={resend}
                  className="cursor-pointer font-semibold text-accent underline"
                >
                  Reenviar el enlace
                </button>
              </>
            )}
          </Alert>
        ) : null}

        <Field
          label="Correo electrónico"
          type="email"
          name="email"
          // The right mobile keyboard, and the browser's own autofill.
          inputMode="email"
          autoComplete="email"
          autoCapitalize="none"
          spellCheck={false}
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <Field
          label="Contraseña"
          type="password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        <Button type="submit" loading={busy} fullWidth className="mt-1">
          Entrar
        </Button>
      </form>
    </AuthShell>
  );
}

export default function LoginPage() {
  // `useSearchParams` needs a Suspense boundary in the App Router.
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
