"use client";

/**
 * One link per social network (feature 019, phase F: attribution of origin).
 *
 * The same chat, with a mark of where the customer comes from: the WhatsApp link opens the chat with
 * «Hola, te escribo desde Instagram» already written, and the web chat link carries `?o=instagram`. The lead
 * keeps that origin, and Resultados counts the leads (and the sales) of each network.
 */

import { useEffect, useState } from "react";

import { Copy } from "@/components/channel/web-chat-card";
import { Card, Select } from "@/components/ui";
import { api, type WebChatSettings } from "@/lib/api/client";

const NETWORKS = ["Instagram", "Facebook", "TikTok", "YouTube", "Google", "LinkedIn"] as const;

export function SocialLinks({ phone }: { phone: string | null }) {
  const [network, setNetwork] = useState<string>(NETWORKS[0]);
  const [chat, setChat] = useState<WebChatSettings | null>(null);

  useEffect(() => {
    api.webChat
      .get()
      .then(setChat)
      .catch(() => setChat(null));
  }, []);

  const whatsapp = phone
    ? `https://wa.me/${phone.replace(/\D/g, "")}?text=${encodeURIComponent(`Hola, te escribo desde ${network}`)}`
    : null;
  const web = chat?.enabled ? `${chat.url}${chat.url.includes("?") ? "&" : "?"}o=${network.toLowerCase()}` : null;

  if (!whatsapp && !web) return null;

  return (
    <Card>
      <div className="space-y-4" data-testid="social-links">
        <div>
          <h2 className="font-heading text-lg font-semibold text-ink">Un enlace por red social</h2>
          <p className="text-sm text-ink-soft">
            Pon cada enlace en la biografía o en los anuncios de su red. Así sabrás de cuál llega cada cliente: lo verás en
            Resultados.
          </p>
        </div>
        <Select
          label="Red social"
          value={network}
          onChange={(e) => setNetwork(e.target.value)}
          options={NETWORKS.map((name) => ({ value: name, label: name }))}
        />
        {whatsapp ? <Copy label="Enlace de WhatsApp" value={whatsapp} /> : null}
        {web ? <Copy label="Enlace del chat web" value={web} /> : null}
      </div>
    </Card>
  );
}
