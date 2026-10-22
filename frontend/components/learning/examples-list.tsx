"use client";

/** The real conversations the assistant consults when a similar case comes up (feature 017, phase 2). */

import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/toast";
import { Alert, Button, Card, Skeleton, cx } from "@/components/ui";
import { api, type ExampleRow } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const ORIGINS: Record<string, string> = {
  importacion: "De un chat importado",
  venta_confirmada: "De una venta confirmada",
};

export function ExamplesList({ refreshKey }: { refreshKey: number }) {
  const toast = useToast();
  const [rows, setRows] = useState<ExampleRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows((await api.assistant.examples()).items);
    } catch (caught) {
      setError(humanMessage(caught));
      setRows([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function run(id: string, action: () => Promise<unknown>, done: string) {
    setBusy(id);
    setError(null);
    try {
      await action();
      toast.success("Listo", done);
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Ejemplos reales" description="Conversaciones de tu negocio, sin datos personales. Cuando llega un caso parecido, el asistente se guía por cómo respondieron tus vendedores.">
      <div className="space-y-3" data-testid="learning-examples">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {rows === null ? <Skeleton className="h-20 w-full" /> : null}
        {rows && rows.length === 0 ? <p className="text-sm text-ink-faint">Todavía no hay ejemplos aprobados.</p> : null}
        {rows && rows.length > 0 ? (
          <ul className="space-y-2">
            {rows.map((row) => (
              <li key={row.id} className="rounded-lg border border-line p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cx("font-medium", row.estado === "activo" ? "text-ink" : "text-ink-faint line-through")}>{row.titulo}</span>
                  <span className="text-xs text-ink-faint">{ORIGINS[row.origen] ?? row.origen}</span>
                  <span className="ml-auto flex gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      loading={busy === row.id}
                      onClick={() => void run(row.id, () => api.assistant.setExample(row.id, row.estado !== "activo"), row.estado === "activo" ? "Apagado" : "Encendido")}
                    >
                      {row.estado === "activo" ? "Apagar" : "Encender"}
                    </Button>
                    <Button type="button" variant="ghost" disabled={busy === row.id} onClick={() => void run(row.id, () => api.assistant.deleteExample(row.id), "Quitado")}>
                      Quitar
                    </Button>
                  </span>
                </div>
                <p className="mt-1 text-ink-soft">{row.tema}</p>
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-ink-faint">Ver la conversación</summary>
                  <ol className="mt-1 space-y-0.5 text-ink-soft">
                    {row.mensajes.map((line, index) => (
                      <li key={index}>
                        <span className="font-semibold text-ink">{line.de === "cliente" ? "Cliente" : "Vendedor"}:</span> {line.texto}
                      </li>
                    ))}
                  </ol>
                </details>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}
