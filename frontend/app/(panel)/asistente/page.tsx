"use client";

/**
 * Asistente · Resumen (TASK-437, TASK-441).
 *
 * Where the business stands: one progress bar, the activation steps in order
 * with a link to each, and the state of every section. The next step is the
 * one thing the screen asks for.
 */

import { useCallback, useEffect, useState } from "react";

import { AssistantHeader } from "@/components/assistant/section-nav";
import { ActivationSteps, ProgressBar } from "@/components/assistant/overview";
import { Alert, Card, Skeleton, cx } from "@/components/ui";
import { api, type Overview } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const SECTION_LABELS: Record<string, string> = {
  informacion_basica: "Datos básicos",
  informacion_negocio: "Información del negocio",
  comportamiento: "Comportamiento",
  proceso_venta: "Proceso de venta",
  productos: "Productos",
  situaciones: "Situaciones",
  multimedia: "Multimedia",
  conocimiento: "Conocimiento",
};

const STATE_LABELS: Record<string, { text: string; tone: string }> = {
  completa: { text: "Completa", tone: "bg-success-soft text-success" },
  parcial: { text: "A medias", tone: "bg-warning-soft text-warning" },
  vacia: { text: "Pendiente", tone: "bg-surface-muted text-ink-soft" },
  opcional: { text: "Opcional", tone: "bg-surface-muted text-ink-faint" },
};

export default function AssistantOverviewPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setOverview(await api.assistant.overview());
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Asistente"
        description="Todo lo que tu asistente sabe y hace: tu información, su forma de hablar, tu proceso de venta y tus propias reglas."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {!overview && !error ? <Skeleton className="h-48 w-full" /> : null}
      {overview ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card title="Para activar tu asistente">
            <div className="space-y-4">
              <ProgressBar value={overview.progreso} />
              <ActivationSteps overview={overview} />
            </div>
          </Card>
          <Card title="Secciones">
            <ul className="divide-y divide-line">
              {overview.secciones.map((section) => {
                const state = STATE_LABELS[section.estado] ?? STATE_LABELS.vacia;
                return (
                  <li key={section.clave} className="flex items-center justify-between py-2.5 text-sm">
                    <span className="text-ink">{SECTION_LABELS[section.clave] ?? section.clave}</span>
                    <span className={cx("rounded-full px-2.5 py-0.5 text-xs font-medium", state.tone)}>
                      {state.text}
                    </span>
                  </li>
                );
              })}
            </ul>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
