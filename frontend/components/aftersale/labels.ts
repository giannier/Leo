/** How the after-sale screens say things (feature 019, phase G): the API speaks in slugs. */

export const RETURN_TYPES: Record<string, string> = {
  devolucion: "Devolución",
  cambio: "Cambio",
  garantia: "Garantía",
  reclamo: "Reclamo",
};

export const RETURN_REASONS: Record<string, string> = {
  defectuoso: "Llegó defectuoso",
  equivocado: "Le llegó otro producto",
  incompleto: "Llegó incompleto",
  no_conforme: "No era lo que esperaba",
  tardio: "Llegó tarde",
  otro: "Otro motivo",
};

export const RETURN_STATES: Record<string, { label: string; tone: string }> = {
  solicitada: { label: "Por revisar", tone: "bg-warning-soft text-warning" },
  aprobada: { label: "Aprobada", tone: "bg-info-soft text-info" },
  rechazada: { label: "Rechazada", tone: "bg-danger-soft text-danger" },
  resuelta: { label: "Resuelta", tone: "bg-success-soft text-success" },
  cancelada: { label: "Cancelada", tone: "bg-surface-muted text-ink-soft" },
};

export const DOCUMENT_TYPES: Record<string, string> = {
  boleta: "Boleta",
  factura: "Factura",
  guia: "Guía de remisión",
  garantia: "Garantía",
  proforma: "Proforma",
  nota_credito: "Nota de crédito",
  otro: "Otro documento",
};

export const ACTOR_LABELS: Record<string, string> = {
  agent: "El asistente",
  user: "Tu equipo",
};
