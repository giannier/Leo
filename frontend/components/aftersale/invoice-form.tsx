"use client";

/**
 * Issue the electronic receipt of an order (feature 019, phase G).
 *
 * A person's click, in two steps: look at what would be issued (the number, the buyer, the base, the tax
 * and the total), then confirm. A receipt is a tax document and the business answers for it, so nothing
 * here is automatic and nothing is issued on the first click. The provider's refusal is shown as it came.
 */

import { useEffect, useState } from "react";

import { formatMoney } from "@/components/orders/labels";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Field, Select } from "@/components/ui";
import { api, type Buyer, type InvoiceInfo, type InvoicePreview } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

type Kind = "boleta" | "factura";

export function InvoiceForm({
  orderId,
  sold,
  onChanged,
}: {
  orderId: string;
  sold: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const canVoid = useHasRole(...MANAGES_CONFIGURATION);
  const [info, setInfo] = useState<InvoiceInfo | null>(null);
  const [kind, setKind] = useState<Kind>("boleta");
  const [buyer, setBuyer] = useState<Buyer>({ documento_tipo: "ninguno", documento_numero: "", nombre: "", direccion: "", email: "" });
  const [preview, setPreview] = useState<InvoicePreview | null>(null);
  const [voiding, setVoiding] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const current = await api.aftersale.invoiceInfo(orderId);
      setInfo(current);
      setBuyer(current.sugerido);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId]);

  function edit(patch: Partial<Buyer>) {
    setPreview(null);
    setBuyer((current) => ({ ...current, ...patch }));
  }

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  if (!sold || info === null) return null;
  if (!info.disponible && !info.comprobante) return null;

  if (info.comprobante) {
    const standing = info.comprobante;
    return (
      <Card title="Comprobante electrónico">
        <div className="space-y-3 text-sm" data-testid="invoice-standing">
          <p className="text-ink">
            <span className="font-semibold">{standing.nombre}</span> emitido. Lo ves y lo descargas en «Documentos».
          </p>
          {canVoid ? (
            voiding ? (
              <div className="space-y-3 rounded-lg border border-line p-3">
                <Field
                  label="Motivo de la anulación"
                  value={reason}
                  maxLength={200}
                  hint="Queda registrado ante tu proveedor."
                  onChange={(e) => setReason(e.target.value)}
                />
                {error ? <Alert tone="danger">{error}</Alert> : null}
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="danger"
                    loading={busy === "void"}
                    disabled={reason.trim().length < 5}
                    onClick={() =>
                      void run("void", async () => {
                        await api.aftersale.voidInvoice(orderId, reason.trim());
                        toast.success("Anulado", standing.nombre);
                        setVoiding(false);
                        setReason("");
                        await load();
                        onChanged();
                      })
                    }
                  >
                    Anular comprobante
                  </Button>
                  <Button type="button" variant="ghost" onClick={() => setVoiding(false)}>
                    Cancelar
                  </Button>
                </div>
              </div>
            ) : (
              <Button type="button" variant="secondary" onClick={() => setVoiding(true)}>
                Anular comprobante
              </Button>
            )
          ) : (
            <p className="text-xs text-ink-faint">Solo el dueño y los administradores anulan un comprobante.</p>
          )}
        </div>
      </Card>
    );
  }

  const body = { tipo: kind, cliente: buyer };
  return (
    <Card
      title="Emitir comprobante electrónico"
      description={`Siguiente número: ${kind === "boleta" ? info.siguiente_boleta : info.siguiente_factura}.`}
    >
      <div className="space-y-4" data-testid="invoice-form">
        {info.solo_prueba ? (
          <Alert tone="warning">Estás en el entorno de pruebas del proveedor: este comprobante no es real.</Alert>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-2">
          <Select
            label="Comprobante"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as Kind);
              setPreview(null);
              if (e.target.value === "factura" && buyer.documento_tipo !== "ruc") edit({ documento_tipo: "ruc" });
            }}
            options={[
              { value: "boleta", label: "Boleta" },
              { value: "factura", label: "Factura (a un cliente con RUC)" },
            ]}
          />
          <Select
            label="Documento del cliente"
            value={buyer.documento_tipo}
            onChange={(e) => edit({ documento_tipo: e.target.value as Buyer["documento_tipo"] })}
            options={[
              { value: "dni", label: "DNI" },
              { value: "ruc", label: "RUC" },
              ...(kind === "boleta" ? [{ value: "ninguno", label: "Sin documento (boleta hasta S/ 700)" }] : []),
            ]}
          />
          {buyer.documento_tipo !== "ninguno" ? (
            <Field
              label={buyer.documento_tipo === "ruc" ? "RUC" : "DNI"}
              inputMode="numeric"
              value={buyer.documento_numero ?? ""}
              onChange={(e) => edit({ documento_numero: e.target.value })}
            />
          ) : null}
          <Field
            label={buyer.documento_tipo === "ruc" ? "Razón social" : "Nombre del cliente"}
            value={buyer.nombre ?? ""}
            onChange={(e) => edit({ nombre: e.target.value })}
          />
          <Field label="Dirección (opcional)" value={buyer.direccion ?? ""} onChange={(e) => edit({ direccion: e.target.value })} />
          <Field label="Correo (opcional)" type="email" value={buyer.email ?? ""} onChange={(e) => edit({ email: e.target.value })} />
        </div>
        <p className="text-xs text-ink-faint">
          Pide el DNI o el RUC por teléfono o en persona, no por el chat: WhatsApp no permite pedir documentos de identidad.
        </p>
        {error ? <Alert tone="danger">{error}</Alert> : null}

        {preview ? (
          <div className="space-y-2 rounded-lg border border-line bg-surface-muted p-3 text-sm" data-testid="invoice-preview">
            <p className="font-semibold text-ink">
              {preview.tipo === "boleta" ? "Boleta" : "Factura"} {preview.comprobante} · {preview.cliente} ({preview.documento})
            </p>
            <ul className="text-ink-soft">
              {preview.lineas.map((line, index) => (
                <li key={index}>
                  {Number(line.cantidad)} × {line.descripcion} — {formatMoney(line.total, preview.moneda)}
                </li>
              ))}
            </ul>
            <dl className="tabular space-y-0.5 border-t border-line pt-2">
              <div className="flex justify-between"><dt>Base</dt><dd>{formatMoney(preview.base, preview.moneda)}</dd></div>
              {Number(preview.descuento) > 0 ? (
                <div className="flex justify-between"><dt>Descuento (ya restado)</dt><dd>{formatMoney(preview.descuento, preview.moneda)}</dd></div>
              ) : null}
              <div className="flex justify-between"><dt>IGV</dt><dd>{formatMoney(preview.igv, preview.moneda)}</dd></div>
              <div className="flex justify-between font-semibold text-ink"><dt>Total</dt><dd>{formatMoney(preview.total, preview.moneda)}</dd></div>
            </dl>
            <Button
              type="button"
              loading={busy === "issue"}
              onClick={() =>
                void run("issue", async () => {
                  const issued = await api.aftersale.issueInvoice(orderId, body);
                  toast.success("Comprobante emitido", issued.nombre);
                  setPreview(null);
                  await load();
                  onChanged();
                })
              }
            >
              Emitir {preview.comprobante}
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            variant="secondary"
            loading={busy === "preview"}
            onClick={() => void run("preview", async () => setPreview(await api.aftersale.previewInvoice(orderId, body)))}
          >
            Ver cómo quedaría
          </Button>
        )}
      </div>
    </Card>
  );
}
