"use client";

/** The returns of one order, and a way to register one a customer made by phone or at the counter. */

import { useCallback, useEffect, useState } from "react";

import { RETURN_REASONS, RETURN_TYPES } from "@/components/aftersale/labels";
import { ReturnCard } from "@/components/aftersale/return-card";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Select, Skeleton, TextArea } from "@/components/ui";
import { api, type ReturnList } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

type Tipo = "devolucion" | "cambio" | "garantia" | "reclamo";
type Motivo = "defectuoso" | "equivocado" | "incompleto" | "no_conforme" | "tardio" | "otro";

export function OrderReturns({ orderId, sold }: { orderId: string; sold: boolean }) {
  const toast = useToast();
  const [list, setList] = useState<ReturnList | null>(null);
  const [adding, setAdding] = useState(false);
  const [tipo, setTipo] = useState<Tipo>("devolucion");
  const [motivo, setMotivo] = useState<Motivo>("defectuoso");
  const [detalle, setDetalle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setList(await api.aftersale.returnsOfOrder(orderId));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, [orderId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function register() {
    setBusy(true);
    setError(null);
    try {
      await api.aftersale.openReturn({ order_id: orderId, tipo, motivo, detalle: detalle.trim(), productos: [] });
      toast.success("Registrada", RETURN_TYPES[tipo]);
      setAdding(false);
      setDetalle("");
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (!sold && (list === null || list.items.length === 0)) return null;
  return (
    <Card title="Devoluciones y reclamos">
      <div className="space-y-3" data-testid="order-returns">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {list === null && !error ? <Skeleton className="h-12 w-full" /> : null}
        {list && list.items.length > 0 ? (
          <ul className="space-y-3">
            {list.items.map((row) => (
              <ReturnCard key={row.id} row={row} />
            ))}
          </ul>
        ) : null}
        {list && list.items.length === 0 ? (
          <p className="text-sm text-ink-faint">Este pedido no tiene solicitudes.</p>
        ) : null}
        {sold && !adding ? (
          <Button type="button" variant="secondary" onClick={() => setAdding(true)}>
            Registrar una devolución o reclamo
          </Button>
        ) : null}
        {adding ? (
          <div className="space-y-3 rounded-lg border border-line p-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Select
                label="Qué pide"
                value={tipo}
                onChange={(e) => setTipo(e.target.value as Tipo)}
                options={Object.entries(RETURN_TYPES).map(([value, label]) => ({ value, label }))}
              />
              <Select
                label="Por qué"
                value={motivo}
                onChange={(e) => setMotivo(e.target.value as Motivo)}
                options={Object.entries(RETURN_REASONS).map(([value, label]) => ({ value, label }))}
              />
            </div>
            <TextArea
              label="Qué pasó"
              rows={3}
              maxLength={1000}
              value={detalle}
              onChange={(e) => setDetalle(e.target.value)}
              hint="Con las palabras del cliente. Es lo que verás al decidir."
            />
            <div className="flex gap-2">
              <Button type="button" loading={busy} onClick={() => void register()}>
                Registrar
              </Button>
              <Button type="button" variant="ghost" onClick={() => setAdding(false)}>
                Cancelar
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
