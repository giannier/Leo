/**
 * What a visitor's page calls (feature 019, phase F): the public chat, with no login.
 *
 * Apart from the panel's client on purpose: no access token, no refresh, no idempotency keys. The visitor's
 * only credential is the session token the server signs for their conversation, kept in the browser.
 */

const BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export interface ChatConfig {
  negocio: string;
  asistente: string;
  saludo: string | null;
  color: string;
}

export interface ChatLine {
  id: string;
  de: "visitante" | "asistente" | "equipo";
  texto: string;
  en: string;
}

export class ChatError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit & { token?: string | null } = {}): Promise<T> {
  const { token, ...rest } = init;
  const response = await fetch(`${BASE_URL}/api/v1/public/chat${path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Chat-Session": token } : {}),
    },
  });
  if (!response.ok) {
    let code = "error";
    let message = "No se pudo completar. Intenta de nuevo.";
    try {
      const body = (await response.json()) as { error?: string; message?: string };
      code = body.error ?? code;
      message = body.message ?? message;
    } catch {
      // A body that is not JSON keeps the generic message.
    }
    throw new ChatError(response.status, code, message);
  }
  return (await response.json()) as T;
}

export const publicChat = {
  config: (key: string) => call<ChatConfig>(`/${key}/config`),
  open: (key: string) => call<{ token: string }>(`/${key}/session`, { method: "POST" }),
  write: (key: string, token: string, texto: string) =>
    call<ChatLine>(`/${key}/messages`, { method: "POST", token, body: JSON.stringify({ texto }) }),
  read: (key: string, token: string, after?: string) =>
    call<{ items: ChatLine[] }>(`/${key}/messages${after ? `?after=${encodeURIComponent(after)}` : ""}`, {
      token,
    }),
};
