"use client";

/**
 * The handful of primitives every screen is built from.
 *
 * They exist so the accessibility work happens once. A `<Field>` always has a
 * real `<label for>`, always shows its error *below the input it belongs to*,
 * and always announces that error to a screen reader. Written per screen,
 * one of those three gets forgotten every time.
 *
 * Touch targets are 44px minimum throughout. The people using this are
 * tapping with a thumb, sometimes with flour on their hands.
 */

import { forwardRef, useId, useState } from "react";
import Link from "next/link";

import { AlertIcon, CheckIcon, EyeIcon, EyeOffIcon, InfoIcon, SpinnerIcon } from "./icons";

function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/* ----------------------------------------------------------------- Button */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent-hover",
  secondary: "bg-surface text-ink border border-line-strong hover:bg-surface-muted",
  ghost: "text-ink-soft hover:bg-surface-muted",
  danger: "bg-danger text-white hover:brightness-95",
};

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", loading = false, fullWidth = false, children, className, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      // Disabled while in flight, so an impatient double-tap does not submit
      // the form twice - which for a registration means two accounts.
      disabled={loading || rest.disabled}
      aria-busy={loading || undefined}
      className={cx(
        "inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-lg px-4",
        "text-sm font-semibold transition-colors duration-150",
        "disabled:cursor-not-allowed disabled:opacity-50",
        BUTTON_STYLES[variant],
        fullWidth && "w-full",
        className,
      )}
      {...rest}
    >
      {loading ? <SpinnerIcon className="size-4" /> : null}
      {children}
    </button>
  );
});

/* ------------------------------------------------------------------ Field */

interface FieldProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string;
  /** Shown under the input, always. Not a placeholder - a placeholder
   * disappears exactly when the user needs it. */
  hint?: string;
  error?: string;
}

export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field(
  { label, hint, error, id, className, type = "text", required, ...rest },
  ref,
) {
  const generated = useId();
  const inputId = id ?? generated;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const [revealed, setRevealed] = useState(false);

  const isPassword = type === "password";
  const inputType = isPassword && revealed ? "text" : type;

  return (
    <div className="flex flex-col gap-1.5">
      {/* The asterisk sits *outside* the label element on purpose. Inside, it
       * becomes part of the field's accessible name - a screen reader
       * announces "Contraseña asterisco", and anything matching on the label
       * has to know about the decoration. `required` on the input is what
       * actually carries the meaning; this is the visual echo of it. */}
      <div className="flex items-baseline gap-1">
        <label htmlFor={inputId} className="text-sm font-medium text-ink">
          {label}
        </label>
        {required ? (
          <span className="text-danger" aria-hidden="true">
            *
          </span>
        ) : null}
      </div>

      <div className="relative">
        <input
          ref={ref}
          id={inputId}
          type={inputType}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={cx(hint && hintId, error && errorId) || undefined}
          className={cx(
            "min-h-11 w-full rounded-lg border bg-surface px-3 text-base text-ink",
            "placeholder:text-ink-faint",
            "transition-colors duration-150",
            error ? "border-danger" : "border-line-strong hover:border-ink-faint",
            isPassword && "pr-12",
            className,
          )}
          {...rest}
        />

        {isPassword ? (
          <button
            type="button"
            onClick={() => setRevealed((v) => !v)}
            // Not in the tab order of the form's happy path, but reachable.
            className="absolute inset-y-0 right-0 flex w-12 cursor-pointer items-center justify-center text-ink-faint hover:text-ink"
            aria-label={revealed ? "Ocultar contraseña" : "Mostrar contraseña"}
          >
            {revealed ? <EyeOffIcon className="size-5" /> : <EyeIcon className="size-5" />}
          </button>
        ) : null}
      </div>

      {hint && !error ? (
        <p id={hintId} className="text-xs text-ink-faint">
          {hint}
        </p>
      ) : null}

      {error ? (
        // `role="alert"` so a screen reader hears it without hunting, and
        // right under the field so a sighted user does not have to.
        <p id={errorId} role="alert" className="flex items-start gap-1.5 text-xs text-danger">
          <AlertIcon className="mt-0.5 size-3.5 shrink-0" />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
});

/* ------------------------------------------------------------------ Alert */

type AlertTone = "info" | "success" | "warning" | "danger";

const ALERT_STYLES: Record<AlertTone, { box: string; icon: string }> = {
  info: { box: "bg-info-soft border-info/25 text-ink", icon: "text-info" },
  success: { box: "bg-success-soft border-success/25 text-ink", icon: "text-success" },
  warning: { box: "bg-warning-soft border-warning/30 text-ink", icon: "text-warning" },
  danger: { box: "bg-danger-soft border-danger/25 text-ink", icon: "text-danger" },
};

const ALERT_ICONS: Record<AlertTone, typeof InfoIcon> = {
  info: InfoIcon,
  success: CheckIcon,
  warning: AlertIcon,
  danger: AlertIcon,
};

export function Alert({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: AlertTone;
  title?: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  const style = ALERT_STYLES[tone];
  const Icon = ALERT_ICONS[tone];
  return (
    <div
      // Errors are announced; the rest are not, so a page full of info boxes
      // does not talk over the user.
      role={tone === "danger" ? "alert" : "status"}
      className={cx("flex gap-3 rounded-lg border p-3 text-sm", style.box)}
    >
      {/* The icon is what carries the meaning for anyone who cannot see the
       * colour difference. */}
      <Icon className={cx("mt-0.5 size-5 shrink-0", style.icon)} />
      <div className="flex-1">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={title ? "mt-0.5 text-ink-soft" : "text-ink-soft"}>{children}</div> : null}
      </div>
      {action}
    </div>
  );
}

/* ------------------------------------------------------------------- Card */

export function Card({
  title,
  description,
  children,
  footer,
}: {
  title?: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-surface p-5 sm:p-6">
      {title ? <h2 className="text-lg font-semibold text-ink">{title}</h2> : null}
      {description ? <p className="mt-1 text-sm text-ink-soft">{description}</p> : null}
      <div className={title || description ? "mt-4" : undefined}>{children}</div>
      {footer ? <div className="mt-5 border-t border-line pt-4">{footer}</div> : null}
    </section>
  );
}

/* ------------------------------------------------------- Auth page shell */

export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-md">
        <Link href="/" className="mb-6 block text-center">
          <span className="font-heading text-2xl font-bold tracking-tight text-brand">Leo</span>
        </Link>

        <div className="rounded-xl border border-line bg-surface p-6 sm:p-8">
          <h1 className="font-heading text-xl font-semibold text-ink">{title}</h1>
          {subtitle ? <p className="mt-1.5 text-sm text-ink-soft">{subtitle}</p> : null}
          <div className="mt-6">{children}</div>
        </div>

        {footer ? <div className="mt-5 text-center text-sm text-ink-soft">{footer}</div> : null}
      </div>
    </main>
  );
}

/* ------------------------------------------------------------- Skeletons */

/** Reserves the space content will occupy, so nothing jumps when it lands. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cx("animate-pulse rounded-md bg-surface-muted", className ?? "h-4 w-full")}
    />
  );
}

export { cx };
