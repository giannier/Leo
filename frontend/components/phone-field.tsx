"use client";

/**
 * A phone number as two boxes: the country (with its dial code) and the number.
 *
 * Nobody types "+51" by hand; they pick Perú and type 974 667 498. The value that
 * goes in and out is still the E.164 string the server wants (`+51974667498`, or
 * "" when the number box is empty), so a form keeps one string in its state.
 */

import { useEffect, useId, useState } from "react";

import { AlertIcon } from "@/components/icons";
import { cx } from "@/components/ui";

export const DIAL_CODES = [
  { iso: "PE", name: "Perú", dial: "51" },
  { iso: "CO", name: "Colombia", dial: "57" },
  { iso: "MX", name: "México", dial: "52" },
  { iso: "CL", name: "Chile", dial: "56" },
  { iso: "EC", name: "Ecuador", dial: "593" },
  { iso: "BO", name: "Bolivia", dial: "591" },
  { iso: "AR", name: "Argentina", dial: "54" },
  { iso: "BR", name: "Brasil", dial: "55" },
  { iso: "UY", name: "Uruguay", dial: "598" },
  { iso: "PY", name: "Paraguay", dial: "595" },
  { iso: "VE", name: "Venezuela", dial: "58" },
  { iso: "PA", name: "Panamá", dial: "507" },
  { iso: "CR", name: "Costa Rica", dial: "506" },
  { iso: "GT", name: "Guatemala", dial: "502" },
  { iso: "SV", name: "El Salvador", dial: "503" },
  { iso: "HN", name: "Honduras", dial: "504" },
  { iso: "NI", name: "Nicaragua", dial: "505" },
  { iso: "CU", name: "Cuba", dial: "53" },
  { iso: "US", name: "Estados Unidos / Canadá", dial: "1" },
  { iso: "ES", name: "España", dial: "34" },
  { iso: "PT", name: "Portugal", dial: "351" },
  { iso: "FR", name: "Francia", dial: "33" },
  { iso: "IT", name: "Italia", dial: "39" },
  { iso: "DE", name: "Alemania", dial: "49" },
  { iso: "GB", name: "Reino Unido", dial: "44" },
] as const;

const DEFAULT_ISO = "PE";

const dialOf = (iso: string) => DIAL_CODES.find((c) => c.iso === iso)?.dial ?? "51";

/** Splits `+51974667498` into its country and the rest; the longest dial code wins. */
export function splitPhone(value: string, fallbackIso: string): { iso: string; national: string } {
  const digits = value.replace(/\D/g, "");
  if (!value.trim().startsWith("+")) return { iso: fallbackIso, national: digits };
  const match = [...DIAL_CODES]
    .sort((a, b) => b.dial.length - a.dial.length)
    .find((c) => digits.startsWith(c.dial));
  return match
    ? { iso: match.iso, national: digits.slice(match.dial.length) }
    : { iso: fallbackIso, national: digits };
}

/** The E.164 string for a country and a number, or "" while the number is empty. */
export function joinPhone(iso: string, national: string): string {
  const digits = national.replace(/\D/g, "");
  return digits ? `+${dialOf(iso)}${digits}` : "";
}

/** Whether `value` is a complete E.164 number (what the server accepts). */
export function isCompletePhone(value: string): boolean {
  return /^\+[1-9]\d{7,14}$/.test(value);
}

interface PhoneFieldProps {
  label: string;
  /** E.164, or "" when empty. */
  value: string;
  onChange: (value: string) => void;
  /** The country the box starts on while there is no number yet. */
  defaultCountry?: string;
  hint?: string;
  error?: string;
  required?: boolean;
  name?: string;
  id?: string;
}

export function PhoneField({
  label,
  value,
  onChange,
  defaultCountry = DEFAULT_ISO,
  hint,
  error,
  required,
  name,
  id,
}: PhoneFieldProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;

  const [state, setState] = useState(() => splitPhone(value, defaultCountry));

  // The value changes from outside too: a saved number arrives after the form
  // renders, and an empty form follows the country chosen above it.
  useEffect(() => {
    if (value === joinPhone(state.iso, state.national)) return;
    setState(splitPhone(value, value ? state.iso : defaultCountry));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, defaultCountry]);

  function update(iso: string, raw: string) {
    // A whole number pasted with its +country: split it instead of doubling the code.
    if (raw.trim().startsWith("+")) {
      const split = splitPhone(raw, iso);
      setState(split);
      onChange(joinPhone(split.iso, split.national));
      return;
    }
    const national = raw.replace(/\D/g, "");
    setState({ iso, national });
    onChange(joinPhone(iso, national));
  }

  return (
    <div className="flex flex-col gap-1.5">
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

      <div className="flex gap-2">
        {/* A native select, like the rest of the forms: the phone's own wheel. */}
        <select
          aria-label={`${label}: país`}
          value={state.iso}
          onChange={(e) => update(e.target.value, state.national)}
          className={cx(
            "min-h-11 w-40 shrink-0 cursor-pointer rounded-lg border bg-surface px-2 text-base text-ink",
            "transition-colors duration-150",
            error ? "border-danger" : "border-line-strong hover:border-ink-faint",
          )}
        >
          {DIAL_CODES.map((c) => (
            <option key={c.iso} value={c.iso}>
              {c.name} (+{c.dial})
            </option>
          ))}
        </select>
        <input
          id={inputId}
          name={name}
          type="tel"
          inputMode="tel"
          autoComplete="tel-national"
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={cx(hint && hintId, error && errorId) || undefined}
          placeholder="974 667 498"
          value={state.national}
          onChange={(e) => update(state.iso, e.target.value)}
          className={cx(
            "min-h-11 min-w-0 flex-1 rounded-lg border bg-surface px-3 text-base text-ink",
            "placeholder:text-ink-faint transition-colors duration-150",
            error ? "border-danger" : "border-line-strong hover:border-ink-faint",
          )}
        />
      </div>

      {hint && !error ? (
        <p id={hintId} className="text-xs text-ink-faint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className="flex items-start gap-1.5 text-xs text-danger">
          <AlertIcon className="mt-0.5 size-3.5 shrink-0" />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}
