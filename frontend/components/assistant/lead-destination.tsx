"use client";

/**
 * Who a hot lead goes to (feature 019, phase E): when the assistant finds a customer ready to buy, the
 * lead is assigned to this person and they are told by email. Without one, it reaches the whole team.
 */

import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/toast";
import { Alert, Button, Select, Skeleton } from "@/components/ui";
import { api, type TeamMember } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

export function LeadDestination({ canEdit }: { canEdit: boolean }) {
  const toast = useToast();
  const [members, setMembers] = useState<TeamMember[] | null>(null);
  const [saved, setSaved] = useState("");
  const [chosen, setChosen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [team, config] = await Promise.all([api.team.get(), api.agentConfig.get()]);
      setMembers(team.members.filter((m) => m.status === "active"));
      setSaved(config.destino_leads ?? "");
      setChosen(config.destino_leads ?? "");
    } catch (caught) {
      setError(humanMessage(caught));
      setMembers([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const config = await api.agentConfig.save({ destino_leads: chosen || null });
      setSaved(config.destino_leads ?? "");
      toast.success("Guardado", "A quién se avisa de un cliente interesado");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (members === null) return <Skeleton className="h-20 w-full" />;

  return (
    <div className="space-y-3 rounded-lg border border-line p-3" data-testid="lead-destination">
      <div>
        <h3 className="font-semibold text-ink">A quién se avisa de un cliente interesado</h3>
        <p className="text-xs text-ink-soft">
          Cuando el asistente encuentra a alguien listo para comprar, se lo asigna a esta persona y le escribe un correo.
        </p>
      </div>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Select
        label="Persona"
        value={chosen}
        disabled={!canEdit}
        onChange={(e) => setChosen(e.target.value)}
        options={[
          { value: "", label: "Nadie en particular (le llega a todo el equipo)" },
          ...members.map((m) => ({ value: m.user_id, label: `${m.full_name} · ${m.email}` })),
        ]}
      />
      {canEdit ? (
        <Button type="button" loading={busy} disabled={chosen === saved} onClick={() => void save()}>
          Guardar
        </Button>
      ) : null}
    </div>
  );
}
