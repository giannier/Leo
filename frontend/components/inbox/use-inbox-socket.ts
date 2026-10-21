"use client";

/**
 * The inbox's live channel (Feature 007, TASK-702 and TASK-707).
 *
 * Connects to `WS /api/v1/ws?token=`, hands every event to `onEvent`, and says
 * every 15 seconds which conversation this panel is looking at (presence).
 * Reconnects with a growing pause; on every (re)connection `onResync` runs, because
 * events sent while the socket was down are not replayed: the panel reads the state
 * again instead (the plan's mitigation).
 */

import { useEffect, useRef, useState } from "react";

import { inboxSocketTarget } from "@/lib/api/client";

export type InboxEvent =
  | { type: "message.new"; conversation_id: string }
  | { type: "conversation.updated"; conversation_id: string; por?: string; anterior?: string }
  | { type: "takeover.expiring"; conversation_id: string }
  | { type: "presence"; conversation_id: string; user_id: string; nombre: string };

const HEARTBEAT_MS = 15_000;
const MAX_BACKOFF_MS = 30_000;

export function useInboxSocket({
  viewing,
  onEvent,
  onResync,
}: {
  viewing: string | null;
  onEvent: (event: InboxEvent) => void;
  onResync: () => void;
}): { connected: boolean } {
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const handlers = useRef({ onEvent, onResync });
  const viewingRef = useRef(viewing);
  handlers.current = { onEvent, onResync };
  viewingRef.current = viewing;

  useEffect(() => {
    let stopped = false;
    let attempt = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      const target = inboxSocketTarget();
      if (!target || stopped) {
        retry = setTimeout(connect, 2_000);
        return;
      }
      const socket = new WebSocket(target.url, target.protocols);
      socketRef.current = socket;
      socket.onopen = () => {
        attempt = 0;
        setConnected(true);
        handlers.current.onResync();
        announce();
      };
      socket.onmessage = (message) => {
        try {
          handlers.current.onEvent(JSON.parse(String(message.data)) as InboxEvent);
        } catch {
          // Not ours to fail on: a malformed event is dropped.
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (stopped) return;
        attempt += 1;
        retry = setTimeout(connect, Math.min(MAX_BACKOFF_MS, 1_000 * 2 ** attempt));
      };
    }

    function announce() {
      const socket = socketRef.current;
      const conversation = viewingRef.current;
      if (socket?.readyState === WebSocket.OPEN && conversation) {
        socket.send(JSON.stringify({ tipo: "viendo", conversation_id: conversation }));
      }
    }

    connect();
    const heartbeat = setInterval(announce, HEARTBEAT_MS);
    return () => {
      stopped = true;
      clearInterval(heartbeat);
      if (retry) clearTimeout(retry);
      socketRef.current?.close();
    };
  }, []);

  // Say at once that this panel moved to another conversation.
  useEffect(() => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN && viewing) {
      socket.send(JSON.stringify({ tipo: "viendo", conversation_id: viewing }));
    }
  }, [viewing]);

  return { connected };
}
