"use client";

/**
 * One conversation in the inbox (Feature 007, TASK-707): the header with who has it
 * and what can be done, the thread, the composer and the internal notes.
 *
 * The rules the panel shows are the server's: whether the 24-hour window is open,
 * who holds the conversation, what a role may do. A button the server would refuse
 * is not offered, and when it refuses anyway (somebody else was faster) the reason
 * is shown and the conversation reloaded.
 */

import Link from "next/link";
import { useState } from "react";

import { Alert, Button, Skeleton, TextArea, cx } from "@/components/ui";
import { WhatsAppText } from "@/components/whatsapp-text";
import {
  api,
  type InboxDetail,
  type InboxNote,
  type ThreadMessage,
} from "@/lib/api/client";
import { ApiError, humanMessage } from "@/lib/api/errors";

const STAGES: Record<string, string> = {
  INICIO: "Saludo",
  CALIFICAR: "Calificando",
  ANUNCIO: "Llegó por anuncio",
  IDENTIFICAR: "Viendo opciones",
  PEDIDO: "Armando pedido",
  DATOS: "Datos del cliente",
  COBRO: "Cobro",
  VERIFICACION: "Pago por revisar",
  CERRADO: "Venta cerrada",
  ASESORIA: "Asesoría",
  DERIVADO: "Espera a una persona",
};

function time(value: string): string {
  return new Date(value).toLocaleString("es-PE", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function Bubble({ message, conversationId }: { message: ThreadMessage; conversationId: string }) {
  const [image, setImage] = useState<string | null>(null);
  if (message.tipo === "event") {
    return (
      <li className="flex justify-center" data-testid="thread-event">
        <span className="rounded-full bg-surface-muted px-3 py-1 text-xs text-ink-faint">
          {message.texto} · {time(message.en)}
        </span>
      </li>
    );
  }
  const customer = message.autor === "customer";
  const person = message.autor === "human_operator";
  return (
    <li className={cx("flex", customer ? "justify-start" : "justify-end")} data-testid="thread-message">
      <div
        className={cx(
          "max-w-[85%] rounded-2xl px-3.5 py-2 text-sm",
          customer && "bg-surface border border-line text-ink",
          person && "bg-brand text-brand-fg",
          !customer && !person && "bg-accent/10 text-ink",
          message.descartado && "opacity-60",
        )}
      >
        <p className="mb-0.5 text-[11px] font-semibold opacity-75">
          {customer ? "Cliente" : person ? (message.operador ?? "Persona del negocio") : "Asistente"}
        </p>
        <div className="whitespace-pre-wrap break-words">
          <WhatsAppText text={message.texto} />
        </div>
        {message.opciones?.length ? (
          <p className="mt-1 text-xs opacity-75">Opciones: {message.opciones.join(" · ")}</p>
        ) : null}
        {message.imagen ? (
          image ? (
            // eslint-disable-next-line @next/next/no-img-element -- a signed, short-lived private link
            <img src={image} alt="Imagen que envió el cliente" className="mt-2 max-h-64 rounded-lg" />
          ) : (
            <button
              type="button"
              className="mt-1 text-xs font-semibold underline"
              onClick={async () => setImage((await api.conversations.image(conversationId, message.id)).url)}
            >
              Ver imagen
            </button>
          )
        ) : null}
        <p className="mt-1 text-right text-[11px] opacity-60">
          {time(message.en)}
          {message.descartado ? " · no enviada: la conversación ya la tenía una persona" : ""}
          {message.estado_entrega === "failed" && !message.descartado ? " · no se pudo enviar" : ""}
        </p>
      </div>
    </li>
  );
}

export function Thread({
  detail,
  messages,
  earlier,
  notes,
  me,
  canSteal,
  viewers,
  expiring,
  onLoadEarlier,
  onChanged,
}: {
  detail: InboxDetail | null;
  messages: ThreadMessage[] | null;
  earlier: boolean;
  notes: InboxNote[];
  me: string | null;
  canSteal: boolean;
  viewers: string[];
  expiring: boolean;
  onLoadEarlier: () => void;
  onChanged: () => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!detail || !messages) return <Skeleton className="h-96 w-full" />;

  const id = detail.id;
  const mine = detail.tomada_por?.id === me;
  const someoneElse = detail.tomada_por !== null && !mine;
  const closed = detail.estado === "closed";
  const canWrite = !closed && detail.ventana.abierta && !someoneElse;

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await run();
    } catch (caught) {
      setError(humanMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) await onChanged();
    } finally {
      setBusy(false);
    }
    await onChanged();
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-3" aria-label="Conversación">
      <header className="space-y-2 rounded-xl border border-line bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate font-heading text-lg font-semibold text-ink" data-testid="thread-contact">
              {detail.contacto.nombre ?? detail.contacto.telefono ?? "Cliente"}
            </h2>
            <p className="text-xs text-ink-faint">
              {detail.contacto.telefono ?? "Simulador"}
              {detail.etapa ? ` · ${STAGES[detail.etapa] ?? detail.etapa}` : ""}
              {detail.contacto.posible_duplicado ? " · posible cliente duplicado" : ""}
              {detail.contacto.opt_out ? " · no quiere recibir mensajes" : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {!closed && detail.tomada_por === null ? (
              <Button type="button" loading={busy} onClick={() => act(() => api.conversations.take(id))}>
                Tomar conversación
              </Button>
            ) : null}
            {mine ? (
              <Button type="button" variant="secondary" loading={busy} onClick={() => act(() => api.conversations.release(id))}>
                Devolver al asistente
              </Button>
            ) : null}
            {someoneElse && canSteal ? (
              <Button type="button" variant="secondary" loading={busy} onClick={() => act(() => api.conversations.steal(id))}>
                Tomarla yo
              </Button>
            ) : null}
            {closed ? (
              <Button type="button" variant="ghost" loading={busy} onClick={() => act(() => api.conversations.reopen(id))}>
                Reabrir
              </Button>
            ) : (
              <Button type="button" variant="ghost" loading={busy} onClick={() => act(() => api.conversations.close(id))}>
                Cerrar
              </Button>
            )}
            {!detail.contacto.opt_out && detail.canal === "whatsapp" ? (
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  if (window.confirm("El cliente no recibirá más mensajes del asistente. ¿Seguir?")) {
                    void act(() => api.conversations.optOut(id));
                  }
                }}
              >
                No escribirle más
              </Button>
            ) : null}
          </div>
        </div>
        <p className="text-sm" data-testid="thread-holder">
          {detail.tomada_por
            ? mine
              ? "La tienes tú: el asistente no responde mientras la tengas."
              : `La tiene ${detail.tomada_por.nombre ?? "otra persona"}.`
            : detail.derivado
              ? `Espera a una persona: ${detail.derivado.motivo}${detail.derivado.resumen ? ` — ${detail.derivado.resumen}` : ""}`
              : "La atiende el asistente."}
          {viewers.length ? ` · También la está viendo: ${viewers.join(", ")}` : ""}
        </p>
        {detail.pedido_id ? (
          <Link href={`/pedidos/${detail.pedido_id}`} className="text-sm font-semibold text-accent underline">
            Ver el pedido
          </Link>
        ) : null}
        {mine && expiring ? (
          <Alert tone="warning">En 5 minutos vuelve al asistente si no escribes nada.</Alert>
        ) : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto rounded-xl bg-canvas p-3">
        {earlier ? (
          <div className="mb-3 flex justify-center">
            <Button type="button" variant="ghost" onClick={onLoadEarlier}>
              Ver anteriores
            </Button>
          </div>
        ) : null}
        <ul className="space-y-2" data-testid="thread">
          {messages.map((message) => (
            <Bubble key={message.id} message={message} conversationId={id} />
          ))}
        </ul>
      </div>

      <form
        className="space-y-2 rounded-xl border border-line bg-surface p-3"
        onSubmit={(event) => {
          event.preventDefault();
          const body = text.trim();
          if (!body) return;
          void act(async () => {
            await api.conversations.send(id, body);
            setText("");
          });
        }}
      >
        {!detail.ventana.abierta ? (
          <p className="text-sm text-ink-soft" data-testid="window-closed">
            El cliente no escribe hace más de 24 horas: podrás responder cuando vuelva a escribir.
          </p>
        ) : detail.ventana.por_cerrar ? (
          <p className="text-xs text-warning">La ventana de 24 horas está por cerrarse.</p>
        ) : null}
        <TextArea
          label={detail.tomada_por === null && !closed ? "Mensaje (al enviar, tomas la conversación)" : "Mensaje"}
          rows={2}
          value={text}
          disabled={!canWrite}
          onChange={(event) => setText(event.target.value)}
        />
        <div className="flex justify-end">
          <Button type="submit" loading={busy} disabled={!canWrite || !text.trim()}>
            Enviar
          </Button>
        </div>
      </form>

      <details className="rounded-xl border border-line bg-surface p-3">
        <summary className="cursor-pointer text-sm font-semibold text-ink">
          Notas internas ({notes.length})
        </summary>
        <ul className="mt-2 space-y-1 text-sm">
          {notes.map((n) => (
            <li key={n.id}>
              <span className="font-medium text-ink">{n.autor ?? "Alguien"}:</span>{" "}
              <span className="text-ink-soft">{n.texto}</span>
            </li>
          ))}
        </ul>
        <form
          className="mt-2 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const body = note.trim();
            if (!body) return;
            void act(async () => {
              await api.conversations.addNote(id, body);
              setNote("");
            });
          }}
        >
          <input
            aria-label="Nueva nota"
            className="min-h-11 flex-1 rounded-lg border border-line-strong bg-surface px-3 text-sm"
            placeholder="Solo la ve tu equipo"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <Button type="submit" variant="secondary">
            Guardar nota
          </Button>
        </form>
      </details>
    </section>
  );
}
