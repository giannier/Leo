"use client";

/**
 * Comanda (feature 016): the orders the business is working on, by stage.
 *
 * Container: it reads the board every few seconds while the tab is in sight, moves an order
 * one stage on a tap, plays a short sound when something new reaches the kitchen, and lets the
 * owner choose which stages tell the customer. The cards are `components/orders/comanda.tsx`.
 *
 * The sound is opt-in and remembered: a browser only lets a page make noise after the person
 * touched it, and a counter tablet that beeps unasked is switched off by the first cook it annoys.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { VolumeIcon, VolumeOffIcon } from "@/components/icons";
import { OrdersNav } from "@/components/orders/orders-nav";
import { ComandaBoard } from "@/components/orders/comanda";
import { noticeMessage } from "@/components/orders/labels";
import type { NextAction } from "@/components/orders/stages";
import { Alert, Button, Card, Checkbox, Skeleton } from "@/components/ui";
import { useToast } from "@/components/toast";
import { api, type BoardOrder, type OrderSettings } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

const REFRESH_MS = 15_000;
const SOUND_KEY = "leo.comanda.sound";

/** A short two-note chime, made with the browser's own synthesiser: no file to ship. */
function chime(context: AudioContext) {
  const start = context.currentTime;
  [660, 880].forEach((frequency, index) => {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, start + index * 0.18);
    gain.gain.exponentialRampToValueAtTime(0.25, start + index * 0.18 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + index * 0.18 + 0.28);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(start + index * 0.18);
    oscillator.stop(start + index * 0.18 + 0.3);
  });
}

function readSoundPreference(): boolean {
  try {
    return window.localStorage.getItem(SOUND_KEY) === "on";
  } catch {
    return false;
  }
}

export default function ComandaPage() {
  const toast = useToast();
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);

  const [orders, setOrders] = useState<BoardOrder[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  // The clock the cards measure their waits against: ticks without asking the server.
  const [now, setNow] = useState(() => Date.now());
  const [busyId, setBusyId] = useState<string | null>(null);
  const [settings, setSettings] = useState<OrderSettings | null>(null);

  const [sound, setSound] = useState(false);
  const audio = useRef<AudioContext | null>(null);
  // The orders that were already waiting for a person the last time we looked.
  const seen = useRef<Set<string> | null>(null);

  useEffect(() => {
    setSound(readSoundPreference());
  }, []);

  // A preference remembered from another visit still needs one touch before the browser lets the
  // page make noise: the first tap anywhere unlocks it.
  useEffect(() => {
    if (!sound || audio.current !== null) return;
    const unlock = () => {
      audio.current ??= new (window.AudioContext ??
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
      void audio.current.resume();
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    return () => window.removeEventListener("pointerdown", unlock);
  }, [sound]);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const board = await api.orders.board(signal);
      setOrders(board.ordenes);
      setError(null);
      setOffline(false);
      setUpdatedAt(Date.now());
      setNow(Date.now());
    } catch (caught) {
      if (signal?.aborted) return;
      // Keep what is on screen if we already had a board: a lost signal is not an empty kitchen.
      setOffline(true);
      setError((current) => current ?? humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load(controller.signal);
    }, REFRESH_MS);
    const tick = setInterval(() => setNow(Date.now()), 30_000);
    return () => {
      controller.abort();
      clearInterval(timer);
      clearInterval(tick);
    };
  }, [load]);

  useEffect(() => {
    api.orders
      .settings()
      .then(setSettings)
      .catch(() => setSettings(null));
  }, []);

  // A chime when an order reaches the kitchen that was not there before.
  useEffect(() => {
    if (orders === null) return;
    const waiting = new Set(
      orders.filter((o) => o.estado === "por_confirmar" || o.estado === "confirmado").map((o) => o.id),
    );
    const before = seen.current;
    seen.current = waiting;
    if (before === null || !sound || audio.current === null) return;
    if ([...waiting].some((id) => !before.has(id))) chime(audio.current);
  }, [orders, sound]);

  function toggleSound() {
    const next = !sound;
    if (next) {
      // Created here, in the tap, because that is when the browser allows it.
      const Context =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (Context) {
        audio.current ??= new Context();
        void audio.current.resume();
        chime(audio.current);
      }
    }
    setSound(next);
    try {
      window.localStorage.setItem(SOUND_KEY, next ? "on" : "off");
    } catch {
      // The choice lasts until the page closes.
    }
  }

  async function advance(order: BoardOrder, action: NextAction) {
    setBusyId(order.id);
    try {
      const result = action.stage
        ? await api.orders.setStage(order.id, action.stage)
        : await api.orders.setStatus(order.id, "completado");
      const notice = result.aviso_cliente ? noticeMessage(result.aviso_cliente) : null;
      if (notice?.tone === "warning") toast.error(`Pedido #${order.numero} actualizado`, notice.text);
      else toast.success(`Pedido #${order.numero} actualizado`, notice?.text);
      await load();
    } catch (caught) {
      toast.error(`No pudimos actualizar el pedido #${order.numero}`, humanMessage(caught));
      await load();
    } finally {
      setBusyId(null);
    }
  }

  async function changeSetting(patch: Partial<OrderSettings>) {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setSettings(next);
    try {
      setSettings(await api.orders.saveSettings(next));
      toast.success("Guardado", "Los avisos al cliente se actualizaron.");
    } catch (caught) {
      setSettings(settings);
      toast.error("No pudimos guardar", humanMessage(caught));
    }
  }

  const waiting = orders?.filter((o) => o.estado === "por_confirmar" || o.estado === "confirmado").length ?? 0;
  const working = orders?.filter((o) => o.estado === "en_curso").length ?? 0;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-ink">Comanda</h1>
          <p className="mt-1 text-sm text-ink-soft">
            {orders === null
              ? "Cargando la comanda…"
              : `${waiting} por atender · ${working} en marcha. Toca el botón de una tarjeta para pasarla a la siguiente etapa.`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {updatedAt ? (
            <span className="text-xs text-ink-faint" role="status">
              {offline ? "Sin conexión, reintentando…" : "Se actualiza sola"}
            </span>
          ) : null}
          <Button
            variant="secondary"
            aria-pressed={sound}
            onClick={toggleSound}
            title="Suena una campanita cuando entra un pedido nuevo"
          >
            {sound ? <VolumeIcon className="size-5" /> : <VolumeOffIcon className="size-5" />}
            {sound ? "Sonido activado" : "Activar sonido"}
          </Button>
        </div>
      </header>

      <OrdersNav />

      {error && orders === null ? (
        <Alert
          tone="danger"
          action={
            <Button variant="secondary" onClick={() => void load()}>
              Reintentar
            </Button>
          }
        >
          {error}
        </Alert>
      ) : null}

      {orders === null && !error ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(15rem,1fr))] gap-4">
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} className="h-72 w-full" />
          ))}
        </div>
      ) : null}

      {orders !== null ? (
        <ComandaBoard orders={orders} now={now} busyId={busyId} onAdvance={(o, a) => void advance(o, a)} />
      ) : null}

      {canConfigure && settings ? (
        <Card
          title="Avisos al cliente"
          description="Cuando mueves un pedido, puedes avisarle por WhatsApp. El aviso solo sale si el cliente te escribió en las últimas 24 horas."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Checkbox
              label="Avisar que su pedido está listo para recoger"
              hint="Solo para pedidos de recojo en tienda."
              checked={settings.avisar_listo}
              onChange={(event) => void changeSetting({ avisar_listo: event.target.checked })}
            />
            <Checkbox
              label="Avisar que su pedido ya va en camino"
              hint="Solo para pedidos con delivery."
              checked={settings.avisar_en_camino}
              onChange={(event) => void changeSetting({ avisar_en_camino: event.target.checked })}
            />
          </div>
          <p className="mt-3 text-xs text-ink-faint">
            Puedes cambiar lo que dicen en Asistente → Comportamiento.
          </p>
        </Card>
      ) : null}
    </div>
  );
}
