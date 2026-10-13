"use client";

/**
 * Modo prueba (HU-03) and the simulator of the sales engine (Feature 004, C-19).
 *
 * The same agent, the same prompt, the same catalogue and the same RAG a real
 * customer would get - only the channel differs. That is the whole value of
 * it: a simulator that answered differently would tell the owner nothing
 * about what their customers will see.
 *
 * Three things the spec insists on, and this page is built around all of them:
 *
 * - **It never consumes trial quota** (Clarification 002), and it says so
 *   where it cannot be missed. An owner who suspects each test costs them a
 *   message stops testing, and testing is what makes their agent good.
 * - **It draws what a customer would see.** The agent sends more than text:
 *   a file, reply buttons, a list. Each is drawn as such, and tapping a button
 *   sends the id the agent gave it - not the words on it - exactly as WhatsApp
 *   does. Nothing here reaches a real phone.
 * - **Every reply exposes how it was produced.** The trace says which tools ran
 *   and which sources were used; the debug panel says where the conversation
 *   is: the stage before and after, the funnel, the facts and the tools the
 *   agent had. That is the difference between "the agent said something wrong"
 *   and "the agent said something wrong *because* my delivery policy was never
 *   indexed", and only the second one is fixable.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AlertIcon, BookIcon, ChatIcon, SparkIcon, SpinnerIcon } from "@/components/icons";
import { Alert, Button, Skeleton, cx } from "@/components/ui";
import { WhatsAppText } from "@/components/whatsapp-text";
import {
  api,
  type Saliente,
  type SandboxDebug,
  type SandboxInput,
  type SandboxTrace,
} from "@/lib/api/client";
import { ApiError, humanMessage } from "@/lib/api/errors";

/** How often the open simulator asks for what the agent said on its own. */
const POLL_MS = 3000;

type Entry =
  | { id: string; role: "customer"; content: string; imageUrl?: string }
  | {
      id: string;
      role: "agent";
      saliente: Saliente;
      trace?: SandboxTrace;
      degraded?: boolean;
    };

const SUGGESTIONS = [
  "Hola, ¿qué venden?",
  "¿Cuánto cuesta?",
  "¿Hacen delivery?",
  "¿Cómo puedo pagar?",
];

export default function SandboxPage() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notReady, setNotReady] = useState(false);
  const [starting, setStarting] = useState(true);
  const [debugOn, setDebugOn] = useState(false);
  const [debug, setDebug] = useState<SandboxDebug | null>(null);

  const endOfThread = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  /** The newest message of the agent on screen: the anchor of the poll. */
  const lastAgentId = useRef<string | null>(null);

  const start = useCallback(async () => {
    setStarting(true);
    setError(null);
    setNotReady(false);
    setEntries([]);
    setDebug(null);
    lastAgentId.current = null;
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
  }, [entries, sending]);

  // What the agent says without being asked - a payment confirmed or rejected, a
  // handover, a follow-up - is written as an outbound message the next time the
  // simulator looks. It looks only when nothing else is in flight.
  useEffect(() => {
    if (!conversationId || sending || starting) return;
    const controller = new AbortController();
    const timer = window.setInterval(async () => {
      const anchor = lastAgentId.current;
      if (!anchor) return;
      try {
        const page = await api.sandbox.after(conversationId, anchor, controller.signal);
        const fresh = page.items.filter((item) => item.saliente);
        if (fresh.length === 0) return;
        setEntries((previous) => {
          const known = new Set(previous.map((entry) => entry.id));
          const added: Entry[] = fresh
            .filter((item) => !known.has(item.id))
            .map((item) => ({ id: item.id, role: "agent", saliente: item.saliente! }));
          if (added.length === 0) return previous;
          lastAgentId.current = added[added.length - 1].id;
          return [...previous, ...added];
        });
      } catch {
        // A missed poll is not an error worth showing: the next one will do.
      }
    }, POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [conversationId, sending, starting]);

  /** Show the agent's answer: one entry per outbound, the trace on the last. */
  const showAnswer = useCallback(
    (localId: string, turn: Awaited<ReturnType<typeof api.sandbox.send>>) => {
      const last = turn.salientes.length - 1;
      setEntries((previous) => [
        ...previous.map((entry) =>
          entry.id === localId ? { ...entry, id: turn.message_id } : entry,
        ),
        ...turn.salientes.map(
          (saliente, index): Entry => ({
            id: saliente.message_id,
            role: "agent",
            saliente,
            trace: index === last ? (turn.trace ?? undefined) : undefined,
            degraded: index === last ? turn.trace?.degraded : undefined,
          }),
        ),
      ]);
      if (last >= 0) lastAgentId.current = turn.salientes[last].message_id;
      if (turn.debug) setDebug(turn.debug);
    },
    [],
  );

  const deliver = useCallback(
    async (
      shown: Pick<Extract<Entry, { role: "customer" }>, "content" | "imageUrl">,
      call: (conversation: string) => ReturnType<typeof api.sandbox.send>,
    ) => {
      if (!conversationId || sending) return;
      setError(null);
      // What the customer did appears immediately. Waiting for the round trip
      // to show what you just typed makes the whole thing feel broken on a slow
      // connection - which is the one these customers are on.
      const localId = `local-${Date.now()}`;
      setEntries((previous) => [...previous, { id: localId, role: "customer", ...shown }]);
      setSending(true);
      try {
        showAnswer(localId, await call(conversationId));
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
    [conversationId, sending, showAnswer],
  );

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content) return;
      setDraft("");
      await deliver({ content }, (id) => api.sandbox.send(id, { content }, debugOn));
    },
    [deliver, debugOn],
  );

  /** A tap sends the id the agent gave the option; the customer's bubble shows its title. */
  const tap = useCallback(
    async (option: { id: string; title: string }) => {
      const body: SandboxInput = { interactive_id: option.id, title: option.title };
      await deliver({ content: option.title }, (id) => api.sandbox.send(id, body, debugOn));
    },
    [deliver, debugOn],
  );

  const sendImage = useCallback(
    async (file: File) => {
      await deliver({ content: "Imagen enviada", imageUrl: URL.createObjectURL(file) }, (id) =>
        api.sandbox.sendImage(id, file, debugOn),
      );
    },
    [deliver, debugOn],
  );

  /** Forget everything - state, messages, sandbox orders - and greet again. */
  const reset = useCallback(async () => {
    if (!conversationId) {
      await start();
      return;
    }
    setStarting(true);
    setError(null);
    try {
      await api.sandbox.reset(conversationId);
      setEntries([]);
      setDebug(null);
      lastAgentId.current = null;
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setStarting(false);
    }
  }, [conversationId, start]);

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
      <Header
        onReset={() => void reset()}
        resetting={starting}
        debugOn={debugOn}
        onDebug={setDebugOn}
      />

      <div className={cx("grid gap-4", debugOn && "lg:grid-cols-[minmax(0,1fr)_22rem]")}>
        <div className="flex min-h-[60vh] flex-col overflow-hidden rounded-xl border border-line bg-surface">
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {starting ? (
              <div className="space-y-3">
                <Skeleton className="h-16 w-2/3" />
                <Skeleton className="ml-auto h-16 w-2/3" />
              </div>
            ) : entries.length === 0 ? (
              <Empty onPick={(text) => void send(text)} />
            ) : (
              entries.map((entry) => (
                <Message key={entry.id} entry={entry} busy={sending} onTap={(o) => void tap(o)} />
              ))
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
            <input
              ref={fileInput}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="sr-only"
              aria-label="Elegir una imagen para enviar"
              data-testid="image-input"
              tabIndex={-1}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void sendImage(file);
              }}
            />
            <Button
              type="button"
              variant="secondary"
              disabled={starting || !conversationId || sending}
              onClick={() => fileInput.current?.click()}
            >
              Enviar imagen
            </Button>
            <Button type="submit" loading={sending} disabled={!draft.trim() || !conversationId}>
              Enviar
            </Button>
          </form>
        </div>

        {debugOn ? <DebugPanel debug={debug} /> : null}
      </div>
    </div>
  );
}

function Header({
  onReset,
  resetting,
  debugOn,
  onDebug,
}: {
  onReset?: () => void;
  resetting?: boolean;
  debugOn?: boolean;
  onDebug?: (on: boolean) => void;
}) {
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

      <div className="flex flex-wrap items-center gap-3">
        {onDebug ? (
          <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={Boolean(debugOn)}
              onChange={(event) => onDebug(event.target.checked)}
              className="size-4 cursor-pointer"
            />
            Modo depuración
          </label>
        ) : null}
        {onReset ? (
          <Button variant="secondary" onClick={onReset} loading={resetting}>
            Reiniciar conversación
          </Button>
        ) : null}
      </div>
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

type OptionRow = { id: string; title: string; description?: string | null };

const BUBBLE =
  "whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-sm rounded-bl-sm bg-surface-muted text-ink";

function Message({
  entry,
  busy,
  onTap,
}: {
  entry: Entry;
  busy: boolean;
  onTap: (option: { id: string; title: string }) => void;
}) {
  if (entry.role === "customer") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] space-y-2">
          {entry.imageUrl ? (
            // eslint-disable-next-line @next/next/no-img-element -- a local blob URL
            <img
              src={entry.imageUrl}
              alt="Imagen que enviaste"
              className="max-h-56 rounded-2xl rounded-br-sm"
            />
          ) : null}
          <div className="whitespace-pre-wrap rounded-2xl rounded-br-sm bg-accent px-3.5 py-2.5 text-sm text-accent-fg">
            {entry.content}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-2">
        <Outbound saliente={entry.saliente} busy={busy} onTap={onTap} />

        {entry.degraded ? (
          <p className="flex items-start gap-1.5 text-xs text-warning">
            <AlertIcon className="mt-0.5 size-3.5 shrink-0" />
            Esta no es la respuesta de tu agente: el modelo falló y contestamos con un mensaje de
            respaldo.
          </p>
        ) : null}

        {entry.trace ? <Trace trace={entry.trace} /> : null}
      </div>
    </div>
  );
}

/** One outbound message, drawn as the customer's phone would draw it. */
function Outbound({
  saliente,
  busy,
  onTap,
}: {
  saliente: Saliente;
  busy: boolean;
  onTap: (option: { id: string; title: string }) => void;
}) {
  switch (saliente.tipo) {
    case "texto":
      return (
        <div className={BUBBLE}>
          <WhatsAppText text={saliente.texto} />
        </div>
      );

    case "imagen":
      return (
        <figure className="space-y-1" data-testid="saliente-imagen">
          {saliente.url ? (
            // eslint-disable-next-line @next/next/no-img-element -- a short-lived signed link
            <img
              src={saliente.url}
              alt={saliente.titulo ?? saliente.clave ?? "Imagen del negocio"}
              className="max-h-64 rounded-2xl rounded-bl-sm"
            />
          ) : (
            <div className="rounded-2xl rounded-bl-sm bg-surface-muted px-3.5 py-2.5 text-xs text-ink-faint">
              Imagen: {saliente.titulo ?? saliente.clave} (el archivo ya no está disponible)
            </div>
          )}
          {saliente.caption ? (
            <figcaption className={BUBBLE}>
              <WhatsAppText text={saliente.caption} />
            </figcaption>
          ) : null}
        </figure>
      );

    case "documento":
    case "video":
    case "audio":
      return (
        <div className={BUBBLE} data-testid="saliente-archivo">
          {saliente.url ? (
            <a href={saliente.url} target="_blank" rel="noreferrer" className="font-medium underline">
              {saliente.titulo ?? saliente.clave}
            </a>
          ) : (
            <span>{saliente.titulo ?? saliente.clave}</span>
          )}
          <span className="ml-2 text-xs text-ink-faint">({saliente.tipo})</span>
          {saliente.caption ? <p className="mt-1">{saliente.caption}</p> : null}
        </div>
      );

    case "botones":
      return (
        <div className="space-y-1.5" data-testid="saliente-botones">
          <div className={BUBBLE}>
            <WhatsAppText text={saliente.body} />
          </div>
          <div className="flex flex-col gap-1.5">
            {(saliente.botones ?? []).map((button: { id: string; title: string }) => (
              <button
                key={button.id}
                type="button"
                disabled={busy}
                data-testid="opcion"
                onClick={() => onTap(button)}
                className="min-h-11 cursor-pointer rounded-xl border border-line-strong bg-surface px-3.5 text-sm font-medium text-accent hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                {button.title}
              </button>
            ))}
          </div>
        </div>
      );

    case "lista":
      return <ListMessage saliente={saliente} busy={busy} onTap={onTap} />;
  }
}

/** A list: the body, and a button that opens the rows (up to ten). */
function ListMessage({
  saliente,
  busy,
  onTap,
}: {
  saliente: Saliente;
  busy: boolean;
  onTap: (option: { id: string; title: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-1.5" data-testid="saliente-lista">
      <div className={BUBBLE}>
        <WhatsAppText text={saliente.body} />
      </div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="min-h-11 w-full cursor-pointer rounded-xl border border-line-strong bg-surface px-3.5 text-sm font-medium text-accent hover:bg-surface-muted"
      >
        {saliente.boton ?? "Ver opciones"}
      </button>
      {open ? (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
          {(saliente.filas ?? []).map((row: OptionRow) => (
            <li key={row.id}>
              <button
                type="button"
                disabled={busy}
                data-testid="opcion"
                onClick={() => {
                  setOpen(false);
                  onTap(row);
                }}
                className="block min-h-11 w-full cursor-pointer px-3.5 py-2 text-left text-sm hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="block font-medium text-ink">{row.title}</span>
                {row.description ? (
                  <span className="block text-xs text-ink-faint">{row.description}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * The debug panel (C-19): where the conversation is and what the agent had.
 *
 * It shows the last turn only. The stage before and after is the headline,
 * because "why did it ask that now?" is almost always answered by the stage; the
 * facts and the tools are underneath for when it is not.
 */
function DebugPanel({ debug }: { debug: SandboxDebug | null }) {
  return (
    <aside
      aria-label="Depuración"
      data-testid="debug-panel"
      className="space-y-3 rounded-xl border border-line bg-surface p-4 text-xs"
    >
      <h2 className="font-heading text-sm font-semibold text-ink">Depuración</h2>
      {debug === null ? (
        <p className="text-ink-faint">Envía un mensaje para ver en qué etapa está la conversación.</p>
      ) : (
        <>
          <div>
            <p className="font-medium text-ink">Etapa</p>
            <p className="mt-0.5 font-mono text-sm text-ink" data-testid="debug-stage">
              {debug.etapa_antes} → {debug.etapa_despues}
            </p>
            <p className="mt-0.5 text-ink-faint">
              Embudo: <span data-testid="debug-funnel">{debug.embudo}</span>
            </p>
            {debug.silencio ? (
              <p className="mt-1 text-warning">El asistente no respondió: {debug.silencio}.</p>
            ) : null}
          </div>

          <div>
            <p className="font-medium text-ink">Herramientas habilitadas</p>
            {debug.herramientas_habilitadas.length === 0 ? (
              <p className="mt-0.5 text-ink-faint">Ninguna en esta etapa.</p>
            ) : (
              <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="debug-tools-enabled">
                {debug.herramientas_habilitadas.map((name) => (
                  <li key={name} className="rounded bg-surface-muted px-1.5 py-0.5 font-mono">
                    {name}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <p className="font-medium text-ink">Herramientas que llamó</p>
            {debug.herramientas_llamadas.length === 0 ? (
              <p className="mt-0.5 text-ink-faint">Ninguna.</p>
            ) : (
              <ul className="mt-1 space-y-0.5" data-testid="debug-tools-called">
                {debug.herramientas_llamadas.map((call, index) => (
                  <li key={index} className="flex gap-2">
                    <span className="font-mono text-ink">{call.nombre}</span>
                    <span className={call.status === "hecho" ? "text-success" : "text-warning"}>
                      {call.status}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <p className="font-medium text-ink">Hechos</p>
            <pre
              className="mt-1 max-h-64 overflow-auto rounded bg-surface-muted p-2 font-mono text-[11px] leading-snug text-ink-soft"
              data-testid="debug-facts"
            >
              {JSON.stringify(debug.hechos, null, 2)}
            </pre>
          </div>

          <p className="text-ink-faint">
            {debug.tokens.prompt} de entrada · {debug.tokens.completion} de salida
            {debug.modelo ? ` · ${debug.modelo}` : ""}
          </p>
        </>
      )}
    </aside>
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
