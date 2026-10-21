"use client";

/**
 * "Leer mi web ahora": the website written once in the business step becomes knowledge with one click,
 * instead of asking for it again in Conocimiento (feature 019).
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { CheckIcon } from "@/components/icons";
import { useToast } from "@/components/toast";
import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const DOMAIN = /^(?:https?:\/\/)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?$/i;
const SOCIAL = /(^|\.)(instagram|facebook|tiktok|twitter|linktr)\.|(^|\/)(wa\.me|x\.com)/i;

/** The address as the server reads it: with https, or null when it is not a website. */
export function websiteOf(raw: string): string | null {
  const text = raw.trim();
  if (!text || /\s/.test(text) || !DOMAIN.test(text) || SOCIAL.test(text)) return null;
  return /^https?:\/\//i.test(text) ? text : `https://${text}`;
}

export function ReadMyWeb({ website }: { website: string }) {
  const toast = useToast();
  const url = websiteOf(website);
  const [alreadyRead, setAlreadyRead] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.knowledge
      .list()
      .then((page) => live && setAlreadyRead(page.sources.some((s) => s.type === "web")))
      .catch(() => live && setAlreadyRead(false));
    return () => {
      live = false;
    };
  }, []);

  if (!url || alreadyRead === null) return null;

  if (alreadyRead) {
    return (
      <p className="inline-flex items-center gap-1.5 rounded-full bg-success-soft px-2.5 py-1 text-sm font-semibold text-success">
        <CheckIcon className="size-4" />
        Tu web ya está en el conocimiento del asistente
      </p>
    );
  }

  async function read() {
    if (!url || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.knowledge.addWeb(url);
      setAlreadyRead(true);
      toast.success("Estamos leyendo tu web", "Puede tardar un minuto; te avisamos cuando esté lista.");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-line bg-surface-muted p-3">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <p className="text-sm text-ink-soft">
        ¿Quieres que el asistente conozca tu negocio leyendo <strong>{url.replace(/^https?:\/\//, "")}</strong>? Lee
        hasta 20 páginas. Lo ves y lo ajustas en{" "}
        <Link href="/knowledge" className="font-semibold text-accent underline">
          Conocimiento
        </Link>
        .
      </p>
      <Button type="button" variant="secondary" loading={busy} onClick={() => void read()}>
        Leer mi web ahora
      </Button>
    </div>
  );
}
