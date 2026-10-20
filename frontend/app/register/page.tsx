"use client";

import Link from "next/link";
import { useState } from "react";

import { PhoneField, isCompletePhone } from "@/components/phone-field";
import { Alert, AuthShell, Button, Field, Select } from "@/components/ui";
import { api } from "@/lib/api/client";
import { ApiError, humanMessage } from "@/lib/api/errors";

/** Mirrors `MIN_PASSWORD_LENGTH` in `app/api/v1/auth.py`. */
const MIN_PASSWORD = 10;

type Errors = Partial<Record<"full_name" | "email" | "password" | "business_name" | "phone_e164", string>>;

/** The business's country: the prefix of a phone a customer types without one. */
const COUNTRIES = [
  { value: "PE", label: "Perú" },
  { value: "CO", label: "Colombia" },
  { value: "MX", label: "México" },
  { value: "CL", label: "Chile" },
  { value: "EC", label: "Ecuador" },
  { value: "BO", label: "Bolivia" },
  { value: "AR", label: "Argentina" },
  { value: "US", label: "Estados Unidos" },
  { value: "ES", label: "España" },
] as const;

export default function RegisterPage() {
  const [form, setForm] = useState({
    full_name: "",
    business_name: "",
    email: "",
    phone_e164: "",
    password: "",
    country: "PE",
  });
  const [errors, setErrors] = useState<Errors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function set<K extends keyof typeof form>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
    // Clear this field's error as soon as it is touched. Leaving it there
    // while the person is visibly fixing it is just nagging.
    setErrors((e) => (e[key as keyof Errors] ? { ...e, [key]: undefined } : e));
  }

  /** Checked on submit, not on every keystroke: telling somebody their email
   * is invalid while they are still typing the third character is noise. */
  function validate(): Errors {
    const found: Errors = {};
    if (!form.full_name.trim()) found.full_name = "Escribe tu nombre.";
    if (!form.business_name.trim()) found.business_name = "Escribe el nombre de tu negocio.";
    if (!form.email.includes("@")) found.email = "Escribe un correo válido.";
    if (form.password.length < MIN_PASSWORD) {
      found.password = `Usa al menos ${MIN_PASSWORD} caracteres.`;
    }
    if (form.phone_e164 && !isCompletePhone(form.phone_e164)) {
      found.phone_e164 = "Usa el formato +51987654321.";
    }
    return found;
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);

    const found = validate();
    if (Object.keys(found).length > 0) {
      setErrors(found);
      // Move focus to the first problem, so a keyboard or screen-reader user
      // is not left hunting for what went wrong.
      document.querySelector<HTMLInputElement>(`[name="${Object.keys(found)[0]}"]`)?.focus();
      return;
    }

    setBusy(true);
    try {
      const phone = form.phone_e164.trim();
      await api.auth.register({
        full_name: form.full_name.trim(),
        business_name: form.business_name.trim(),
        email: form.email.trim(),
        password: form.password,
        phone_e164: phone || null,
        country: form.country,
      });
      setDone(form.email.trim());
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "validation_error") {
        setErrors(caught.fields as Errors);
      } else {
        setFailure(humanMessage(caught));
      }
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <AuthShell
        title="Revisa tu correo"
        subtitle={`Enviamos un enlace de confirmación a ${done}.`}
        footer={
          <Link href="/login" className="font-semibold text-accent hover:underline">
            Ir a entrar
          </Link>
        }
      >
        <Alert tone="success" title="Cuenta creada">
          Abre el enlace para activar tu prueba de 14 días. Si no lo ves, revisa la carpeta de
          spam.
        </Alert>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Crea tu cuenta"
      subtitle="14 días de prueba. Sin tarjeta de crédito."
      footer={
        <>
          ¿Ya tienes cuenta?{" "}
          <Link href="/login" className="font-semibold text-accent hover:underline">
            Entra aquí
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        {failure ? <Alert tone="danger">{failure}</Alert> : null}

        <Field
          label="Tu nombre"
          name="full_name"
          autoComplete="name"
          required
          value={form.full_name}
          error={errors.full_name}
          onChange={(e) => set("full_name", e.target.value)}
        />

        <Field
          label="Nombre de tu negocio"
          name="business_name"
          autoComplete="organization"
          required
          hint="Así te va a presentar el agente ante tus clientes."
          value={form.business_name}
          error={errors.business_name}
          onChange={(e) => set("business_name", e.target.value)}
        />

        <Field
          label="Correo electrónico"
          name="email"
          type="email"
          inputMode="email"
          autoComplete="email"
          autoCapitalize="none"
          spellCheck={false}
          required
          value={form.email}
          error={errors.email}
          onChange={(e) => set("email", e.target.value)}
        />

        <Select
          label="País del negocio"
          name="country"
          options={COUNTRIES}
          value={form.country}
          onChange={(e) => set("country", e.target.value)}
        />

        <PhoneField
          label="Teléfono de contacto"
          name="phone_e164"
          hint="Opcional. Es solo un dato de contacto: no te pedimos verificarlo."
          defaultCountry={form.country}
          value={form.phone_e164}
          error={errors.phone_e164}
          onChange={(value) => set("phone_e164", value)}
        />

        <Field
          label="Contraseña"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          hint={`Mínimo ${MIN_PASSWORD} caracteres.`}
          value={form.password}
          error={errors.password}
          onChange={(e) => set("password", e.target.value)}
        />

        <Button type="submit" loading={busy} fullWidth className="mt-1">
          Crear mi cuenta
        </Button>
      </form>
    </AuthShell>
  );
}
