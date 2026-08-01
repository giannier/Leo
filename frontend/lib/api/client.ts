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

const BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const PREFIX = "/api/v1";

/** Paths that must never trigger the refresh dance. */
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

export function getAccessToken(): string | null {
  return accessToken;
}

export function onSessionEnded(handler: (() => void) | null): void {
  onSessionLost = handler;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
  /** Set for the calls that must not attempt a refresh on 401. */
  skipRefresh?: boolean;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(`${BASE_URL}${PREFIX}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) {
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

/** One typed request, with the 401 retry built in. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
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
export type ChecklistItem = Checklist["items"][number];

export type BusinessProfile = Ok<Ops["/api/v1/tenant/business-profile"]["get"]>;
export type BusinessProfilePatch = JsonBody<
  Ops["/api/v1/tenant/business-profile"]["put"]["requestBody"]
>;
export type AgentConfig = Ok<Ops["/api/v1/tenant/agent-config"]["get"]>;
export type AgentConfigPatch = JsonBody<Ops["/api/v1/tenant/agent-config"]["put"]["requestBody"]>;

export type ProductPage = Ok<Ops["/api/v1/tenant/products"]["get"]>;
export type Product = ProductPage["items"][number];
export type ProductBody = JsonBody<Ops["/api/v1/tenant/products"]["post"]["requestBody"]>;
export type ImportPreview = Ok<Ops["/api/v1/tenant/products/import"]["post"]>;

export type KnowledgeList = Ok<Ops["/api/v1/tenant/knowledge"]["get"]>;
export type KnowledgeSource = KnowledgeList["sources"][number];
export type KnowledgeUsage = KnowledgeList["usage"];

export type PaymentMethod = Ok<Ops["/api/v1/tenant/payment-methods"]["get"]>[number];
export type PaymentMethodBody = JsonBody<
  Ops["/api/v1/tenant/payment-methods"]["post"]["requestBody"]
>;

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

  agentConfig: {
    get: () => request<AgentConfig>("/tenant/agent-config"),
    save: (body: AgentConfigPatch) =>
      request<AgentConfig>("/tenant/agent-config", { method: "PUT", body }),
  },

  products: {
    list: (query?: { limit?: number; offset?: number; search?: string }) =>
      request<ProductPage>("/tenant/products", { query }),
    create: (body: ProductBody) =>
      request<Product>("/tenant/products", { method: "POST", body }),
    remove: (id: string) => request<void>(`/tenant/products/${id}`, { method: "DELETE" }),

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
  },

  payments: {
    list: () => request<PaymentMethod[]>("/tenant/payment-methods"),
    create: (body: PaymentMethodBody) =>
      request<PaymentMethod>("/tenant/payment-methods", { method: "POST", body }),
    update: (id: string, body: Partial<PaymentMethodBody>) =>
      request<PaymentMethod>(`/tenant/payment-methods/${id}`, { method: "PATCH", body }),
    remove: (id: string) => request<void>(`/tenant/payment-methods/${id}`, { method: "DELETE" }),
  },
};
