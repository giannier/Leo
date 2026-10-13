/**
 * The API's error envelope, as `contracts/api.md` specifies it.
 *
 * Every failure from the backend - including the ones FastAPI raises itself -
 * comes back as `{error, message, fields?}`. `error` is a stable slug meant
 * for code to branch on; `message` is English prose meant for a developer.
 *
 * The panel never shows `message` to a shop owner. It maps `error` to Spanish
 * copy instead: "insufficient_role" has to become something a person can act
 * on, and a translated server string would drift from the code that raised it.
 */

export interface ApiErrorBody {
  error: string;
  message: string;
  fields?: Record<string, string>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  /** Per-field validation messages, keyed by field name. */
  readonly fields: Record<string, string>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error;
    this.fields = body.fields ?? {};
  }
}

/**
 * The network never answered: no DNS, no route, the API is down, the laptop
 * lost wifi. Kept distinct from `ApiError` because the advice differs - one
 * means "check your connection", the other means "the request was wrong".
 */
export class NetworkError extends Error {
  constructor(cause?: unknown) {
    super("No pudimos contactar al servidor.");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

/** Spanish copy for the error slugs the panel can actually run into. */
const MESSAGES: Record<string, string> = {
  // Auth
  invalid_credentials: "Correo o contraseña incorrectos.",
  email_not_verified: "Confirma tu correo antes de entrar. Revisa tu bandeja.",
  not_authenticated: "Tu sesión terminó. Vuelve a entrar.",
  token_expired: "Tu sesión terminó. Vuelve a entrar.",
  invalid_token: "El enlace no es válido o ya se usó.",
  token_used: "Ese enlace ya se usó. Pide uno nuevo.",
  email_taken: "Ya existe una cuenta con ese correo.",
  phone_in_use: "Ese teléfono ya está en uso en otra cuenta de prueba.",

  // OTP
  otp_invalid: "El código no coincide. Revisa los seis dígitos.",
  otp_expired: "El código venció. Pide uno nuevo.",
  otp_rate_limited: "Pediste demasiados códigos. Espera unos minutos.",

  // Access
  insufficient_role: "Tu rol no permite hacer esto. Pídeselo al dueño de la cuenta.",
  no_active_tenant: "Tu usuario no está asociado a ningún negocio.",
  tenant_access_denied: "No tienes acceso a este negocio.",
  not_found: "No encontramos lo que buscabas.",

  // Agent activation
  checklist_incomplete: "Te falta completar la configuración antes de activar el agente.",
  number_not_connected: "Primero conecta un número de WhatsApp.",
  tenant_not_active: "Tu prueba terminó. Elige un plan para reactivar el agente.",

  // Connection
  phone_not_verified: "Verifica tu teléfono de contacto antes de pedir un número.",
  phone_already_connected: "Ese número ya está conectado. Escríbenos si es tuyo.",
  open_request_exists: "Ya hay una solicitud abierta para ese número.",

  // Orders
  invalid_transition: "Ese pedido ya no puede pasar a ese estado. Actualiza la página.",
  already_reviewed: "Otra persona ya revisó este pedido. Actualiza la página.",

  // Generic
  validation_error: "Revisa los datos marcados.",
  rate_limited: "Demasiados intentos. Espera un momento.",
  internal_error: "Algo falló de nuestro lado. Intenta de nuevo.",
};

/**
 * What to show the user. Falls back to something honest rather than to the
 * server's English, because a slug we forgot to translate is our bug and the
 * user should not be handed the debugging output.
 */
export function humanMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return MESSAGES[error.code] ?? "No pudimos completar la acción. Intenta de nuevo.";
  }
  if (error instanceof NetworkError) {
    return "No pudimos contactar al servidor. Revisa tu conexión.";
  }
  return "Ocurrió un error inesperado.";
}
