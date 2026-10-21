/**
 * The typed API client.
 *
 * Two rules shape everything here.
 *
 * **The access token never leaves memory.** Not `localStorage`, not a
 * readable cookie. A script injected into the panel can read both of those,
 * and the whole point of the backend putting the refresh token in an
 * httpOnly cookie is defeated if the access token is sitting where any
 * third-party script can pick it up. The cost is that a page reload starts
 * with no token - which is fine, because `POST /auth/refresh` returns the
 * entire session and the cookie travels on its own.
 *
 * **A 401 is retried exactly once, and concurrent 401s share one refresh.**
 * A dashboard that fires six requests on mount will get six 401s a quarter of
 * an hour into a session. Six refreshes would rotate the token family six
 * times and the backend's replay detection would - correctly - kill the
 * session. So the first 401 starts a refresh and the other five wait on it.
 */

import type { paths } from "./schema";
import { ApiError, type ApiErrorBody, NetworkError } from "./errors";
import { forgetKey, keyFor, writeFingerprint } from "./idempotency";

const BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const PREFIX = "/api/v1";

/** Paths that must never trigger the refresh dance. */
const inFlight = new Map<string, Promise<unknown>>();

const NO_REFRESH = new Set(["/auth/refresh", "/auth/login", "/auth/logout", "/auth/register"]);

type Session = paths["/api/v1/auth/refresh"]["post"]["responses"][200]["content"]["application/json"];

let accessToken: string | null = null;
let activeTenantId: string | null = null;
/** In flight, so parallel 401s wait on one refresh instead of racing. */
let refreshing: Promise<Session | null> | null = null;
/** Called when the session is definitively gone, so the UI can redirect. */
let onSessionLost: (() => void) | null = null;

export function setAccessToken(token: string | null, tenantId?: string | null): void {
  accessToken = token;
  if (tenantId !== undefined) {
    activeTenantId = tenantId;
  }
}

/** The live channel's address (Feature 007); the token travels as a subprotocol, not in the URL. */
/**
 * Where the inbox socket connects, and what it offers as subprotocols. The access token
 * travels as the second one: a browser cannot set headers on a WebSocket, and in the URL
 * it would end up in every access log.
 */
export function inboxSocketTarget(): { url: string; protocols: string[] } | null {
  if (!accessToken) return null;
  const base = BASE_URL.replace(/^http/, "ws");
  return { url: `${base}/api/v1/ws`, protocols: ["leo", accessToken] };
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function onSessionEnded(handler: (() => void) | null): void {
  onSessionLost = handler;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** An array repeats the key (`estado=a&estado=b`), which is how FastAPI reads a list. */
  query?: Record<string, string | number | boolean | readonly string[] | undefined>;
  /** Set for the calls that must not attempt a refresh on 401. */
  skipRefresh?: boolean;
  signal?: AbortSignal;
  /** Set by `request` for a POST, so a retry of the same press cannot create a second copy. */
  idempotencyKey?: string;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(`${BASE_URL}${PREFIX}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (Array.isArray(value)) {
        for (const item of value) {
          url.searchParams.append(key, item);
        }
      } else if (value !== undefined) {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function readError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    // A proxy or a crash produced something that is not our envelope.
    body = { error: "internal_error", message: `HTTP ${response.status}` };
  }
  return new ApiError(response.status, body);
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json" };
  // `FormData` is sent as-is. Setting Content-Type by hand would omit the
  // multipart boundary the browser generates, and the server would reject
  // every upload with a parse error that says nothing about the cause.
  const isForm = options.body instanceof FormData;
  if (options.body !== undefined && !isForm) {
    headers["Content-Type"] = "application/json";
  }
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  if (options.idempotencyKey) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }

  try {
    return await fetch(buildUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body:
        options.body === undefined
          ? undefined
          : isForm
            ? (options.body as FormData)
            : JSON.stringify(options.body),
      // The refresh cookie rides on this. Without it the browser withholds
      // the cookie on a cross-origin request and every refresh 401s.
      credentials: "include",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    throw new NetworkError(cause);
  }
}

/**
 * Exchange the refresh cookie for a new access token.
 *
 * Returns the whole session, so the caller can restore who is signed in
 * without a second round trip. `null` means the cookie is gone or refused,
 * which is the normal answer for a first visit.
 */
export async function refreshSession(): Promise<Session | null> {
  if (refreshing) {
    return refreshing;
  }

  refreshing = (async () => {
    try {
      const response = await send("/auth/refresh", {
        method: "POST",
        body: activeTenantId ? { tenant_id: activeTenantId } : {},
        skipRefresh: true,
      });
      if (!response.ok) {
        accessToken = null;
        return null;
      }
      const session = (await response.json()) as Session;
      accessToken = session.access_token;
      activeTenantId = session.active_tenant_id ?? null;
      return session;
    } catch {
      // A network blip is not proof the session is over. Report failure so
      // the caller can show "no connection", but keep the token: the next
      // attempt may well succeed, and clearing it would sign the user out
      // every time their wifi hiccups.
      return null;
    } finally {
      refreshing = null;
    }
  })();

  return refreshing;
}

/* ------------------------------------------------------------------ *
 * Writes that cannot be doubled (the helpers live in `./idempotency`).
 *
 * On a poor connection people press "Guardar" again and again, or the answer to the
 * first press never arrives and the screen retries. Two defences, both here so every
 * form has them without knowing:
 *
 *  1. The same write (method, path and body) already in flight is not sent again: the
 *     second press receives the first one's answer.
 *  2. A POST carries an `Idempotency-Key`. A retry of the same payload after a failure
 *     reuses the key, so if the first attempt did reach the server the server answers
 *     with what it already did instead of creating another row. A success forgets the
 *     key, so creating the same thing on purpose later still works.
 * ------------------------------------------------------------------ */

/** One typed request, with the 401 retry built in. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  // Reads and uploads are left alone: a read can be repeated freely, and a file is not
  // something to fingerprint.
  if (method === "GET" || options.body instanceof FormData) return perform<T>(path, options);

  const fingerprint = writeFingerprint(method, path, options.body);
  const running = inFlight.get(fingerprint);
  if (running) return running as Promise<T>;

  // A POST without a body ("duplicate this product") still gets an empty JSON one, so the
  // server can tell it apart and protect it the same way.
  const withKey =
    method === "POST" && !path.startsWith("/auth/")
      ? { ...options, body: options.body ?? {}, idempotencyKey: keyFor(fingerprint) }
      : options;
  const attempt = perform<T>(path, withKey)
    .then((result) => {
      forgetKey(fingerprint);
      return result;
    })
    .finally(() => inFlight.delete(fingerprint));
  inFlight.set(fingerprint, attempt);
  return attempt;
}

async function perform<T>(path: string, options: RequestOptions): Promise<T> {
  let response = await send(path, options);

  const mayRetry = !options.skipRefresh && !NO_REFRESH.has(path);
  if (response.status === 401 && mayRetry) {
    const session = await refreshSession();
    if (session === null) {
      accessToken = null;
      onSessionLost?.();
      throw await readError(response);
    }
    response = await send(path, options);
  }

  if (!response.ok) {
    throw await readError(response);
  }

  // 204 and friends. `json()` on an empty body throws, and several of our
  // endpoints answer 204 by design (activate, deactivate, verify-email).
  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** A file from the API (a receipt PDF): same auth and refresh as `request`. */
export async function requestBlob(path: string): Promise<Blob> {
  let response = await send(path, {});
  if (response.status === 401) {
    const session = await refreshSession();
    if (session === null) {
      accessToken = null;
      onSessionLost?.();
      throw await readError(response);
    }
    response = await send(path, {});
  }
  if (!response.ok) {
    throw await readError(response);
  }
  return response.blob();
}

/* ------------------------------------------------------------------ *
 * Named operations.
 *
 * Thin wrappers rather than a generic `api.get("/whatever")`, so a renamed
 * endpoint is a TypeScript error here instead of a 404 at runtime, and so
 * every call site gets the real response type from the OpenAPI schema.
 * ------------------------------------------------------------------ */

type Ops = {
  [K in keyof paths]: paths[K];
};

type JsonBody<T> = T extends { content: { "application/json": infer B } } ? B : never;
type Ok<T> = T extends { responses: infer R }
  ? R extends { 200: { content: { "application/json": infer B } } }
    ? B
    : R extends { 201: { content: { "application/json": infer B } } }
      ? B
      : void
  : never;

export type LoginBody = JsonBody<Ops["/api/v1/auth/login"]["post"]["requestBody"]>;
export type RegisterBody = JsonBody<Ops["/api/v1/auth/register"]["post"]["requestBody"]>;
export type SessionPayload = Session;
export type TenantSummary = Ok<Ops["/api/v1/tenant"]["get"]>;
export type Checklist = Ok<Ops["/api/v1/tenant/activation-checklist"]["get"]>;
export type Usage = Ok<Ops["/api/v1/tenant/usage"]["get"]>;
export type ChecklistItem = Checklist["items"][number];

export type BusinessProfile = Ok<Ops["/api/v1/tenant/business-profile"]["get"]>;
export type BusinessProfilePatch = JsonBody<
  Ops["/api/v1/tenant/business-profile"]["put"]["requestBody"]
>;
export type AgentConfig = Ok<Ops["/api/v1/tenant/agent-config"]["get"]>;
export type AgentConfigPatch = JsonBody<Ops["/api/v1/tenant/agent-config"]["put"]["requestBody"]>;

export type ProductPage = Ok<Ops["/api/v1/tenant/products"]["get"]>;
export type Product = ProductPage["items"][number];
type ProductCreate = JsonBody<Ops["/api/v1/tenant/products"]["post"]["requestBody"]>;
/** Fields the server fills in when omitted (TASK-406); the generated type lists
 * them as required because the schema is read from the response side. */
type ProductServerDefaults =
  | "modo_venta"
  | "es_digital"
  | "bundle_permite_libres"
  | "destacado"
  | "tags"
  | "agendable"
  | "buffer_min";
export type ProductBody = Omit<ProductCreate, ProductServerDefaults> &
  Partial<Pick<ProductCreate, ProductServerDefaults>>;
export type ImportPreview = Ok<Ops["/api/v1/tenant/products/import"]["post"]>;
export type ProductPatch = JsonBody<Ops["/api/v1/tenant/products/{product_id}"]["put"]["requestBody"]>;
export type PhotoList = Ok<Ops["/api/v1/tenant/products/{product_id}/photos"]["get"]>;
export type Photo = PhotoList["items"][number];

export type KnowledgeList = Ok<Ops["/api/v1/tenant/knowledge"]["get"]>;
// Dashboard (Feature 010). The report is a free-form dict on the server; typed here.
export interface MetricsSummary {
  conversations: number;
  conversations_agent_only: number;
  conversations_with_person: number;
  messages_in: number;
  messages_out_agent: number;
  messages_out_person: number;
  first_responses: number;
  handovers: number;
  orders_confirmed: number;
  primera_respuesta_promedio_segundos: number | null;
  horas_ahorradas: number;
  derivaciones_por_motivo: Record<string, number>;
  ventas?: Record<string, string>;
}
export interface MetricsDay {
  dia: string;
  conversaciones: number;
  solo_agente: number;
  con_persona: number;
  derivaciones: number;
  pedidos: number;
}
/** How fast a person answers after the assistant hands a conversation over (minutes). */
export interface HandoffResponse {
  total: number;
  atendidos: number;
  sin_atender: number;
  mediana_minutos: number | null;
  p90_minutos: number | null;
}
export interface MetricsReport {
  desde: string;
  hasta: string;
  traspasos?: HandoffResponse;
  minutos_por_conversacion: number;
  total: MetricsSummary;
  anterior: MetricsSummary;
  por_dia: MetricsDay[];
  mas_vendidos?: { producto: string; cantidad: number }[];
  // What Meta will bill this month (Feature 011, C-111); owners and admins only.
  whatsapp_mes?: {
    desde: string;
    mensajes_servicio: number;
    gratis_restantes: number;
    plantillas_utilidad: number;
    plantillas_marketing: number;
    costo_estimado_usd: string | null;
  };
}
export interface PlatformMetrics {
  desde: string;
  hasta: string;
  negocios_por_estado: Record<string, number>;
  registros_por_semana: Record<string, number>;
  registros: number;
  convertidos: number;
  conversion: number | null;
  bajas: number;
  mrr_usd: string;
  margen_por_negocio: {
    tenant_id: string;
    negocio: string;
    ingreso_usd: string;
    costo_llm_usd: string;
    margen_usd: string;
    tokens: number;
  }[];
  trazas_sin_precio: number;
  operacion: {
    conexiones_pendientes: number;
    sin_procesar: number;
    envios_fallidos: number;
    latencia_p95_ms: number | null;
    numeros_en_riesgo: number;
  };
}
export type ModelCostRow = Ok<Ops["/api/v1/platform-admin/model-costs"]["get"]>[number];

// Billing (Feature 009).
export type BillingCatalog = Ok<Ops["/api/v1/tenant/billing/plans"]["get"]>;
export type BillingOverview = Ok<Ops["/api/v1/tenant/billing"]["get"]>;
export type BillingCharge = Ok<Ops["/api/v1/tenant/billing/checkout"]["post"]>;
export type BillingChange = Ok<Ops["/api/v1/tenant/billing/change-plan"]["post"]>;
export type BillingCycle = "monthly" | "annual";
export type AdminSubscription = Ok<Ops["/api/v1/platform-admin/billing/subscriptions"]["get"]>[number];
export type AdminPayment = Ok<Ops["/api/v1/platform-admin/billing/payments"]["post"]>;
export type AdminCoupon = Ok<Ops["/api/v1/platform-admin/billing/coupons"]["get"]>[number];
export type KnowledgeDetail = Ok<Ops["/api/v1/tenant/knowledge/{source_id}"]["get"]>;
export type KnowledgeSource = KnowledgeList["sources"][number];
export type KnowledgeUsage = KnowledgeList["usage"];

export type ConnectionRequest = Ok<
  Ops["/api/v1/tenant/connection-requests"]["get"]
>[number];
export type ConnectionRequestBody = JsonBody<
  Ops["/api/v1/tenant/connection-requests"]["post"]["requestBody"]
>;
export type WhatsAppNumber = Ok<Ops["/api/v1/tenant/whatsapp-number"]["get"]>;
/** The business's own YCloud API key and the number to connect with it. */
export interface ConnectNumberBody {
  api_key: string;
  phone_e164: string;
  display_name: string;
}

export type Team = Ok<Ops["/api/v1/tenant/team"]["get"]>;
export type TeamMember = Team["members"][number];
export type TeamInvitation = Team["invitations"][number];
export type InviteBody = JsonBody<
  Ops["/api/v1/tenant/team/invitations"]["post"]["requestBody"]
>;

export type AdminRequest = Ok<
  Ops["/api/v1/platform-admin/connection-requests"]["get"]
>[number];
export type AdminTenant = Ok<Ops["/api/v1/platform-admin/tenants"]["get"]>[number];
export type DeadLetter = Ok<Ops["/api/v1/platform-admin/dead-letters"]["get"]>[number];
export type AdminTenantDetail = Ok<
  Ops["/api/v1/platform-admin/tenants/{tenant_id}"]["get"]
>;
export type TransitionBody = JsonBody<
  Ops["/api/v1/platform-admin/connection-requests/{request_id}"]["put"]["requestBody"]
>;

export type SandboxTurn = Ok<
  Ops["/api/v1/tenant/sandbox/conversations/{conversation_id}/messages"]["post"]
>;
/** The trace of a turn; null when the turn produced no model answer. */
export type SandboxTrace = NonNullable<SandboxTurn["trace"]>;
/** One outbound message, typed the way the simulator draws it. */
export type Saliente = SandboxTurn["salientes"][number];
/** The debug panel of a turn (`?debug=true`). */
export type SandboxDebug = NonNullable<SandboxTurn["debug"]>;
/** What the customer did: wrote, or tapped a button or a list row. */
export type SandboxInput = ({ content: string } | { interactive_id: string; title: string }) & {
  /** Draft situations tried in this turn only (017, RF-009). */
  situaciones_prueba?: string[];
};
export type SandboxHistory = Ok<
  Ops["/api/v1/tenant/sandbox/conversations/{conversation_id}/messages"]["get"]
>;
export type SandboxMessage = SandboxHistory["items"][number];

export type PaymentMethod = Ok<Ops["/api/v1/tenant/payment-methods"]["get"]>[number];
export type PaymentMethodBody = JsonBody<
  Ops["/api/v1/tenant/payment-methods"]["post"]["requestBody"]
>;

export type InboxPage = Ok<Ops["/api/v1/tenant/conversations"]["get"]>;
export type InboxItem = InboxPage["items"][number];
export type InboxFilter = "todas" | "agente" | "necesitan_humano" | "tomadas" | "mias" | "cerradas";
export type InboxDetail = Ok<Ops["/api/v1/tenant/conversations/{conversation_id}"]["get"]>;
export type ThreadPage = Ok<Ops["/api/v1/tenant/conversations/{conversation_id}/messages"]["get"]>;
export type ThreadMessage = ThreadPage["items"][number];
export type InboxSend = Ok<Ops["/api/v1/tenant/conversations/{conversation_id}/messages"]["post"]>;
export type InboxNote = Ok<Ops["/api/v1/tenant/conversations/{conversation_id}/notes"]["post"]>;
export type InboxSettings = Ok<Ops["/api/v1/tenant/notification-settings"]["get"]>;
export type ConversationState = Ok<Ops["/api/v1/tenant/conversations/{conversation_id}/state"]["get"]>;
export type OrderPage = Ok<Ops["/api/v1/tenant/orders"]["get"]>;
export type OrderListItem = OrderPage["items"][number];
export type OrderSummary = Ok<Ops["/api/v1/tenant/orders/summary"]["get"]>;
export type OrderDetail = Ok<Ops["/api/v1/tenant/orders/{order_id}"]["get"]>;
export type OrderLine = OrderDetail["items"][number];
export type PaymentProof = OrderDetail["comprobantes"][number];
export type OrderHistoryEntry = OrderDetail["historial"][number];
export type ProofImage = Ok<
  Ops["/api/v1/tenant/orders/{order_id}/proofs/{proof_id}/image"]["get"]
>;
export type OrderDecision = Ok<Ops["/api/v1/tenant/orders/{order_id}/confirm"]["post"]>;
export type OrderStatusResult = Ok<Ops["/api/v1/tenant/orders/{order_id}/status"]["post"]>;
/** What the customer was told: sent, window closed (no WhatsApp), or not sent. */
export type CustomerNotice = OrderDecision["aviso_cliente"];
/** The states an operator can move a confirmed order to. */
export type OperatorState = JsonBody<
  Ops["/api/v1/tenant/orders/{order_id}/status"]["post"]["requestBody"]
>["estado"];
// ------------------------------------------------- the assistant by layers (017)
export type Overview = Ok<Ops["/api/v1/assistant/overview"]["get"]>;
export interface ChannelHealth {
  numeros_conectados: number;
  ultimo_webhook: string | null;
  recibidos_24h: number;
  con_error_24h: number;
}
// Analytics (Feature 013).
export interface FunnelStep {
  conversaciones: number;
  con_intencion: number;
  por_pagar: number;
  ganados: number;
  tasa_intencion: number | null;
  tasa_por_pagar: number | null;
  tasa_cierre: number | null;
  conversion: number | null;
}
export interface FunnelReport {
  desde: string;
  hasta: string;
  actual: FunnelStep;
  anterior: FunnelStep;
  por_origen: Record<string, { conversaciones: number; ganados: number }>;
  por_dia_semana: Record<string, { conversaciones: number; ganados: number }>;
  por_hora: Record<string, { conversaciones: number; ganados: number }>;
}
export interface ProductsReport {
  vendidos: { producto: string; unidades: number; monto?: Record<string, string> }[];
  pedidos: number;
  consultados_sin_venta: { producto: string; consultas: number }[];
  agotados_pedidos: { producto: string; consultas: number }[];
  ticket_promedio?: Record<string, string>;
}
export interface CsatReport {
  enviadas: number;
  respondidas: number;
  con_nota: number;
  promedio: number | null;
  distribucion: Record<string, number>;
  comentarios: { fecha: string | null; nota: number | null; comentario: string | null }[];
  asistente: { respuestas: number; promedio: number | null };
  por_persona?: { nombre: string; respuestas: number; promedio: number | null }[];
}
export interface AnalyticsSettings {
  csat_activo: boolean;
  reporte_mensual: boolean;
}
export interface NpsSummary {
  nps: number | null;
  respuestas: number;
  descartadas: number;
  segmentos: Record<string, Record<string, { nps: number | null; respuestas: number }>>;
  comentarios: { negocio: string; nota: number; comentario: string; fecha: string }[];
}
// Campaigns (Feature 011).
export type MessageTemplate = Ok<Ops["/api/v1/tenant/templates"]["post"]>;
export type LibraryTemplate = Ok<Ops["/api/v1/tenant/templates/library"]["get"]>[number];
export type ContactPage = Ok<Ops["/api/v1/tenant/contacts"]["get"]>;
export type ContactRow = ContactPage["items"][number];
export type TagCount = Ok<Ops["/api/v1/tenant/contacts/tags"]["get"]>[number];
export type ImportInfo = Ok<Ops["/api/v1/tenant/contacts/import"]["get"]>;
export type ImportResult = Ok<Ops["/api/v1/tenant/contacts/import"]["post"]>;
export type CampaignSettings = Ok<Ops["/api/v1/tenant/campaigns/settings"]["get"]>;
export type Campaign = Ok<Ops["/api/v1/tenant/campaigns"]["post"]>;
export type CampaignEstimate = Ok<Ops["/api/v1/tenant/campaigns/estimate"]["post"]>;
export type CampaignBody = JsonBody<Ops["/api/v1/tenant/campaigns"]["post"]["requestBody"]>;
export type Segment = NonNullable<CampaignBody["segment"]>;
export type TemplateBody = JsonBody<Ops["/api/v1/tenant/templates"]["post"]["requestBody"]>;
export type MetaRate = Ok<Ops["/api/v1/platform-admin/meta-rates"]["get"]>[number];
export interface CampaignReport {
  destinatarios: number;
  pendientes: number;
  enviados: number;
  entregados: number;
  leidos: number;
  respondieron: number;
  fallidos: number;
  omitidos: number;
  motivos_omision: Record<string, number>;
  bajas: number;
  ventas_atribuidas: number;
  // A/B (Feature 013).
  variantes?: Record<
    "A" | "B",
    {
      destinatarios: number;
      enviados: number;
      respondieron: number;
      ventas: number;
      tasa_respuesta: number | null;
      tasa_venta: number | null;
      muestra_pequena: boolean;
    }
  >;
  monto_vendido?: Record<string, string>;
  costo_estimado_usd?: string | null;
}
// The agenda (feature 019, phase D).
export type Scheduling = Ok<Ops["/api/v1/tenant/scheduling"]["get"]>;
export type SchedulingSettings = Scheduling["ajustes"];
export type SchedulingResource = Scheduling["recursos"][number];
export type SchedulingBlock = Scheduling["bloqueos"][number];
export type BookableService = Scheduling["servicios"][number];
export type AppointmentList = Ok<Ops["/api/v1/tenant/appointments"]["get"]>;
export type Appointment = AppointmentList["items"][number];
export type FreeSlots = Ok<Ops["/api/v1/tenant/scheduling/availability"]["get"]>;
export type ResourceBody = {
  nombre: string;
  horario: Record<string, string[][]> | null;
  product_ids: string[];
  is_active: boolean;
};
// CRM (Feature 012).
export type Pipeline = Ok<Ops["/api/v1/tenant/pipeline"]["get"]>;
export type PipelineStage = Pipeline["stages"][number];
export type LostReason = Pipeline["lost_reasons"][number];
export type LeadBoard = Ok<Ops["/api/v1/tenant/leads/board"]["get"]>;
export type LeadCard = LeadBoard["columns"][number]["items"][number];
export type LeadPage = Ok<Ops["/api/v1/tenant/leads"]["get"]>;
export type LeadDetail = Ok<Ops["/api/v1/tenant/leads/{lead_id}"]["get"]>;
export interface CrmMetrics {
  activos_por_etapa: Record<string, number>;
  nuevos: number;
  ganados: number;
  perdidos: number;
  perdidos_por_motivo: Record<string, number>;
  llegaron_a: Record<string, number>;
  conversion: number | null;
  dias_promedio_para_ganar: number | null;
  valor_pipeline?: Record<string, string>;
  vendido?: Record<string, string>;
}
export type Valoracion = JsonBody<
  Ops["/api/v1/tenant/sandbox/conversations/{conversation_id}/messages/{message_id}/feedback"]["put"]["requestBody"]
>;
export type InfoSection = "locales" | "entregas" | "tipos_cliente" | "devoluciones" | "contacto";
export type SectionData = Ok<Ops["/api/v1/assistant/business-info/{section}"]["get"]>;
export type Faq = Ok<Ops["/api/v1/assistant/faqs"]["get"]>["items"][number];
export type Promotion = Ok<Ops["/api/v1/assistant/promotions"]["get"]>["items"][number];
export type PromotionBody = JsonBody<Ops["/api/v1/assistant/promotions"]["post"]["requestBody"]>;
export type Behavior = Ok<Ops["/api/v1/assistant/behavior"]["get"]>;
export type BehaviorBody = JsonBody<Ops["/api/v1/assistant/behavior"]["put"]["requestBody"]>;
export type Category = Ok<Ops["/api/v1/assistant/categories"]["get"]>["items"][number];
export type ProcessTemplate = Ok<Ops["/api/v1/assistant/templates"]["get"]>["items"][number];
export type ProcessView = Ok<Ops["/api/v1/assistant/process"]["get"]>;
export type RuleView = ProcessView["etapas"][number]["reglas"][number];
export type RubroView = Ok<Ops["/api/v1/assistant/rubro"]["get"]>;
export type PromptPreview = Ok<Ops["/api/v1/assistant/prompt-preview"]["get"]>;
export type Situation = Ok<Ops["/api/v1/assistant/situations"]["get"]>["items"][number];
export type SituationBody = JsonBody<Ops["/api/v1/assistant/situations"]["post"]["requestBody"]>;
export type MediaAsset = Ok<Ops["/api/v1/assistant/media"]["get"]>["items"][number];
export type CatalogList = Ok<Ops["/api/v1/platform-admin/catalog"]["get"]>;
export type CatalogVersion = Ok<
  Ops["/api/v1/platform-admin/catalog/{kind}/{key}/versions/{number}"]["get"]
>;
export type CatalogContent = JsonBody<
  Ops["/api/v1/platform-admin/catalog/{kind}/{key}/versions/{number}"]["put"]["requestBody"]
>;
export type CatalogKind = "proceso" | "rubro";

export type OrderListQuery = {
  estado?: readonly string[];
  desde?: string;
  hasta?: string;
  q?: string;
  sandbox?: boolean;
  page?: number;
  page_size?: number;
};

export const api = {
  auth: {
    register: (body: RegisterBody) =>
      request<Ok<Ops["/api/v1/auth/register"]["post"]>>("/auth/register", {
        method: "POST",
        body,
      }),

    login: (body: LoginBody) =>
      request<Session>("/auth/login", { method: "POST", body, skipRefresh: true }),

    logout: () => request<void>("/auth/logout", { method: "POST", skipRefresh: true }),

    verifyEmail: (token: string) =>
      request<void>("/auth/verify-email", { method: "POST", body: { token }, skipRefresh: true }),

    resendVerification: (email: string) =>
      request<void>("/auth/resend-verification", {
        method: "POST",
        body: { email },
        skipRefresh: true,
      }),

    requestOtp: () => request<void>("/auth/phone/request-otp", { method: "POST" }),

    verifyOtp: (code: string) =>
      request<void>("/auth/phone/verify-otp", { method: "POST", body: { code } }),
  },

  tenant: {
    current: () => request<TenantSummary>("/tenant"),
    checklist: () => request<Checklist>("/tenant/activation-checklist"),
    usage: () => request<Usage>("/tenant/usage"),
  },

  agent: {
    activate: () => request<void>("/tenant/agent/activate", { method: "POST" }),
    deactivate: () => request<void>("/tenant/agent/deactivate", { method: "POST" }),
  },

  profile: {
    get: () => request<BusinessProfile>("/tenant/business-profile"),
    save: (body: BusinessProfilePatch) =>
      request<BusinessProfile>("/tenant/business-profile", { method: "PUT", body }),
  },

  scheduling: {
    overview: () => request<Scheduling>("/tenant/scheduling"),
    saveSettings: (body: Partial<Omit<SchedulingSettings, "plantilla_aprobada">>) =>
      request<SchedulingSettings>("/tenant/scheduling/settings", { method: "PUT", body }),
    createResource: (body: ResourceBody) =>
      request<SchedulingResource>("/tenant/scheduling/resources", { method: "POST", body }),
    saveResource: (id: string, body: ResourceBody) =>
      request<SchedulingResource>(`/tenant/scheduling/resources/${id}`, { method: "PUT", body }),
    deleteResource: (id: string) =>
      request<void>(`/tenant/scheduling/resources/${id}`, { method: "DELETE" }),
    createBlock: (body: { resource_id: string; desde: string; hasta: string; motivo?: string | null }) =>
      request<SchedulingBlock>("/tenant/scheduling/blocks", { method: "POST", body }),
    deleteBlock: (id: string) => request<void>(`/tenant/scheduling/blocks/${id}`, { method: "DELETE" }),
    availability: (query: { servicio_id: string; desde?: string; ignore?: string }) =>
      request<FreeSlots>("/tenant/scheduling/availability", { query }),
    appointments: (query: { desde: string; hasta?: string; recurso_id?: string }) =>
      request<AppointmentList>("/tenant/appointments", { query }),
    book: (body: {
      servicio_id: string;
      inicio: string;
      nombre_cliente: string;
      telefono?: string | null;
      notas?: string | null;
    }) => request<Appointment>("/tenant/appointments", { method: "POST", body }),
    act: (id: string, accion: "confirmar" | "completar" | "no_asistio" | "cancelar", motivo?: string) =>
      request<Appointment>(`/tenant/appointments/${id}/action`, {
        method: "POST",
        body: { accion, motivo: motivo ?? null },
      }),
    reschedule: (id: string, inicio: string) =>
      request<Appointment>(`/tenant/appointments/${id}/reschedule`, { method: "POST", body: { inicio } }),
  },

  agentConfig: {
    get: () => request<AgentConfig>("/tenant/agent-config"),
    save: (body: AgentConfigPatch) =>
      request<AgentConfig>("/tenant/agent-config", { method: "PUT", body }),
  },

  products: {
    list: (query?: {
      limit?: number;
      offset?: number;
      search?: string;
      category?: string;
      is_active?: boolean;
      tag?: string;
    }) => request<ProductPage>("/tenant/products", { query }),
    create: (body: ProductBody) =>
      request<Product>("/tenant/products", { method: "POST", body }),
    update: (id: string, body: ProductPatch) =>
      request<Product>(`/tenant/products/${id}`, { method: "PUT", body }),
    duplicate: (id: string) =>
      request<Product>(`/tenant/products/${id}/duplicate`, { method: "POST" }),
    remove: (id: string) => request<void>(`/tenant/products/${id}`, { method: "DELETE" }),
    photos: (id: string) => request<PhotoList>(`/tenant/products/${id}/photos`),
    addPhoto: (id: string, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<PhotoList>(`/tenant/products/${id}/photos`, { method: "POST", body: form });
    },
    orderPhotos: (id: string, ids: string[]) =>
      request<PhotoList>(`/tenant/products/${id}/photos/order`, { method: "PUT", body: { ids } }),
    removePhoto: (id: string, photoId: string) =>
      request<void>(`/tenant/products/${id}/photos/${photoId}`, { method: "DELETE" }),

    /** Upload a CSV or XLSX and get back what *would* be created. */
    preview: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<ImportPreview>("/tenant/products/import", { method: "POST", body: form });
    },
    confirmImport: (previewId: string) =>
      request<{ created: number }>(`/tenant/products/import/${previewId}/confirm`, {
        method: "POST",
      }),
  },

  knowledge: {
    list: () => request<KnowledgeList>("/tenant/knowledge"),
    addText: (body: { type: "faq" | "policy" | "text"; title: string; content: string }) =>
      request<{ id: string; status: string }>("/tenant/knowledge", { method: "POST", body }),
    addFile: (file: File, title: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("title", title);
      return request<{ id: string; status: string }>("/tenant/knowledge", {
        method: "POST",
        body: form,
      });
    },
    remove: (id: string) => request<void>(`/tenant/knowledge/${id}`, { method: "DELETE" }),
    // Feature 006.
    get: (id: string) => request<KnowledgeDetail>(`/tenant/knowledge/${id}`),
    update: (id: string, body: { title?: string; content?: string; priority?: number }) =>
      request<KnowledgeSource>(`/tenant/knowledge/${id}`, { method: "PATCH", body }),
    resync: (id: string) =>
      request<KnowledgeSource>(`/tenant/knowledge/${id}/resync`, { method: "POST" }),
    chunks: (id: string) => request<{ chunks: string[] }>(`/tenant/knowledge/${id}/chunks`),
    addWeb: (url: string, title?: string) =>
      request<{ id: string; status: string }>("/tenant/knowledge/web", {
        method: "POST",
        body: title ? { url, title } : { url },
      }),
  },

  metrics: {
    report: (query: { desde?: string; hasta?: string }) =>
      request<MetricsReport>("/tenant/metrics", { query }),
    exportCsv: (desde: string, hasta: string) =>
      requestBlob(`/tenant/metrics/export.csv?desde=${desde}&hasta=${hasta}`),
    settings: () => request<{ minutos_por_conversacion: number }>("/tenant/metrics/settings"),
    saveSettings: (minutos: number) =>
      request<{ minutos_por_conversacion: number }>("/tenant/metrics/settings", {
        method: "PUT",
        body: { minutos_por_conversacion: minutos },
      }),
    platform: (query: { desde?: string; hasta?: string }) =>
      request<PlatformMetrics>("/platform-admin/metrics", { query }),
    modelCosts: () => request<ModelCostRow[]>("/platform-admin/model-costs"),
    setModelCost: (body: {
      modelo: string;
      entrada_por_1k_usd: string;
      salida_por_1k_usd: string;
    }) => request<ModelCostRow>("/platform-admin/model-costs", { method: "PUT", body }),
  },

  billing: {
    plans: () => request<BillingCatalog>("/tenant/billing/plans"),
    overview: () => request<BillingOverview>("/tenant/billing"),
    checkout: (body: { plan_id: string; ciclo: BillingCycle; cupon?: string }) =>
      request<BillingCharge>("/tenant/billing/checkout", { method: "POST", body }),
    changePlan: (body: { plan_id: string; ciclo: BillingCycle }) =>
      request<BillingChange>("/tenant/billing/change-plan", { method: "POST", body }),
    cancel: () => request<unknown>("/tenant/billing/cancel", { method: "POST" }),
    resume: () => request<unknown>("/tenant/billing/resume", { method: "POST" }),
    addon: (id: string) =>
      request<BillingCharge>(`/tenant/billing/addons/${id}/checkout`, { method: "POST" }),
    cancelCharge: (id: string) =>
      request<BillingCharge>(`/tenant/billing/charges/${id}/cancel`, { method: "POST" }),
    receipt: (id: string) => requestBlob(`/tenant/billing/payments/${id}/receipt.pdf`),
  },

  admin: {
    channelHealth: () => request<ChannelHealth>("/platform-admin/channel-health"),
    metaRates: () => request<MetaRate[]>("/platform-admin/meta-rates"),
    saveMetaRate: (body: { country: string; category: "marketing" | "utility" | "service"; valid_from: string; rate_usd: string }) =>
      request<MetaRate[]>("/platform-admin/meta-rates", { method: "PUT", body }),
    billing: {
      subscriptions: () => request<AdminSubscription[]>("/platform-admin/billing/subscriptions"),
      recordPayment: (body: {
        referencia: string;
        operacion: string;
        monto_usd: string;
        monto_local?: string;
        moneda_local?: string;
        nota?: string;
      }) => request<AdminPayment>("/platform-admin/billing/payments", { method: "POST", body }),
      coupons: () => request<AdminCoupon[]>("/platform-admin/billing/coupons"),
      createCoupon: (body: { codigo: string; porcentaje: number; vence?: string; usos_maximos?: number }) =>
        request<AdminCoupon>("/platform-admin/billing/coupons", { method: "POST", body }),
      extend: (tenantId: string, body: { dias: number; motivo: string }) =>
        request<unknown>(`/platform-admin/billing/tenants/${tenantId}/extend`, {
          method: "POST",
          body,
        }),
    },
    queue: (status?: string) =>
      request<AdminRequest[]>("/platform-admin/connection-requests", {
        query: status ? { status } : undefined,
      }),
    transition: (id: string, body: TransitionBody) =>
      request<AdminRequest>(`/platform-admin/connection-requests/${id}`, {
        method: "PUT",
        body,
      }),
    tenants: (query?: { search?: string; status?: string }) =>
      request<AdminTenant[]>("/platform-admin/tenants", { query }),
    tenant: (id: string) => request<AdminTenantDetail>(`/platform-admin/tenants/${id}`),

    deadLetters: () => request<DeadLetter[]>("/platform-admin/dead-letters"),

    retryDeadLetter: (id: string) =>
      request<{ id: string; queued: boolean }>(`/platform-admin/dead-letters/${id}/retry`, {
        method: "POST",
      }),
    extendTrial: (id: string, days: number, reason: string) =>
      request<AdminTenantDetail>(`/platform-admin/tenants/${id}/extend-trial`, {
        method: "POST",
        body: { days, reason },
      }),
  },

  connection: {
    list: () => request<ConnectionRequest[]>("/tenant/connection-requests"),
    create: (body: ConnectionRequestBody) =>
      request<ConnectionRequest>("/tenant/connection-requests", { method: "POST", body }),
    /** Connects the number with the business's own YCloud account: Leo checks the key,
     * creates the webhook there and stores its secret. */
    connect: (body: ConnectNumberBody) =>
      request<WhatsAppNumber>("/tenant/whatsapp-number", { method: "POST", body }),
    /** The live number, or `null`. A 404 here means "none yet", which is a
     * state and not an error - so it is translated rather than thrown. */
    number: async () => {
      try {
        return await request<WhatsAppNumber>("/tenant/whatsapp-number");
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 404) return null;
        throw caught;
      }
    },
  },

  analytics: {
    funnel: (query: { desde?: string; hasta?: string }) =>
      request<FunnelReport>("/tenant/analytics/funnel", { query }),
    products: (query: { desde?: string; hasta?: string }) =>
      request<ProductsReport>("/tenant/analytics/products", { query }),
    csat: (query: { desde?: string; hasta?: string }) =>
      request<CsatReport>("/tenant/analytics/csat", { query }),
    settings: () => request<AnalyticsSettings>("/tenant/analytics/settings"),
    saveSettings: (body: AnalyticsSettings) =>
      request<AnalyticsSettings>("/tenant/analytics/settings", { method: "PUT", body }),
    nps: () => request<{ preguntar: boolean; ciclo: string | null }>("/tenant/nps"),
    answerNps: (body: { score: number | null; comment?: string | null }) =>
      request<void>("/tenant/nps", { method: "POST", body }),
    platformNps: () => request<NpsSummary>("/platform-admin/nps"),
    monthlyPreview: async (month: string) => (await requestBlob(`/tenant/reports/monthly/${month}`)).text(),
  },

  templates: {
    list: () => request<MessageTemplate[]>("/tenant/templates"),
    library: () => request<LibraryTemplate[]>("/tenant/templates/library"),
    clone: (key: string) =>
      request<MessageTemplate>(`/tenant/templates/library/${key}/clone`, { method: "POST" }),
    create: (body: TemplateBody) =>
      request<MessageTemplate>("/tenant/templates", { method: "POST", body }),
    update: (id: string, body: TemplateBody) =>
      request<MessageTemplate>(`/tenant/templates/${id}`, { method: "PUT", body }),
    duplicate: (id: string) =>
      request<MessageTemplate>(`/tenant/templates/${id}/duplicate`, { method: "POST" }),
    remove: (id: string) => request<void>(`/tenant/templates/${id}`, { method: "DELETE" }),
    submit: (id: string) =>
      request<MessageTemplate>(`/tenant/templates/${id}/submit`, { method: "POST" }),
  },

  contacts: {
    list: (query: { q?: string; tag?: string; permiso?: string; page?: number }) =>
      request<ContactPage>("/tenant/contacts", { query }),
    tags: () => request<TagCount[]>("/tenant/contacts/tags"),
    setTags: (id: string, tags: string[]) =>
      request<string[]>(`/tenant/contacts/${id}/tags`, { method: "PUT", body: { tags } }),
    importInfo: () => request<ImportInfo>("/tenant/contacts/import"),
    importCsv: (file: File, accepted: boolean, tags: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("acepto", accepted ? "true" : "false");
      form.append("etiquetas", tags);
      return request<ImportResult>("/tenant/contacts/import", { method: "POST", body: form });
    },
  },

  campaigns: {
    list: () => request<Campaign[]>("/tenant/campaigns"),
    get: (id: string) => request<Campaign>(`/tenant/campaigns/${id}`),
    estimate: (templateId: string, segment: Segment) =>
      request<CampaignEstimate>("/tenant/campaigns/estimate", {
        method: "POST",
        body: { template_id: templateId, segment },
      }),
    create: (body: CampaignBody) => request<Campaign>("/tenant/campaigns", { method: "POST", body }),
    update: (id: string, body: CampaignBody) =>
      request<Campaign>(`/tenant/campaigns/${id}`, { method: "PUT", body }),
    remove: (id: string) => request<void>(`/tenant/campaigns/${id}`, { method: "DELETE" }),
    act: (id: string, action: "schedule" | "pause" | "resume" | "cancel") =>
      request<Campaign>(`/tenant/campaigns/${id}/${action}`, { method: "POST" }),
    settings: () => request<CampaignSettings>("/tenant/campaigns/settings"),
    saveSettings: (body: { optin_preguntar: boolean; seguimiento_plantilla_id: string | null }) =>
      request<CampaignSettings>("/tenant/campaigns/settings", { method: "PUT", body }),
  },

  crm: {
    pipeline: () => request<Pipeline>("/tenant/pipeline"),
    saveStages: (body: { key?: string | null; label: string; is_active?: boolean }[]) =>
      request<Pipeline>("/tenant/pipeline/stages", { method: "PUT", body }),
    saveReasons: (body: { key?: string | null; label: string; is_active?: boolean }[]) =>
      request<Pipeline>("/tenant/pipeline/lost-reasons", { method: "PUT", body }),
    board: (query?: { asignado?: string; search?: string; puntaje?: "caliente" | "tibio" | "frio" }) =>
      request<LeadBoard>("/tenant/leads/board", { query }),
    today: (dias = 3) => request<LeadPage>("/tenant/leads/today", { query: { dias } }),
    detail: (id: string) => request<LeadDetail>(`/tenant/leads/${id}`),
    move: (id: string, stageId: string, lostReason?: string) =>
      request<LeadDetail>(`/tenant/leads/${id}/move`, {
        method: "POST",
        body: { stage_id: stageId, lost_reason: lostReason ?? null },
      }),
    assign: (id: string, userId: string | null) =>
      request<LeadDetail>(`/tenant/leads/${id}/assign`, { method: "POST", body: { user_id: userId } }),
    discard: (id: string) => request<void>(`/tenant/leads/${id}/discard`, { method: "POST" }),
    quoteSent: (id: string, body: { monto?: string | null; moneda?: "PEN" | "USD" | null } = {}) =>
      request<LeadDetail>(`/tenant/leads/${id}/quote/sent`, { method: "POST", body }),
    note: (id: string, content: string) =>
      request<unknown>(`/tenant/leads/${id}/notes`, { method: "POST", body: { content } }),
    metrics: () => request<CrmMetrics>("/tenant/leads/metrics"),
    exportCsv: () => requestBlob("/tenant/leads/export.csv"),
  },

  team: {
    get: () => request<Team>("/tenant/team"),
    invite: (body: InviteBody) =>
      request<TeamInvitation>("/tenant/team/invitations", { method: "POST", body }),
    resendInvitation: (id: string) =>
      request<void>(`/tenant/team/invitations/${id}/resend`, { method: "POST" }),
    revokeInvitation: (id: string) =>
      request<void>(`/tenant/team/invitations/${id}`, { method: "DELETE" }),
    // PUT: the backend has no PATCH here (a 405 broke role changes before 017).
    updateMember: (
      membershipId: string,
      body: { role?: string; status?: string; puede_revisar?: boolean },
    ) => request<TeamMember>(`/tenant/team/members/${membershipId}`, { method: "PUT", body }),
  },

  sandbox: {
    start: () =>
      request<{ conversation_id: string }>("/tenant/sandbox/conversations", { method: "POST" }),
    send: (conversationId: string, input: SandboxInput, debug = false) =>
      request<SandboxTurn>(`/tenant/sandbox/conversations/${conversationId}/messages`, {
        method: "POST",
        body: input,
        query: debug ? { debug: true } : undefined,
      }),
    sendImage: (conversationId: string, image: File, debug = false, drafts: string[] = []) => {
      const form = new FormData();
      form.append("image", image);
      if (drafts.length) form.append("situaciones_prueba", drafts.join(","));
      return request<SandboxTurn>(`/tenant/sandbox/conversations/${conversationId}/messages`, {
        method: "POST",
        body: form,
        query: debug ? { debug: true } : undefined,
      });
    },
    history: (conversationId: string) =>
      request<SandboxHistory>(`/tenant/sandbox/conversations/${conversationId}/messages`),
    /** What the agent said after a message: confirmations, handovers, follow-ups. */
    after: (conversationId: string, messageId: string, signal?: AbortSignal) =>
      request<SandboxHistory>(`/tenant/sandbox/conversations/${conversationId}/messages`, {
        query: { after: messageId },
        signal,
      }),
    reset: (conversationId: string) =>
      request<void>(`/tenant/sandbox/conversations/${conversationId}/reset`, { method: "POST" }),
    /** ¿Respondió bien? (Feature 018): thumbs up or down on a reply, with the reason. */
    rate: (conversationId: string, messageId: string, body: Valoracion) =>
      request<Valoracion>(
        `/tenant/sandbox/conversations/${conversationId}/messages/${messageId}/feedback`,
        { method: "PUT", body },
      ),
    unrate: (conversationId: string, messageId: string) =>
      request<void>(`/tenant/sandbox/conversations/${conversationId}/messages/${messageId}/feedback`, {
        method: "DELETE",
      }),
  },

  payments: {
    list: () => request<PaymentMethod[]>("/tenant/payment-methods"),
    create: (body: PaymentMethodBody) =>
      request<PaymentMethod>("/tenant/payment-methods", { method: "POST", body }),
    update: (id: string, body: Partial<PaymentMethodBody>) =>
      request<PaymentMethod>(`/tenant/payment-methods/${id}`, { method: "PUT", body }),
    remove: (id: string) => request<void>(`/tenant/payment-methods/${id}`, { method: "DELETE" }),
  },
  assistant: {
    overview: () => request<Overview>("/assistant/overview"),
    section: (section: InfoSection) => request<SectionData>(`/assistant/business-info/${section}`),
    saveSection: (section: InfoSection, body: Record<string, unknown>) =>
      request<SectionData>(`/assistant/business-info/${section}`, { method: "PUT", body }),
    faqs: () => request<{ items: Faq[] }>("/assistant/faqs"),
    createFaq: (body: { pregunta: string; respuesta: string }) =>
      request<Faq>("/assistant/faqs", { method: "POST", body }),
    updateFaq: (id: string, body: Partial<{ pregunta: string; respuesta: string; is_active: boolean }>) =>
      request<Faq>(`/assistant/faqs/${id}`, { method: "PATCH", body }),
    removeFaq: (id: string) => request<void>(`/assistant/faqs/${id}`, { method: "DELETE" }),
    promotions: () => request<{ items: Promotion[] }>("/assistant/promotions"),
    createPromotion: (body: PromotionBody) =>
      request<Promotion>("/assistant/promotions", { method: "POST", body }),
    updatePromotion: (id: string, body: PromotionBody) =>
      request<Promotion>(`/assistant/promotions/${id}`, { method: "PUT", body }),
    removePromotion: (id: string) =>
      request<void>(`/assistant/promotions/${id}`, { method: "DELETE" }),
    behavior: () => request<Behavior>("/assistant/behavior"),
    saveBehavior: (body: BehaviorBody) =>
      request<Behavior>("/assistant/behavior", { method: "PUT", body }),
    categories: () => request<{ items: Category[] }>("/assistant/categories"),
    createCategory: (body: { nombre: string; orden?: number; activa?: boolean }) =>
      request<Category>("/assistant/categories", { method: "POST", body }),
    saveCategory: (id: string, body: { nombre: string; orden: number; activa: boolean }) =>
      request<Category>(`/assistant/categories/${id}`, { method: "PUT", body }),
    templates: () => request<{ items: ProcessTemplate[] }>("/assistant/templates"),
    process: () => request<ProcessView>("/assistant/process"),
    saveProcess: (template_key: string, options: Record<string, unknown> = {}) =>
      request<ProcessView>("/assistant/process", { method: "PUT", body: { template_key, options } }),
    /** Give the template back to the platform: it follows what the agent does again. */
    automaticProcess: () =>
      request<ProcessView>("/assistant/process/automatica", { method: "POST" }),
    switchRule: (kind: CatalogKind, ruleKey: string, apagada: boolean) =>
      request<RuleView>(`/assistant/rules/${kind}/${encodeURIComponent(ruleKey)}`, {
        method: "PUT",
        body: { apagada },
      }),
    rubro: () => request<RubroView>("/assistant/rubro"),
    newsSeen: (kind: CatalogKind) =>
      request<void>(`/assistant/novedades/${kind}/vistas`, { method: "POST" }),
    preview: (stage: string) =>
      request<PromptPreview>("/assistant/prompt-preview", { query: { stage } }),
    situations: (query?: { estado?: string; origen?: string }) =>
      request<{ items: Situation[] }>("/assistant/situations", { query }),
    createSituation: (body: SituationBody) =>
      request<Situation>("/assistant/situations", { method: "POST", body }),
    updateSituation: (id: string, body: SituationBody) =>
      request<Situation>(`/assistant/situations/${id}`, { method: "PUT", body }),
    removeSituation: (id: string) =>
      request<void>(`/assistant/situations/${id}`, { method: "DELETE" }),
    setSituation: (id: string, active: boolean) =>
      request<Situation>(`/assistant/situations/${id}/${active ? "activar" : "desactivar"}`, {
        method: "POST",
      }),
    fromSuggested: (id: string, valores: Record<string, string>) =>
      request<Situation>(`/assistant/situations/desde-sugerida/${id}`, {
        method: "POST",
        body: { valores },
      }),
    media: () => request<{ items: MediaAsset[] }>("/assistant/media"),
    uploadMedia: (file: File, fields: { clave: string; titulo: string; cuando_usar: string; caption?: string }) => {
      const form = new FormData();
      form.append("file", file);
      for (const [key, value] of Object.entries(fields)) {
        if (value) form.append(key, value);
      }
      return request<MediaAsset>("/assistant/media", { method: "POST", body: form });
    },
    updateMedia: (id: string, body: Partial<{ clave: string; titulo: string; cuando_usar: string; caption: string; is_active: boolean }>) =>
      request<MediaAsset>(`/assistant/media/${id}`, { method: "PATCH", body }),
    replaceMediaFile: (id: string, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<MediaAsset>(`/assistant/media/${id}/file`, { method: "POST", body: form });
    },
    removeMedia: (id: string) => request<void>(`/assistant/media/${id}`, { method: "DELETE" }),
  },

  catalog: {
    list: () => request<CatalogList>("/platform-admin/catalog"),
    get: (kind: CatalogKind, key: string, version: number) =>
      request<CatalogVersion>(`/platform-admin/catalog/${kind}/${key}/versions/${version}`),
    draft: (kind: CatalogKind, key: string) =>
      request<CatalogVersion>(`/platform-admin/catalog/${kind}/${key}/versions`, { method: "POST" }),
    save: (kind: CatalogKind, key: string, version: number, body: CatalogContent) =>
      request<CatalogVersion>(`/platform-admin/catalog/${kind}/${key}/versions/${version}`, {
        method: "PUT",
        body,
      }),
    runEvals: (kind: CatalogKind, key: string, version: number) =>
      request<{ estado: string }>(
        `/platform-admin/catalog/${kind}/${key}/versions/${version}/evals`,
        { method: "POST" },
      ),
    publish: (kind: CatalogKind, key: string, version: number) =>
      request<CatalogVersion>(
        `/platform-admin/catalog/${kind}/${key}/versions/${version}/publicar`,
        { method: "POST" },
      ),
  },

  conversations: {
    state: (id: string) => request<ConversationState>(`/tenant/conversations/${id}/state`),
    resumeBot: (id: string) =>
      request<ConversationState>(`/tenant/conversations/${id}/resume-bot`, { method: "POST" }),
    // The inbox (Feature 007).
    list: (
      query: { filtro?: InboxFilter; q?: string; pruebas?: boolean; cursor?: string },
      signal?: AbortSignal,
    ) => request<InboxPage>("/tenant/conversations", { query, signal }),
    get: (id: string) => request<InboxDetail>(`/tenant/conversations/${id}`),
    thread: (id: string, antesDe?: string) =>
      request<ThreadPage>(`/tenant/conversations/${id}/messages`, {
        query: antesDe ? { antes_de: antesDe } : {},
      }),
    image: (id: string, messageId: string) =>
      request<{ url: string; expira_en: number }>(
        `/tenant/conversations/${id}/messages/${messageId}/image`,
      ),
    take: (id: string) => request<InboxDetail>(`/tenant/conversations/${id}/takeover`, { method: "POST" }),
    release: (id: string) =>
      request<InboxDetail>(`/tenant/conversations/${id}/release`, { method: "POST" }),
    steal: (id: string) => request<InboxDetail>(`/tenant/conversations/${id}/steal`, { method: "POST" }),
    close: (id: string) => request<InboxDetail>(`/tenant/conversations/${id}/close`, { method: "POST" }),
    reopen: (id: string) => request<InboxDetail>(`/tenant/conversations/${id}/reopen`, { method: "POST" }),
    optOut: (id: string) => request<InboxDetail>(`/tenant/conversations/${id}/opt-out`, { method: "POST" }),
    send: (id: string, texto: string) =>
      request<InboxSend>(`/tenant/conversations/${id}/messages`, { method: "POST", body: { texto } }),
    notes: (id: string) => request<{ items: InboxNote[] }>(`/tenant/conversations/${id}/notes`),
    addNote: (id: string, texto: string) =>
      request<InboxNote>(`/tenant/conversations/${id}/notes`, { method: "POST", body: { texto } }),
    settings: () => request<InboxSettings>("/tenant/notification-settings"),
    saveSettings: (body: InboxSettings) =>
      request<InboxSettings>("/tenant/notification-settings", { method: "PUT", body }),
  },
  orders: {
    list: (query: OrderListQuery, signal?: AbortSignal) =>
      request<OrderPage>("/tenant/orders", { query, signal }),
    summary: (periodo: "hoy" | "7d" | "30d", signal?: AbortSignal) =>
      request<OrderSummary>("/tenant/orders/summary", { query: { periodo }, signal }),
    get: (id: string) => request<OrderDetail>(`/tenant/orders/${id}`),
    /** A signed link that lives ten minutes; ask again rather than keep it. */
    /** An image the model did not take for a receipt, kept on the order (D-4). */
    noteImage: (id: string, imageId: string) =>
      request<ProofImage>(`/tenant/orders/${id}/notes/${imageId}/image`),
    proofImage: (id: string, proofId: string) =>
      request<ProofImage>(`/tenant/orders/${id}/proofs/${proofId}/image`),
    confirm: (id: string, siguientePaso?: string) =>
      request<OrderDecision>(`/tenant/orders/${id}/confirm`, {
        method: "POST",
        body: siguientePaso ? { siguiente_paso: siguientePaso } : {},
      }),
    reject: (id: string, motivo: string) =>
      request<OrderDecision>(`/tenant/orders/${id}/reject`, { method: "POST", body: { motivo } }),
    setStatus: (id: string, estado: OperatorState, motivo?: string) =>
      request<OrderStatusResult>(`/tenant/orders/${id}/status`, {
        method: "POST",
        body: motivo ? { estado, motivo } : { estado },
      }),
  },
};
