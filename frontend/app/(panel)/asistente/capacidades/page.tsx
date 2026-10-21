"use client";

/**
 * Asistente · Qué hará tu agente (feature 019).
 *
 * The owner says what the agent does; the template, the objective and what the setup asks for follow from it.
 */

import { useEffect, useState } from "react";

import { CapabilityPicker, HowItSells } from "@/components/assistant/capability-picker";
import { LeadDestination } from "@/components/assistant/lead-destination";
import { QualificationQuestions } from "@/components/assistant/qualification-questions";
import { AssistantHeader } from "@/components/assistant/section-nav";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Skeleton } from "@/components/ui";
import { api } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

export default function CapabilitiesPage() {
  const canEdit = useHasRole(...MANAGES_CONFIGURATION);
  const toast = useToast();
  const [saved, setSaved] = useState<string[] | null>(null);
  const [draft, setDraft] = useState<string[]>([]);
  const [suggested, setSuggested] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.agentConfig
      .get()
      .then((config) => {
        const current = config.capacidades ?? ["atender", "vender"];
        setSaved(current);
        setDraft(current);
      })
      .catch((caught) => setError(humanMessage(caught)));
    api.assistant
      .rubro()
      .then((rubro) => {
        setSuggested(rubro.capacidades_sugeridas ?? []);
        setNotice(rubro.aviso_politica ?? null);
      })
      .catch(() => setSuggested([]));
  }, []);

  const changed = saved !== null && draft.join(",") !== saved.join(",");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const config = await api.agentConfig.save({ capacidades: draft as ("atender" | "vender" | "cotizar" | "agendar" | "captar")[] });
      const current = config.capacidades ?? draft;
      setSaved(current);
      setDraft(current);
      toast.success("Guardado", "Lo que hará tu agente");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Qué hará tu agente"
        description="Elige lo que hace y Leo ajusta cómo conversa y qué te pide para dejarlo listo."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {saved === null ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <>
          <Card>
            <div className="space-y-4">
              <CapabilityPicker value={draft} onChange={setDraft} suggested={suggested} notice={notice} disabled={!canEdit} />
              <HowItSells refreshKey={(saved ?? []).join(",")} />
              {canEdit ? (
                <Button type="button" loading={busy} disabled={!changed} onClick={() => void save()}>
                  Guardar
                </Button>
              ) : null}
            </div>
          </Card>
          {saved.includes("captar") ? (
            <Card>
              <div className="space-y-4">
                <QualificationQuestions canEdit={canEdit} />
                <LeadDestination canEdit={canEdit} />
              </div>
            </Card>
          ) : null}
        </>
      )}
    </div>
  );
}
