"use client";

/**
 * "Chat web" (feature 019, phase F): the business's chat for its website and for the link in the bio of its
 * social networks. Off until the owner turns it on; the address and the snippet are shown once it is.
 * Saved, the settings collapse into a summary with an "Editar" button.
 */

import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/toast";
import { Alert, Button, Card, Checkbox, Field, Skeleton, TextArea } from "@/components/ui";
import { api, type WebChatSettings } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

function Copy({ label, value }: { label: string; value: string }) {
  const toast = useToast();
  return (
    <div className="space-y-1">
      <p className="text-sm font-medium text-ink">{label}</p>
      <div className="flex flex-wrap items-start gap-2">
        <code className="min-w-0 flex-1 break-all rounded-lg bg-surface-muted px-3 py-2 text-xs text-ink">{value}</code>
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            void navigator.clipboard
              ?.writeText(value)
              .then(() => toast.success("Copiado", label))
              .catch(() => undefined);
          }}
        >
          Copiar
        </Button>
      </div>
    </div>
  );
}

export function WebChatCard() {
  const toast = useToast();
  const [chat, setChat] = useState<WebChatSettings | null>(null);
  const [editing, setEditing] = useState(false);
  const [saludo, setSaludo] = useState("");
  const [color, setColor] = useState("#2563eb");
  const [limit, setLimit] = useState("300");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const current = await api.webChat.get();
      setChat(current);
      setSaludo(current.saludo ?? "");
      setColor(current.color);
      setLimit(String(current.limite_diario));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(patch: Parameters<typeof api.webChat.save>[0], done: string) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.webChat.save(patch);
      setChat(updated);
      setEditing(false);
      toast.success("Guardado", done);
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function rotate() {
    setBusy(true);
    setError(null);
    try {
      setChat(await api.webChat.rotate());
      toast.success("Dirección nueva", "La anterior dejó de funcionar");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <div className="space-y-4" data-testid="web-chat">
        <div>
          <h2 className="font-heading text-lg font-semibold text-ink">Chat web</h2>
          <p className="text-sm text-ink-soft">
            El mismo asistente atiende en tu página web y en el enlace que pongas en tus redes sociales. No necesita
            número de WhatsApp.
          </p>
        </div>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {!chat ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <Checkbox
              label="Chat web activado"
              hint="Apagado, nadie puede abrirlo."
              checked={chat.enabled}
              disabled={busy}
              onChange={(e) => void save({ enabled: e.target.checked }, e.target.checked ? "Chat web activado" : "Chat web apagado")}
            />
            {chat.enabled ? (
              <div className="space-y-3">
                <Copy label="Enlace para tus redes sociales" value={chat.url} />
                <Copy label="Código para tu página web (pégalo antes de </body>)" value={chat.snippet} />
              </div>
            ) : null}

            {!editing ? (
              <div className="space-y-2 text-sm">
                <p className="text-ink-soft">
                  Saludo: <span className="text-ink">{chat.saludo ?? "el del asistente"}</span> · Color:{" "}
                  <span className="inline-block size-3 rounded-full align-middle" style={{ background: chat.color }} />{" "}
                  {chat.color} · Tope: {chat.limite_diario} mensajes al día
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="secondary" onClick={() => setEditing(true)}>
                    Editar
                  </Button>
                  <Button type="button" variant="ghost" disabled={busy} onClick={() => void rotate()}>
                    Cambiar la dirección
                  </Button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <TextArea
                  label="Saludo que se ve al abrir (opcional)"
                  rows={2}
                  maxLength={300}
                  value={saludo}
                  onChange={(e) => setSaludo(e.target.value)}
                />
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                    Color
                    <input
                      type="color"
                      value={color}
                      onChange={(e) => setColor(e.target.value)}
                      className="h-11 w-full cursor-pointer rounded-lg border border-line-strong bg-surface"
                    />
                  </label>
                  <Field
                    label="Tope de mensajes al día"
                    inputMode="numeric"
                    value={limit}
                    hint="Entre 10 y 5000. Protege tu cuota si alguien abusa del chat."
                    onChange={(e) => setLimit(e.target.value)}
                  />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    loading={busy}
                    onClick={() =>
                      void save({ saludo: saludo.trim(), color, limite_diario: Number(limit) }, "Chat web")
                    }
                  >
                    Guardar
                  </Button>
                  <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
                    Cancelar
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
