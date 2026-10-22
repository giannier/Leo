"use client";

/**
 * The documents of an order (feature 019, phase G): the boleta, the factura, the guía, the warranty.
 *
 * The business attaches the PDF it already has, or Leo makes the order's summary. When the customer asks
 * for one by WhatsApp, the assistant sends it from here, and only to the customer the order belongs to.
 * A document is never reachable by a key like the files of the library.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { DOCUMENT_TYPES } from "@/components/aftersale/labels";
import { formatDateTime } from "@/components/orders/labels";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Field, Select, Skeleton } from "@/components/ui";
import { api, type OrderDocumentRow } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const ORIGINS: Record<string, string> = {
  subido: "Adjuntado por tu equipo",
  generado: "Hecho por Leo",
  electronico: "Emitido electrónicamente",
};

function size(bytes: number): string {
  return bytes < 1_000_000 ? `${Math.max(1, Math.round(bytes / 1000))} KB` : `${(bytes / 1_000_000).toFixed(1)} MB`;
}

export function DocumentsCard({ orderId, sold }: { orderId: string; sold: boolean }) {
  const toast = useToast();
  const [items, setItems] = useState<OrderDocumentRow[] | null>(null);
  const [tipo, setTipo] = useState("boleta");
  const [serie, setSerie] = useState("");
  const [numero, setNumero] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setItems((await api.aftersale.documents(orderId)).items);
    } catch (caught) {
      setError(humanMessage(caught));
      setItems([]);
    }
  }, [orderId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(name: string, action: () => Promise<unknown>, done?: string) {
    setBusy(name);
    setError(null);
    try {
      await action();
      if (done) toast.success("Listo", done);
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function open(documentId: string) {
    setError(null);
    try {
      const { url } = await api.aftersale.documentLink(orderId, documentId);
      window.open(url, "_blank", "noopener");
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  async function attach() {
    if (!file) return;
    await run(
      "attach",
      async () => {
        await api.aftersale.attach(orderId, file, { tipo, serie: serie.trim(), numero: numero.trim() });
        setFile(null);
        setSerie("");
        setNumero("");
        if (input.current) input.current.value = "";
      },
      "Documento adjunto",
    );
  }

  return (
    <Card title="Documentos" description="Si el cliente pide su boleta, factura o garantía por WhatsApp, el asistente se la envía desde aquí.">
      <div className="space-y-4" data-testid="order-documents">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {items === null ? <Skeleton className="h-16 w-full" /> : null}
        {items && items.length === 0 ? <p className="text-sm text-ink-faint">Todavía no hay documentos.</p> : null}
        {items && items.length > 0 ? (
          <ul className="divide-y divide-line">
            {items.map((doc) => (
              <li key={doc.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-ink">
                    {doc.nombre}
                    {doc.estado === "anulado" ? <span className="ml-2 text-xs text-danger">(anulado)</span> : null}
                  </p>
                  <p className="text-xs text-ink-faint">
                    {ORIGINS[doc.origen] ?? doc.origen} · {size(doc.tamano)} · {formatDateTime(doc.creado)}
                    {doc.enviado ? ` · se envió al cliente el ${formatDateTime(doc.enviado)}` : ""}
                  </p>
                </div>
                <Button type="button" variant="secondary" onClick={() => void open(doc.id)}>
                  Abrir
                </Button>
                {doc.estado === "emitido" ? (
                  <Button
                    type="button"
                    variant="ghost"
                    loading={busy === `void-${doc.id}`}
                    onClick={() => void run(`void-${doc.id}`, () => api.aftersale.voidDocument(orderId, doc.id), "Documento anulado")}
                  >
                    Anular
                  </Button>
                ) : null}
                {doc.origen !== "electronico" ? (
                  <Button
                    type="button"
                    variant="ghost"
                    loading={busy === `del-${doc.id}`}
                    onClick={() => void run(`del-${doc.id}`, () => api.aftersale.removeDocument(orderId, doc.id), "Documento quitado")}
                  >
                    Quitar
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}

        {sold ? (
          <div className="space-y-3 border-t border-line pt-4">
            <p className="text-sm font-medium text-ink">Adjuntar un documento</p>
            <div className="grid gap-3 sm:grid-cols-3">
              <Select
                label="Tipo"
                value={tipo}
                onChange={(e) => setTipo(e.target.value)}
                options={Object.entries(DOCUMENT_TYPES).map(([value, label]) => ({ value, label }))}
              />
              <Field label="Serie (opcional)" value={serie} maxLength={20} placeholder="B001" onChange={(e) => setSerie(e.target.value)} />
              <Field label="Número (opcional)" value={numero} maxLength={20} placeholder="123" onChange={(e) => setNumero(e.target.value)} />
            </div>
            <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
              Archivo (PDF, JPG o PNG)
              <input
                ref={input}
                type="file"
                accept="application/pdf,image/jpeg,image/png"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="text-sm font-normal"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <Button type="button" loading={busy === "attach"} disabled={!file || busy !== null} onClick={() => void attach()}>
                Adjuntar
              </Button>
              <Button
                type="button"
                variant="secondary"
                loading={busy === "summary"}
                disabled={busy !== null}
                onClick={() => void run("summary", () => api.aftersale.makeSummary(orderId), "Resumen creado")}
              >
                Crear resumen en PDF
              </Button>
            </div>
            <p className="text-xs text-ink-faint">
              El resumen lista lo que compró y lo que pagó; no es una boleta ni una factura.
            </p>
          </div>
        ) : (
          <p className="text-xs text-ink-faint">Podrás adjuntar documentos cuando confirmes el pedido.</p>
        )}
      </div>
    </Card>
  );
}
