"use client";

/**
 * The inbox's two settings (Feature 007, RF-004 and RF-008), for whoever manages
 * the business: after how long a conversation taken by a person goes back to the
 * assistant, and after how long a conversation waiting for a person sends an email.
 */

import { useEffect, useState } from "react";

import { Alert, Button, Select, Skeleton } from "@/components/ui";
import { api, type InboxSettings } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const RETURN_OPTIONS = [
  { value: "15", label: "15 minutos" },
  { value: "30", label: "30 minutos" },
  { value: "60", label: "1 hora" },
  { value: "120", label: "2 horas" },
  { value: "never", label: "Nunca: solo cuando la devuelva" },
];
const QUEUE_OPTIONS = [
  { value: "5", label: "5 minutos" },
  { value: "10", label: "10 minutos" },
  { value: "30", label: "30 minutos" },
  { value: "60", label: "1 hora" },
];

export function InboxSettingsCard({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<InboxSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  useEffect(() => {
    api.conversations
      .settings()
      .then(setSettings)
      .catch((caught) => setMessage({ tone: "danger", text: humanMessage(caught) }));
  }, []);

  if (!settings) return message ? <Alert tone="danger">{message.text}</Alert> : <Skeleton className="h-32 w-full" />;

  const returnValue = settings.auto_return_minutes === null || settings.auto_return_minutes === undefined
    ? "never"
    : String(settings.auto_return_minutes);

  // A value set through the API that is not in a list is shown as it is.
  const returnOptions = RETURN_OPTIONS.some((o) => o.value === returnValue)
    ? RETURN_OPTIONS
    : [{ value: returnValue, label: `${returnValue} minutos` }, ...RETURN_OPTIONS];
  const current = String(settings.unattended_queue_minutes);
  const queueOptions = QUEUE_OPTIONS.some((o) => o.value === current)
    ? QUEUE_OPTIONS
    : [{ value: current, label: `${current} minutos` }, ...QUEUE_OPTIONS];

  return (
    <form
      className="space-y-3 rounded-xl border border-line bg-surface p-4"
      aria-label="Ajustes de la bandeja"
      onSubmit={async (event) => {
        event.preventDefault();
        setSaving(true);
        setMessage(null);
        try {
          setSettings(await api.conversations.saveSettings(settings));
          setMessage({ tone: "success", text: "Ajustes guardados." });
        } catch (caught) {
          setMessage({ tone: "danger", text: humanMessage(caught) });
        } finally {
          setSaving(false);
        }
      }}
    >
      <Select
        label="Si quien tomó una conversación no escribe, vuelve al asistente después de"
        value={returnValue}
        onChange={(event) =>
          setSettings({
            ...settings,
            auto_return_minutes: event.target.value === "never" ? null : Number(event.target.value),
          })
        }
        options={returnOptions}
      />
      <Select
        label="Si una conversación espera a una persona y nadie la toma, avisar por correo después de"
        value={String(settings.unattended_queue_minutes)}
        onChange={(event) => setSettings({ ...settings, unattended_queue_minutes: Number(event.target.value) })}
        options={queueOptions}
      />
      {message ? <Alert tone={message.tone}>{message.text}</Alert> : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cerrar
        </Button>
        <Button type="submit" loading={saving}>
          Guardar ajustes
        </Button>
      </div>
    </form>
  );
}
