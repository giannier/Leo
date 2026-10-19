"use client";

/**
 * Whether YCloud's deliveries reach Leo (TASK-459): the first thing to look at when a
 * connected number "receives nothing". The usual causes, in order: no public URL (tunnel or
 * server), no webhook secret, or the webhook not created in YCloud's console.
 */

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui";
import { api, type ChannelHealth } from "@/lib/api/client";

function ago(iso: string | null): string {
  if (!iso) return "nunca";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "hace un momento";
  if (minutes < 60) return `hace ${minutes} min`;
  if (minutes < 48 * 60) return `hace ${Math.round(minutes / 60)} h`;
  return `hace ${Math.round(minutes / 1440)} días`;
}

export function ChannelHealthCard() {
  const [health, setHealth] = useState<ChannelHealth | null>(null);

  useEffect(() => {
    api.admin.channelHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  if (!health) return null;
  const silent = health.numeros_conectados > 0 && health.recibidos_24h === 0;
  return (
    <section aria-label="Estado del canal de WhatsApp" className="space-y-2" data-testid="channel-health">
      {!health.secreto_configurado ? (
        <Alert tone="danger" title="Falta el secreto del webhook de YCloud">
          Sin <code>YCLOUD_WEBHOOK_SECRET</code> Leo rechaza todos los mensajes que llegan. Cópialo de YCloud
          (Desarrolladores → Webhooks), ponlo en el <code>.env</code> y reinicia el backend.
        </Alert>
      ) : silent ? (
        <Alert tone="warning" title="No llega nada de YCloud">
          Hay {health.numeros_conectados} número(s) conectado(s) y ningún mensaje en 24 h (último: {ago(health.ultimo_webhook)}).
          Revisa que el webhook de YCloud apunte a <code>https://…/webhooks/ycloud</code> y que esa URL sea
          pública (túnel abierto o servidor).
        </Alert>
      ) : null}
      <p className="text-sm text-ink-soft">
        WhatsApp: {health.numeros_conectados} número(s) conectado(s) · último mensaje de YCloud {ago(health.ultimo_webhook)} ·{" "}
        {health.recibidos_24h} en 24 h{health.con_error_24h ? ` · ${health.con_error_24h} con error (ver Sin procesar)` : ""}
      </p>
    </section>
  );
}
