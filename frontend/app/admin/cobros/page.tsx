"use client";

/**
 * Cobros (Feature 009, TASK-908): what Giannier does by hand while payments are
 * manual. See who owes what, record a transfer or Yape against its reference,
 * create coupons, and give a business a few more days. Every action is audited
 * on the server.
 */

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, Field, Skeleton } from "@/components/ui";
import { api, type AdminCoupon, type AdminSubscription } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

function usd(value: string | number): string {
  return `US$ ${Number(value).toFixed(2)}`;
}

export default function CobrosPage() {
  const [rows, setRows] = useState<AdminSubscription[] | null>(null);
  const [coupons, setCoupons] = useState<AdminCoupon[]>([]);
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [subscriptions, existing] = await Promise.all([
        api.admin.billing.subscriptions(),
        api.admin.billing.coupons(),
      ]);
      setRows(subscriptions);
      setCoupons(existing);
    } catch (caught) {
      setNotice({ tone: "danger", text: humanMessage(caught) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pending = (rows ?? []).flatMap((row) => row.pendientes.map((charge) => ({ row, charge })));

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Cobros</h1>
        <p className="text-sm text-ink-soft">
          Los pagos llegan por transferencia o Yape con una referencia. Regístralos aquí y el plan del
          negocio se activa al instante.
        </p>
      </header>
      {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}

      <RecordPayment
        onDone={async (text) => {
          setNotice({ tone: "success", text });
          await load();
        }}
        onError={(text) => setNotice({ tone: "danger", text })}
      />

      <Card title={`Por cobrar (${pending.length})`}>
        {rows === null ? (
          <Skeleton className="h-24 w-full" />
        ) : pending.length === 0 ? (
          <p className="text-sm text-ink-soft">Nada pendiente.</p>
        ) : (
          <ul className="divide-y divide-line text-sm" data-testid="admin-pending">
            {pending.map(({ row, charge }) => (
              <li key={charge.id} className="flex flex-wrap justify-between gap-2 py-2">
                <span>
                  <span className="font-semibold text-ink">{row.negocio}</span> · {charge.concepto}
                </span>
                <span className="font-mono">
                  {charge.referencia} · {usd(charge.monto)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Negocios">
        {rows === null ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <ul className="divide-y divide-line text-sm">
            {rows.map((row) => (
              <li key={row.tenant_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-semibold text-ink">{row.negocio}</span> · {row.plan} ·{" "}
                  {row.estado ?? row.estado_negocio}
                  {row.hasta ? ` · hasta ${new Date(row.hasta).toLocaleDateString("es-PE")}` : ""}
                </span>
                {row.estado === "active" || row.estado === "past_due" ? (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={async () => {
                      const reason = window.prompt("¿Por qué le das 7 días más? (queda en la auditoría)");
                      if (!reason) return;
                      try {
                        await api.admin.billing.extend(row.tenant_id, { dias: 7, motivo: reason });
                        setNotice({ tone: "success", text: `${row.negocio}: 7 días más.` });
                        await load();
                      } catch (caught) {
                        setNotice({ tone: "danger", text: humanMessage(caught) });
                      }
                    }}
                  >
                    +7 días
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Coupons
        coupons={coupons}
        onDone={async (text) => {
          setNotice({ tone: "success", text });
          await load();
        }}
        onError={(text) => setNotice({ tone: "danger", text })}
      />
    </div>
  );
}

function RecordPayment({
  onDone,
  onError,
}: {
  onDone: (text: string) => Promise<void>;
  onError: (text: string) => void;
}) {
  const [reference, setReference] = useState("");
  const [operation, setOperation] = useState("");
  const [amount, setAmount] = useState("");
  const [local, setLocal] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <Card title="Registrar un pago">
      <form
        className="grid gap-3 sm:grid-cols-2"
        aria-label="Registrar un pago"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          try {
            const payment = await api.admin.billing.recordPayment({
              referencia: reference.trim(),
              operacion: operation.trim(),
              monto_usd: amount.trim(),
              ...(local.trim() ? { monto_local: local.trim(), moneda_local: "PEN" } : {}),
            });
            setReference("");
            setOperation("");
            setAmount("");
            setLocal("");
            await onDone(
              payment.repetido
                ? `Ese pago ya estaba registrado (${payment.comprobante}). No se cobró dos veces.`
                : `Pago registrado: ${payment.concepto}, comprobante ${payment.comprobante}.`,
            );
          } catch (caught) {
            onError(humanMessage(caught));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="Referencia (LEO-...)" value={reference} onChange={(e) => setReference(e.target.value)} />
        <Field
          label="N.° de operación del banco o Yape"
          value={operation}
          onChange={(e) => setOperation(e.target.value)}
        />
        <Field label="Monto en dólares" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <Field
          label="Monto recibido en soles (opcional)"
          inputMode="decimal"
          value={local}
          onChange={(e) => setLocal(e.target.value)}
        />
        <div className="sm:col-span-2">
          <Button type="submit" loading={busy} disabled={!reference.trim() || !operation.trim() || !amount.trim()}>
            Registrar pago
          </Button>
        </div>
      </form>
    </Card>
  );
}

function Coupons({
  coupons,
  onDone,
  onError,
}: {
  coupons: AdminCoupon[];
  onDone: (text: string) => Promise<void>;
  onError: (text: string) => void;
}) {
  const [code, setCode] = useState("");
  const [percent, setPercent] = useState("10");

  return (
    <Card title="Cupones">
      <ul className="mb-3 text-sm text-ink-soft">
        {coupons.map((c) => (
          <li key={c.codigo}>
            <span className="font-mono text-ink">{c.codigo}</span> · {c.porcentaje} %
            {c.usos_maximos ? ` · ${c.usos_maximos} usos` : ""}
            {c.vence ? ` · vence ${new Date(c.vence).toLocaleDateString("es-PE")}` : ""}
          </li>
        ))}
      </ul>
      <form
        className="flex flex-wrap items-end gap-2"
        aria-label="Nuevo cupón"
        onSubmit={async (event) => {
          event.preventDefault();
          try {
            await api.admin.billing.createCoupon({ codigo: code.trim(), porcentaje: Number(percent) });
            setCode("");
            await onDone(`Cupón ${code.trim().toUpperCase()} creado.`);
          } catch (caught) {
            onError(humanMessage(caught));
          }
        }}
      >
        <Field label="Código" value={code} onChange={(e) => setCode(e.target.value)} />
        <Field label="Porcentaje" inputMode="numeric" value={percent} onChange={(e) => setPercent(e.target.value)} />
        <Button type="submit" variant="secondary" disabled={!code.trim()}>
          Crear cupón
        </Button>
      </form>
    </Card>
  );
}
