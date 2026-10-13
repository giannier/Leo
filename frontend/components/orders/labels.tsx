/**
 * How the orders screens say things.
 *
 * The API speaks in slugs (`por_confirmar`, `monto_distinto`); the person at the
 * counter does not. Everything that turns one into the other lives here, so the
 * list, the detail and the tests agree on the words.
 */

import type { CustomerNotice } from "@/lib/api/client";
import { cx } from "@/components/ui";

export const STATE_LABELS: Record<string, string> = {
  borrador: "Borrador",
  por_confirmar: "Por confirmar",
  confirmado: "Confirmado",
  en_curso: "En curso",
  completado: "Completado",
  descartado: "Descartado",
};

/** Order of the filter chips: the work that needs a person comes first. */
export const STATE_ORDER = [
  "por_confirmar",
  "confirmado",
  "en_curso",
  "completado",
  "borrador",
  "descartado",
] as const;

const STATE_STYLES: Record<string, string> = {
  borrador: "bg-surface-muted text-ink-soft",
  por_confirmar: "bg-warning-soft text-warning",
  confirmado: "bg-success-soft text-success",
  en_curso: "bg-info-soft text-info",
  completado: "bg-success-soft text-success",
  descartado: "bg-danger-soft text-danger",
};

export function StateBadge({ state }: { state: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold",
        STATE_STYLES[state] ?? "bg-surface-muted text-ink-soft",
      )}
    >
      {STATE_LABELS[state] ?? state}
    </span>
  );
}

/** What each flag on a receipt means for the person about to decide. */
export const OBSERVATION_LABELS: Record<string, { label: string; detail: string }> = {
  monto_distinto: {
    label: "El monto no coincide",
    detail: "El monto del comprobante es distinto al total del pedido.",
  },
  codigo_repetido: {
    label: "Código repetido",
    detail: "Ese código de operación ya aparece en otro comprobante de tu negocio.",
  },
  baja_confianza: {
    label: "Lectura poco segura",
    detail: "La imagen se leyó con poca seguridad. Revisa los datos contra la foto.",
  },
  sin_datos: {
    label: "No se pudo leer",
    detail: "No se extrajeron datos de la imagen. Compárala tú mismo con el pedido.",
  },
  monto_negativo: {
    label: "Monto negativo",
    detail: "Parece una devolución o un reverso, no un pago. Revisa la foto antes de decidir.",
  },
  destinatario_distinto: {
    label: "Pago a otra persona",
    detail: "El destinatario del comprobante no coincide con las cuentas de cobro de tu negocio.",
  },
  destinatario_no_verificado: {
    label: "Destinatario sin verificar",
    detail: "No se pudo comprobar a quién se hizo el pago. Revisa el nombre en la foto.",
  },
  fecha_anterior_al_pedido: {
    label: "Fecha anterior al pedido",
    detail: "La operación es de un día anterior a la creación del pedido.",
  },
};

export function observationLabel(code: string): string {
  return OBSERVATION_LABELS[code]?.label ?? code;
}

const CURRENCY_SYMBOLS: Record<string, string> = { PEN: "S/", USD: "US$" };

/**
 * `"1500.5"` -> `S/ 1,500.50`. The amount is only ever re-spelled, never added up
 * or compared here: money arrives as an exact string and stays one.
 */
export function formatMoney(amount: string | number | null | undefined, currency?: string | null) {
  if (amount === null || amount === undefined || amount === "") return "—";
  const [whole = "0", cents = ""] = String(amount).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const symbol = currency ? (CURRENCY_SYMBOLS[currency] ?? currency) : "";
  return `${symbol ? `${symbol} ` : ""}${grouped}.${cents.padEnd(2, "0").slice(0, 2)}`;
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("es-PE", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  // A bare date (`2026-10-13`) is a day, not an instant: parsing it as UTC would
  // show the day before in Lima.
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Date(year, (month ?? 1) - 1, day).toLocaleDateString("es-PE", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/** The sentence the operator reads after deciding. */
export function noticeMessage(notice: CustomerNotice): {
  tone: "success" | "warning";
  text: string;
} {
  switch (notice) {
    case "enviado":
      return { tone: "success", text: "Aviso enviado al cliente." };
    case "ventana_cerrada":
      return {
        tone: "warning",
        text: "La ventana de 24 h está cerrada: el aviso no se envió por WhatsApp. Escríbele al cliente por otro medio.",
      };
    default:
      return {
        tone: "warning",
        text: "No pudimos enviar el aviso al cliente. Avísale tú por otro medio.",
      };
  }
}

export const DATA_LABELS: Record<string, string> = {
  nombres: "Nombre",
  telefono: "Teléfono",
  direccion: "Dirección",
  referencia: "Referencia",
  email: "Correo",
  dni: "DNI",
  ruc: "RUC",
  razon_social: "Razón social",
  distrito: "Distrito",
};

export function dataLabel(key: string): string {
  return DATA_LABELS[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export const HISTORY_LABELS: Record<string, string> = {
  creado: "Pedido creado",
  comprobante_recibido: "Comprobante recibido",
  "order.confirmed": "Pedido confirmado",
  "order.rejected": "Pedido rechazado",
  "order.status_changed": "Cambio de estado",
};
