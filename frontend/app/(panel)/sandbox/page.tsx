"use client";

/**
 * Modo prueba (HU-03).
 *
 * The same agent, the same prompt, the same catalogue and the same RAG a real
 * customer would get - only the channel differs. That is the whole value of
 * it: a simulator that answered differently would tell the owner nothing
 * about what their customers will see.
 *
 * Two things the spec insists on, and this page is built around both:
 *
 * - **It never consumes trial quota** (Clarification 002), and it says so
 *   where it cannot be missed. An owner who suspects each test costs them a
 *   message stops testing, and testing is what makes their agent good.
 * - **Every reply exposes its trace** - which tools ran, which sources were
 *   used. That is the difference between "the agent said something wrong" and
 *   "the agent said something wrong *because* my delivery policy was never
 *   indexed", and only the second one is fixable.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AlertIcon, BookIcon, ChatIcon, SparkIcon, SpinnerIcon } from "@/components/icons";
import { Alert, Button, Skeleton, cx } from "@/components/ui";
import { api, type SandboxTrace } from "@/lib/api/client";
import { ApiError, humanMessage } from "@/lib/api/errors";

interface Turn {
  id: string;
  role: "customer" | "agent";
  content: string;
  trace?: SandboxTrace;
  degraded?: boolean;
}

const SUGGESTIONS = [
  "Hola, ¿qué venden?",
  "¿Cuánto cuesta?",
  "¿Hacen delivery?",
  "¿Cómo puedo pagar?",
];

export default function SandboxPage() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notReady, setNotReady] = useState(false);
  const [starting, setStarting] = useState(true);

  const endOfThread = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const start = useCallback(async () => {
    setStarting(true);
    setError(null);
    setNotReady(false);
    setTurns([]);
    try {
      const { conversation_id } = await api.sandbox.start();
      setConversationId(conversation_id);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "sandbox_not_ready") {
        setNotReady(true);
      } else {
        setError(humanMessage(caught));
      }
    } finally {
      setStarting(false);
    }
  }, []);

  useEffect(() => {
    void start();
  }, [start]);

  // Follow the conversation as it grows, the way a chat should.
  useEffect(() => {
    endOfThread.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, sending]);

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || !conversationId || sending) return;

      setDraft("");
      setError(null);
      // The question appears immediately. Waiting for the round trip to show
      // what you just typed makes the whole thing feel broken on a slow
      // connection - which is the one these customers are on.
      const localId = `local-${Date.now()}`;
      setTurns((previous) => [...previous, { id: localId, role: "customer", content }]);
      setSending(true);

      try {
        const turn = await api.sandbox.send(conversationId, content);
        setTurns((previous) => [
          ...previous.map((entry) =>
            entry.id === localId ? { ...entry, id: turn.message_id } : entry,
          ),
          {
            id: turn.reply.message_id,
            role: "agent",
            content: turn.reply.content,
            trace: turn.trace,
            degraded: turn.trace.degraded,
          },
        ]);
      } catch (caught) {
        // The question stays on screen; only the answer is missing. Removing
        // it would make the failure look like the message never sent, and
        // they would type the whole thing again.
        setError(humanMessage(caught));
      } finally {
        setSending(false);
        input.current?.focus();
      }
    },
    [conversationId, sending],
  );

  if (notReady) {
    return (
      <div className="space-y-5">
        <Header />
        <Alert tone="info" title="Todavía no puedes probar tu agente">
          Te falta completar la configuración. Cuando termines los pasos 1 a 5 el modo prueba se
          habilita solo, sin necesidad de conectar WhatsApp.
          <div className="mt-3">
            <Link
              href="/onboarding"
              className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
            >
              Ir a la configuración
            </Link>
          </div>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Header onReset={() => void start()} resetting={starting} />

      <div className="flex min-h-[60vh] flex-col overflow-hidden rounded-xl border border-line bg-surface">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {starting ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-2/3" />
              <Skeleton className="ml-auto h-16 w-2/3" />
            </div>
          ) : turns.length === 0 ? (
            <Empty onPick={(text) => void send(text)} />
          ) : (
            turns.map((turn) => <Bubble key={turn.id} turn={turn} />)
          )}

          {sending ? <Thinking /> : null}
          <div ref={endOfThread} />
        </div>

        {error ? (
          <div className="border-t border-line p-3">
            <Alert tone="danger">{error}</Alert>
          </div>
        ) : null}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void send(draft);
          }}
          className="flex items-end gap-2 border-t border-line p-3"
        >
          <label htmlFor="sandbox-input" className="sr-only">
            Escribe como si fueras un cliente
          </label>
          <textarea
            ref={input}
            id="sandbox-input"
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter breaks the line: what every chat
              // does, and what fingers already expect.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(draft);
              }
            }}
            placeholder="Escribe como si fueras un cliente..."
            disabled={starting || !conversationId}
            className="max-h-32 min-h-11 flex-1 resize-none rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-base text-ink placeholder:text-ink-faint disabled:opacity-50"
          />
          <Button type="submit" loading={sending} disabled={!draft.trim() || !conversationId}>
            Enviar
          </Button>
        </form>
      </div>
    </div>
  );
}

function Header({ onReset, resetting }: { onReset?: () => void; resetting?: boolean }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="font-heading text-2xl font-semibold text-ink">Modo prueba</h1>
          <span className="rounded-full bg-warning-soft px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-warning">
            Modo prueba
          </span>
        </div>
        <p className="mt-1 text-sm text-ink-soft">
          Es tu agente real, con tu catálogo y tu configuración. No gasta mensajes de tu prueba y
          no le llega a ningún cliente.
        </p>
      </div>

      {onReset ? (
        <Button variant="secondary" onClick={onReset} loading={resetting}>
          Reiniciar conversación
        </Button>
      ) : null}
    </header>
  );
}

function Empty({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="py-8 text-center">
      <ChatIcon className="mx-auto size-10 text-line-strong" />
      <p className="mt-3 text-sm font-medium text-ink">Escríbele como lo haría un cliente</p>
      <p className="mt-1 text-xs text-ink-faint">
        Prueba lo que más te preguntan. Si responde mal, ajusta la configuración y vuelve.
      </p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {SUGGESTIONS.map((text) => (
          <button
            key={text}
            type="button"
            onClick={() => onPick(text)}
            className="min-h-11 cursor-pointer rounded-full border border-line-strong px-3.5 text-sm text-ink-soft hover:bg-surface-muted hover:text-ink"
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

function Thinking() {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-faint" aria-live="polite">
      <SpinnerIcon className="size-4" />
      Tu agente está pensando...
    </div>
  );
}

function Bubble({ turn }: { turn: Turn }) {
  const mine = turn.role === "customer";
  return (
    <div className={cx("flex", mine ? "justify-end" : "justify-start")}>
      <div className="max-w-[85%] space-y-2">
        <div
          className={cx(
            "whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-sm",
            mine
              ? "rounded-br-sm bg-accent text-accent-fg"
              : "rounded-bl-sm bg-surface-muted text-ink",
          )}
        >
          {turn.content}
        </div>

        {turn.degraded ? (
          <p className="flex items-start gap-1.5 text-xs text-warning">
            <AlertIcon className="mt-0.5 size-3.5 shrink-0" />
            Esta no es la respuesta de tu agente: el modelo falló y contestamos con un mensaje de
            respaldo.
          </p>
        ) : null}

        {turn.trace ? <Trace trace={turn.trace} /> : null}
      </div>
    </div>
  );
}

/**
 * The expandable trace panel (HU-03).
 *
 * A `<details>`, collapsed by default: it works with the keyboard and with a
 * screen reader for free, and the transcript stays readable for somebody
 * testing conversation flow rather than debugging retrieval.
 */
function Trace({ trace }: { trace: SandboxTrace }) {
  const tools = trace.tools_called ?? [];
  const sources = trace.rag_sources ?? [];

  return (
    <details className="rounded-lg border border-line bg-surface text-xs" data-testid="trace">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-ink-soft hover:text-ink">
        <SparkIcon className="size-3.5 shrink-0" />
        <span className="font-medium">Ver cómo respondió</span>
        <span className="tabular ml-auto text-ink-faint">
          {tools.length > 0 ? `${tools.length} herramienta${tools.length === 1 ? "" : "s"} · ` : ""}
          {sources.length > 0 ? `${sources.length} fuente${sources.length === 1 ? "" : "s"} · ` : ""}
          {trace.latency_ms} ms
        </span>
      </summary>

      <div className="space-y-3 border-t border-line px-3 py-2.5">
        <Row label="Modelo" value={trace.model} />
        <Row
          label="Tokens"
          value={`${trace.input_tokens} de entrada · ${trace.output_tokens} de salida`}
        />

        <div>
          <p className="font-medium text-ink">Herramientas que usó</p>
          {tools.length === 0 ? (
            // Said explicitly. An empty section reads as a bug, and for a
            // greeting, using no tool is the correct behaviour.
            <p className="mt-0.5 text-ink-faint">Ninguna. Respondió con lo que ya sabía.</p>
          ) : (
            <ul className="mt-1 space-y-1">
              {tools.map((tool, index) => (
                <li key={index} className="flex items-start gap-2">
                  <span className="font-mono text-ink">{String(tool.tool ?? "?")}</span>
                  <span className={tool.ok === false ? "text-danger" : "text-success"}>
                    {tool.ok === false ? "falló" : "ok"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <p className="flex items-center gap-1.5 font-medium text-ink">
            <BookIcon className="size-3.5 shrink-0" />
            Fuentes que consultó
          </p>
          {sources.length === 0 ? (
            <p className="mt-0.5 text-ink-faint">
              Ninguna. Si esperabas que usara tu conocimiento cargado, revisa que esté indexado.
            </p>
          ) : (
            <ul className="mt-1 space-y-1">
              {sources.map((source, index) => (
                <li key={index} className="text-ink-soft">
                  {String(source.title ?? source.source_id ?? "fuente")}
                  {typeof source.score === "number" ? (
                    <span className="tabular ml-2 text-ink-faint">{source.score.toFixed(2)}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </details>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <p className="flex flex-wrap gap-2">
      <span className="font-medium text-ink">{label}:</span>
      <span className="font-mono text-ink-soft">{value}</span>
    </p>
  );
}
