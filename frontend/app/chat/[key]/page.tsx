"use client";

/**
 * The business's public chat (feature 019, phase F): a page anybody can open from a link in a social
 * network's bio, or inside the widget on a website (`?embed=1`).
 *
 * The visitor's session is made on their first message, not on opening the page, so somebody who only
 * looks creates no contact. Its token lives in this browser. While an answer is awaited the page asks for
 * news every two seconds, and every eight otherwise.
 */

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { ChatError, publicChat, type ChatConfig, type ChatLine } from "@/lib/api/public-chat";

const MAX_TEXT = 500;
const FAST_MS = 2_000;
const SLOW_MS = 8_000;
// How long to keep asking quickly for an answer before assuming nobody is going to write.
const WAIT_MS = 60_000;

function storageKey(key: string) {
  return `leo-chat-${key}`;
}

function remembered(key: string): { token: string | null; lines: ChatLine[] } {
  try {
    const raw = window.localStorage.getItem(storageKey(key));
    if (raw) return JSON.parse(raw) as { token: string | null; lines: ChatLine[] };
  } catch {
    // Private windows and blocked storage: the chat works, it just forgets on reload.
  }
  return { token: null, lines: [] };
}

function remember(key: string, state: { token: string | null; lines: ChatLine[] }) {
  try {
    window.localStorage.setItem(storageKey(key), JSON.stringify(state));
  } catch {
    // See `remembered`.
  }
}

function Chat() {
  const { key } = useParams<{ key: string }>();
  const embed = useSearchParams().get("embed") === "1";
  const [config, setConfig] = useState<ChatConfig | null>(null);
  const [missing, setMissing] = useState(false);
  const [lines, setLines] = useState<ChatLine[]>([]);
  const [token, setToken] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [waitingSince, setWaitingSince] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const saved = remembered(key);
    setToken(saved.token);
    setLines(saved.lines);
    publicChat
      .config(key)
      .then(setConfig)
      .catch(() => setMissing(true));
  }, [key]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [lines, waitingSince]);

  const poll = useCallback(async () => {
    if (!token) return;
    const heard = [...lines].reverse().find((line) => line.de !== "visitante");
    try {
      const news = await publicChat.read(key, token, heard?.en);
      if (news.items.length) {
        const next = [...lines, ...news.items.filter((n) => !lines.some((l) => l.id === n.id))];
        setLines(next);
        remember(key, { token, lines: next });
        setWaitingSince(null);
      }
    } catch (caught) {
      if (caught instanceof ChatError && caught.status === 401) {
        // The session is gone (expired or forged): the next message opens a new one.
        setToken(null);
        remember(key, { token: null, lines });
      }
    }
  }, [key, token, lines]);

  useEffect(() => {
    if (!token) return;
    const waiting = waitingSince !== null && Date.now() - waitingSince < WAIT_MS;
    const timer = setInterval(() => void poll(), waiting ? FAST_MS : SLOW_MS);
    return () => clearInterval(timer);
  }, [token, poll, waitingSince]);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const clean = text.trim();
    if (!clean || busy) return;
    setBusy(true);
    setError(null);
    try {
      let session = token;
      if (!session) {
        session = (await publicChat.open(key)).token;
        setToken(session);
      }
      const sent = await publicChat.write(key, session, clean);
      const next = [...lines, sent];
      setLines(next);
      remember(key, { token: session, lines: next });
      setText("");
      setWaitingSince(Date.now());
    } catch (caught) {
      if (caught instanceof ChatError && caught.status === 401) {
        setToken(null);
        setError("Se venció la conversación. Escribe de nuevo para empezar otra.");
      } else {
        setError(caught instanceof ChatError ? caught.message : "No se pudo enviar. Intenta de nuevo.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (missing) {
    return (
      <main className="flex min-h-dvh items-center justify-center p-6 text-center text-ink-soft">
        Este chat no está disponible.
      </main>
    );
  }
  if (!config) return <main className="min-h-dvh" aria-busy="true" />;

  const color = config.color;
  const greeting = config.saludo;
  const typing = waitingSince !== null && Date.now() - waitingSince < WAIT_MS;

  return (
    <main className={`mx-auto flex h-dvh w-full max-w-xl flex-col bg-surface ${embed ? "" : "sm:my-4 sm:h-[calc(100dvh-2rem)] sm:rounded-2xl sm:border sm:border-line sm:shadow-lg"}`}>
      <header className="flex items-center gap-3 px-4 py-3 text-white" style={{ background: color }}>
        <span className="flex size-9 items-center justify-center rounded-full bg-white/20 font-heading font-semibold">
          {config.asistente.slice(0, 1).toUpperCase()}
        </span>
        <div>
          <h1 className="font-heading text-base font-semibold leading-tight">{config.negocio}</h1>
          <p className="text-xs opacity-90">{config.asistente} · asistente virtual</p>
        </div>
      </header>

      <div className="flex-1 space-y-2 overflow-y-auto bg-surface-muted p-3" aria-live="polite">
        {greeting ? (
          <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-surface px-3 py-2 text-sm text-ink shadow-sm">{greeting}</div>
        ) : null}
        {lines.map((line) => (
          <div
            key={line.id}
            className={line.de === "visitante" ? "flex justify-end" : "flex justify-start"}
          >
            <div
              className={
                line.de === "visitante"
                  ? "max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm px-3 py-2 text-sm text-white shadow-sm"
                  : "max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-bl-sm bg-surface px-3 py-2 text-sm text-ink shadow-sm"
              }
              style={line.de === "visitante" ? { background: color } : undefined}
            >
              {line.de === "equipo" ? <span className="mb-0.5 block text-[11px] font-semibold opacity-70">Equipo</span> : null}
              {line.texto}
            </div>
          </div>
        ))}
        {typing ? <p className="text-xs text-ink-faint">{config.asistente} está escribiendo…</p> : null}
        <div ref={bottom} />
      </div>

      {error ? (
        <p role="alert" className="bg-danger-soft px-3 py-2 text-xs text-danger">
          {error}
        </p>
      ) : null}
      <form onSubmit={send} className="flex items-end gap-2 border-t border-line bg-surface p-3">
        <label className="sr-only" htmlFor="chat-text">
          Tu mensaje
        </label>
        <textarea
          id="chat-text"
          value={text}
          maxLength={MAX_TEXT}
          rows={1}
          placeholder="Escribe tu mensaje"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(e as unknown as React.FormEvent);
            }
          }}
          className="max-h-32 min-h-11 flex-1 resize-none rounded-xl border border-line-strong bg-surface px-3 py-2.5 text-base text-ink placeholder:text-ink-faint"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="min-h-11 cursor-pointer rounded-xl px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
          style={{ background: color }}
        >
          Enviar
        </button>
      </form>
    </main>
  );
}

export default function PublicChatPage() {
  return (
    <Suspense fallback={<main className="min-h-dvh" aria-busy="true" />}>
      <Chat />
    </Suspense>
  );
}
