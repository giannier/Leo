"use client";

/**
 * Bandeja (Feature 007, TASK-707).
 *
 * Every conversation of the business, live: the list on the left, the selected
 * conversation on the right (one at a time on a phone). Container only: it owns the
 * state, the requests and the live channel, and hands plain props to
 * `components/inbox/*`.
 *
 * Live updates arrive by WebSocket; while it is down, a 30-second poll keeps the
 * panel close to the truth, and every reconnection re-reads the state (events sent
 * while disconnected are not replayed). The browser notification is a courtesy for
 * an open tab: a conversation that starts waiting for a person while the tab is in
 * the background.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ConversationList } from "@/components/inbox/conversation-list";
import { InboxSettingsCard } from "@/components/inbox/inbox-settings";
import { Thread } from "@/components/inbox/thread";
import { useInboxSocket, type InboxEvent } from "@/components/inbox/use-inbox-socket";
import { Alert, Button, cx } from "@/components/ui";
import {
  api,
  type InboxDetail,
  type InboxFilter,
  type InboxItem,
  type InboxNote,
  type ThreadMessage,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole, useSession } from "@/lib/session";

const POLL_MS = 30_000;
const SEARCH_DEBOUNCE_MS = 300;
/** A presence older than this means the other person left. */
const PRESENCE_TTL_MS = 40_000;

export default function BandejaPage() {
  const { user } = useSession();
  const canSteal = useHasRole(...MANAGES_CONFIGURATION);
  const me = user?.id ?? null;

  const [filter, setFilter] = useState<InboxFilter>("todas");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [sandbox, setSandbox] = useState(false);
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<string | null>(null);
  // `?c=<id>` opens that conversation (the CRM's "Abrir conversación", Feature 012).
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("c");
    if (wanted) setSelected(wanted);
  }, []);
  const [detail, setDetail] = useState<InboxDetail | null>(null);
  const [messages, setMessages] = useState<ThreadMessage[] | null>(null);
  const [earlier, setEarlier] = useState<string | null>(null);
  const [notes, setNotes] = useState<InboxNote[]>([]);
  const [presence, setPresence] = useState<Record<string, { nombre: string; at: number; conversation: string }>>({});
  const [expiring, setExpiring] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);

  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const waitingRef = useRef<number | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const loadList = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const page = await api.conversations.list(
          { filtro: filter, q: query || undefined, pruebas: sandbox || undefined },
          signal,
        );
        setItems(page.items);
        setNext(page.siguiente);
        setWaiting(page.esperando);
        if (
          waitingRef.current !== null &&
          page.esperando > waitingRef.current &&
          typeof Notification !== "undefined" &&
          Notification.permission === "granted" &&
          document.hidden
        ) {
          new Notification("Leo: una conversación espera a una persona", {
            body: "Abre la bandeja para atenderla.",
          });
        }
        waitingRef.current = page.esperando;
        setError(null);
      } catch (caught) {
        if (!signal?.aborted) setError(humanMessage(caught));
      }
    },
    [filter, query, sandbox],
  );

  const loadConversation = useCallback(async (id: string) => {
    try {
      const [found, thread, written] = await Promise.all([
        api.conversations.get(id),
        api.conversations.thread(id),
        api.conversations.notes(id),
      ]);
      if (selectedRef.current !== id) return;
      setDetail(found);
      setMessages(thread.items);
      setEarlier(thread.anteriores);
      setNotes(written.items);
      // Opening it marked it read.
      setItems((current) =>
        current?.map((item) => (item.id === id ? { ...item, no_leidos: 0 } : item)) ?? current,
      );
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setItems(null);
    void loadList(controller.signal);
    return () => controller.abort();
  }, [loadList]);

  useEffect(() => {
    setDetail(null);
    setMessages(null);
    setNotes([]);
    setExpiring(null);
    if (selected) void loadConversation(selected);
  }, [selected, loadConversation]);

  const refresh = useCallback(async () => {
    await loadList();
    if (selectedRef.current) await loadConversation(selectedRef.current);
  }, [loadList, loadConversation]);

  const onEvent = useCallback(
    (event: InboxEvent) => {
      if (event.type === "presence") {
        if (event.user_id === me) return;
        setPresence((current) => ({
          ...current,
          [event.user_id]: { nombre: event.nombre, at: Date.now(), conversation: event.conversation_id },
        }));
        return;
      }
      if (event.type === "takeover.expiring") {
        setExpiring(event.conversation_id);
        return;
      }
      void loadList();
      if (event.conversation_id === selectedRef.current) void loadConversation(event.conversation_id);
    },
    [me, loadList, loadConversation],
  );

  const { connected } = useInboxSocket({ viewing: selected, onEvent, onResync: () => void refresh() });

  // The fallback while the live channel is down.
  useEffect(() => {
    if (connected) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [connected, refresh]);

  // The browser notification needs a permission the person grants once.
  useEffect(() => {
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      void Notification.requestPermission().catch(() => undefined);
    }
  }, []);

  async function loadMore() {
    if (!next) return;
    try {
      const page = await api.conversations.list({
        filtro: filter,
        q: query || undefined,
        pruebas: sandbox || undefined,
        cursor: next,
      });
      setItems((current) => [...(current ?? []), ...page.items]);
      setNext(page.siguiente);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  async function loadEarlier() {
    if (!selected || !earlier) return;
    try {
      const page = await api.conversations.thread(selected, earlier);
      setMessages((current) => [...page.items, ...(current ?? [])]);
      setEarlier(page.anteriores);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  const now = Date.now();
  const viewers = Object.values(presence)
    .filter((p) => p.conversation === selected && now - p.at < PRESENCE_TTL_MS)
    .map((p) => p.nombre);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-ink">Bandeja</h1>
          <p className="text-sm text-ink-soft">
            Las conversaciones de tu negocio. Toma una para responder tú; el asistente se calla mientras la tengas.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={cx("text-xs", connected ? "text-success" : "text-ink-faint")}
            data-testid="live-status"
          >
            {connected ? "En vivo" : "Actualizando cada 30 s"}
          </span>
          {canSteal ? (
            <Button type="button" variant="secondary" onClick={() => setShowSettings((on) => !on)}>
              Ajustes
            </Button>
          ) : null}
        </div>
      </header>
      {showSettings ? <InboxSettingsCard onClose={() => setShowSettings(false)} /> : null}
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <div className="grid gap-4 lg:h-[calc(100vh-12rem)] lg:grid-cols-[22rem_1fr]">
        <div className={cx("min-h-0", selected && "hidden lg:flex lg:flex-col")}>
          <ConversationList
            items={items}
            filter={filter}
            search={search}
            sandbox={sandbox}
            waiting={waiting}
            selected={selected}
            more={next !== null}
            onFilter={setFilter}
            onSearch={setSearch}
            onSandbox={setSandbox}
            onSelect={setSelected}
            onMore={() => void loadMore()}
          />
        </div>
        {selected ? (
          <div className="flex min-h-0 flex-col gap-2">
            <button
              type="button"
              className="self-start text-sm font-semibold text-accent lg:hidden"
              onClick={() => setSelected(null)}
            >
              ← Volver a la lista
            </button>
            <Thread
              key={selected}
              detail={detail}
              messages={messages}
              earlier={earlier !== null}
              notes={notes}
              me={me}
              canSteal={canSteal}
              viewers={viewers}
              expiring={expiring === selected}
              onLoadEarlier={() => void loadEarlier()}
              onChanged={refresh}
            />
          </div>
        ) : (
          <p className="hidden items-center justify-center rounded-xl border border-dashed border-line text-sm text-ink-soft lg:flex">
            Elige una conversación.
          </p>
        )}
      </div>
    </div>
  );
}
