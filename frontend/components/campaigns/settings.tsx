"use client";

/**
 * Campaign settings (Feature 011): asking for permission after a sale (C-102), and the
 * template that follows up a customer once WhatsApp's 24 hours are over (C-110).
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { Alert, Button, Card, Checkbox, Select, Skeleton } from "@/components/ui";
import { Bubble } from "@/components/campaigns/templates";
import { api, type CampaignSettings, type MessageTemplate } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

export function SettingsPanel({ canEdit }: { canEdit: boolean }) {
  const [settings, setSettings] = useState<CampaignSettings | null>(null);
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [ask, setAsk] = useState(true);
  const [followUp, setFollowUp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([api.campaigns.settings(), api.templates.list()])
      .then(([current, list]) => {
        setSettings(current);
        setAsk(current.optin_preguntar);
        setFollowUp(current.seguimiento_plantilla_id ?? "");
        setTemplates(list);
      })
      .catch((caught) => setError(humanMessage(caught)));
  }, []);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      setSettings(
        await api.campaigns.saveSettings({ optin_preguntar: ask, seguimiento_plantilla_id: followUp || null }),
      );
      setSaved(true);
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (!settings) return error ? <Alert tone="danger">{error}</Alert> : <Skeleton className="h-40 w-full" />;
  const utility = templates.filter((t) => t.category === "utility" && t.status === "approved");

  return (
    <div className="space-y-5">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {saved ? <Alert tone="success">Guardado.</Alert> : null}
      <Card
        title="Pedir permiso para promociones"
        description="Después de que confirmas un pago, Leo le pregunta una sola vez al cliente si quiere recibir promociones. Sin IA, sin costo."
      >
        <div className="grid gap-4 md:grid-cols-[1fr_16rem]">
          <Checkbox
            checked={ask}
            disabled={!canEdit}
            onChange={(e) => setAsk(e.target.checked)}
            label="Preguntar después de una venta confirmada"
            hint="Quien toca «Sí, avísame» puede recibir tus campañas. Quien escribe BAJA deja de recibirlas."
          />
          <div className="space-y-2">
            <Bubble body="¿Quieres que te avisemos de promociones y novedades por aquí? Puedes darte de baja cuando quieras escribiendo BAJA." />
            <div className="flex gap-2 text-xs">
              <span className="flex-1 rounded-lg border border-line bg-surface p-2 text-center text-info">Sí, avísame</span>
              <span className="flex-1 rounded-lg border border-line bg-surface p-2 text-center text-info">No, gracias</span>
            </div>
          </div>
        </div>
      </Card>

      <Card
        title="Seguimiento después de 24 horas"
        description="WhatsApp solo deja escribir libremente durante 24 horas desde el último mensaje del cliente. Después, el seguimiento puede ir como una plantilla de Utilidad aprobada (máximo 1 por conversación y 2 al mes por cliente)."
      >
        <div className="space-y-3">
          {!settings.seguimiento_activo ? (
            <Alert tone="info">
              Los seguimientos están apagados. Enciéndelos en <Link href="/asistente/comportamiento" className="font-semibold underline">Asistente</Link>.
            </Alert>
          ) : null}
          <Select
            label="Plantilla para el seguimiento"
            value={followUp}
            disabled={!canEdit}
            onChange={(e) => setFollowUp(e.target.value)}
            options={[
              { value: "", label: "Ninguna (no seguir después de 24 horas)" },
              ...utility.map((t) => ({ value: t.id, label: t.label })),
            ]}
            hint={utility.length ? undefined : "Necesitas una plantilla de Utilidad aprobada (por ejemplo «Recordatorio de pago pendiente»)."}
          />
        </div>
      </Card>

      {canEdit ? (
        <Button type="button" loading={busy} onClick={() => void save()}>
          Guardar ajustes
        </Button>
      ) : null}
    </div>
  );
}
