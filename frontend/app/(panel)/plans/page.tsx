"use client";

/**
 * Mi plan (Feature 009, TASK-908).
 *
 * Where the owner sees what they pay, what they owe and what they used, and
 * chooses or changes a plan. v1 is paid by hand (Giannier, 2026-10-14): choosing
 * a plan creates a charge with a reference; the owner pays by transfer or Yape
 * quoting it, and the plan starts when the platform records the payment. The
 * page says exactly that, next to the amount and the instructions.
 *
 * Owner and admins read it; only the owner buys, changes or cancels (the API
 * enforces it; the buttons just do not offer what would be refused).
 */

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, Field, Skeleton, cx } from "@/components/ui";
import {
  api,
  type BillingCatalog,
  type BillingCharge,
  type BillingCycle,
  type BillingOverview,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { useSession } from "@/lib/session";

type Plan = BillingCatalog["planes"][number];

const STATUS_TEXT: Record<string, string> = {
  active: "Activo",
  past_due: "Pago pendiente (en gracia)",
  paused: "En pausa por falta de pago",
  canceled: "Cancelado",
  trialing: "En prueba",
};

function usd(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "—" : `US$ ${Number(value).toFixed(2)}`;
}

function day(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleDateString("es-PE", { day: "numeric", month: "long", year: "numeric" }) : "";
}

function soles(value: string | null | undefined, rate: string | null | undefined): string {
  if (!value || !rate) return "";
  return ` (≈ S/ ${(Number(value) * Number(rate)).toFixed(2)})`;
}

export default function PlansPage() {
  const { role } = useSession();
  const isOwner = role === "owner";
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null);
  const [overview, setOverview] = useState<BillingOverview | null>(null);
  const [cycle, setCycle] = useState<BillingCycle>("monthly");
  const [coupon, setCoupon] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "danger" | "info"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [plans, state] = await Promise.all([api.billing.plans(), api.billing.overview()]);
      setCatalog(plans);
      setOverview(state);
    } catch (caught) {
      setNotice({ tone: "danger", text: humanMessage(caught) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(key: string, run: () => Promise<string | null>) {
    setBusy(key);
    setNotice(null);
    try {
      const text = await run();
      if (text) setNotice({ tone: "success", text });
      await load();
    } catch (caught) {
      setNotice({ tone: "danger", text: humanMessage(caught) });
    } finally {
      setBusy(null);
    }
  }

  if (!overview || !catalog) {
    return notice ? <Alert tone="danger">{notice.text}</Alert> : <Skeleton className="h-64 w-full" />;
  }

  const subscription = overview.suscripcion;
  const live = subscription && ["active", "past_due"].includes(subscription.estado);
  const percent = overview.consumo.limite ? Math.min(100, (100 * overview.consumo.usados) / overview.consumo.limite) : 0;

  function choose(plan: Plan) {
    if (live) {
      return act(`plan-${plan.id}`, async () => {
        const result = await api.billing.changePlan({ plan_id: plan.id, ciclo: cycle });
        return result.cobro
          ? `Listo: paga ${usd(result.cobro.monto)} con la referencia ${result.cobro.referencia} y el cambio se aplica al registrarse el pago.`
          : `El cambio a ${plan.nombre} se aplicará el ${day(subscription?.hasta)}.`;
      });
    }
    return act(`plan-${plan.id}`, async () => {
      const charge = await api.billing.checkout({ plan_id: plan.id, ciclo: cycle, cupon: coupon.trim() || undefined });
      return `Listo: paga ${usd(charge.monto)} con la referencia ${charge.referencia}. Tu plan empieza cuando registremos el pago.`;
    });
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Mi plan</h1>
        <p className="text-sm text-ink-soft">Lo que pagas, lo que debes y lo que usaste en este período.</p>
      </header>

      {!overview.cobro_activo ? (
        <Alert tone="info" title="Modo pruebas">
          Por ahora Leo no cobra: nada vence ni se pausa. Puedes ver los planes y probar el proceso.
        </Alert>
      ) : null}
      {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}

      <Card title="Estado">
        <div className="space-y-3 text-sm" data-testid="billing-status">
          {subscription ? (
            <>
              <p>
                <span className="font-semibold text-ink">
                  {subscription.plan_nombre} {subscription.ciclo === "annual" ? "anual" : "mensual"}
                </span>{" "}
                · {STATUS_TEXT[subscription.estado] ?? subscription.estado} · {usd(subscription.precio)}
              </p>
              <p className="text-ink-soft">
                Período del {day(subscription.desde)} al {day(subscription.hasta)}.
                {subscription.gracia_hasta ? ` Tu agente sigue respondiendo hasta el ${day(subscription.gracia_hasta)}.` : ""}
                {subscription.cambio_programado ? ` Cambia a ${subscription.cambio_programado} al terminar el período.` : ""}
                {subscription.cancela_al_final ? " Se cancela al terminar el período." : ""}
              </p>
              {isOwner && live ? (
                subscription.cancela_al_final ? (
                  <Button type="button" variant="secondary" loading={busy === "resume"} onClick={() => void act("resume", async () => { await api.billing.resume(); return "Tu plan sigue."; })}>
                    No cancelar
                  </Button>
                ) : (
                  <Button
                    type="button"
                    variant="ghost"
                    loading={busy === "cancel"}
                    onClick={() => {
                      if (window.confirm("Tu plan seguirá hasta el final del período pagado y luego tu agente se pausará. ¿Cancelar?")) {
                        void act("cancel", async () => { await api.billing.cancel(); return "Tu plan se cancelará al terminar el período."; });
                      }
                    }}
                  >
                    Cancelar mi plan
                  </Button>
                )
              ) : null}
            </>
          ) : (
            <p>
              {overview.estado_negocio === "trial"
                ? `Estás en la prueba gratis${overview.prueba_hasta ? ` hasta el ${day(overview.prueba_hasta)}` : ""}. Si contratas antes, los días que te quedan se suman a tu primer mes.`
                : "No tienes un plan vigente."}
            </p>
          )}
          <div>
            <p className="text-xs text-ink-soft" data-testid="billing-usage">
              Mensajes del asistente: {overview.consumo.usados.toLocaleString("es-PE")} de{" "}
              {overview.consumo.limite.toLocaleString("es-PE")}
            </p>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-line">
              <div className={cx("h-full rounded-full", percent >= 80 ? "bg-warning" : "bg-accent")} style={{ width: `${percent}%` }} />
            </div>
          </div>
        </div>
      </Card>

      {overview.pendientes.length ? (
        <section className="space-y-3" aria-label="Por pagar">
          <h2 className="font-semibold text-ink">Por pagar</h2>
          {overview.pendientes.map((charge) => (
            <PendingCharge key={charge.id} charge={charge} rate={catalog.soles_por_dolar} canCancel={isOwner} busy={busy === charge.id} onCancel={() => act(charge.id, async () => { await api.billing.cancelCharge(charge.id); return "Anulamos ese cobro."; })} />
          ))}
        </section>
      ) : null}

      <section className="space-y-3" aria-label="Planes">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold text-ink">Planes</h2>
          <div className="flex gap-1" role="group" aria-label="Período">
            {(["monthly", "annual"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={cycle === value}
                onClick={() => setCycle(value)}
                className={cx("min-h-9 rounded-full border px-3 text-xs font-medium", cycle === value ? "border-brand bg-brand text-brand-fg" : "border-line bg-surface text-ink-soft")}
              >
                {value === "monthly" ? "Mensual" : "Anual (−20 %)"}
              </button>
            ))}
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {catalog.planes.map((plan) => {
            const price = cycle === "monthly" ? plan.precio_mensual : plan.precio_anual;
            const current = subscription?.plan === plan.id && subscription.ciclo === cycle && live;
            return (
              <div key={plan.id} className={cx("space-y-3 rounded-xl border bg-surface p-5", current ? "border-brand" : "border-line")} data-testid={`plan-${plan.id}`}>
                <h3 className="font-heading text-lg font-semibold text-ink">{plan.nombre}</h3>
                <p className="text-2xl font-semibold text-ink">
                  {usd(price)}
                  <span className="text-sm font-normal text-ink-soft"> / {cycle === "monthly" ? "mes" : "año"}</span>
                </p>
                {catalog.soles_por_dolar ? <p className="text-xs text-ink-faint">Referencia{soles(price, catalog.soles_por_dolar)}</p> : null}
                <ul className="space-y-1 text-sm text-ink-soft">
                  <li>{plan.mensajes.toLocaleString("es-PE")} mensajes del asistente al mes</li>
                  <li>{plan.asientos} personas en el equipo</li>
                  <li>{(plan.caracteres_conocimiento / 1_000_000).toLocaleString("es-PE")} M caracteres de conocimiento</li>
                </ul>
                {current ? (
                  <p className="text-sm font-semibold text-brand">Tu plan</p>
                ) : isOwner ? (
                  <Button type="button" loading={busy === `plan-${plan.id}`} onClick={() => void choose(plan)}>
                    {live ? "Cambiar a este plan" : "Elegir"}
                  </Button>
                ) : null}
              </div>
            );
          })}
        </div>
        {isOwner && !live ? (
          <div className="max-w-xs">
            <Field label="Cupón (opcional)" value={coupon} onChange={(e) => setCoupon(e.target.value)} />
          </div>
        ) : null}
        <p className="text-xs text-ink-faint">
          Precios finales en dólares, impuestos incluidos. Subir de plan aplica lo que no usaste del actual; bajar se aplica al terminar el período.
        </p>
      </section>

      {live && catalog.paquetes.length ? (
        <section className="space-y-3" aria-label="Paquetes">
          <h2 className="font-semibold text-ink">¿Te faltan mensajes este período?</h2>
          {catalog.paquetes.map((pack) => (
            <div key={pack.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface p-4">
              <p className="text-sm text-ink">
                {pack.nombre}: {usd(pack.precio)}
                {soles(pack.precio, catalog.soles_por_dolar)}
              </p>
              {isOwner ? (
                <Button type="button" variant="secondary" loading={busy === pack.id} onClick={() => void act(pack.id, async () => { const charge = await api.billing.addon(pack.id); return `Paga ${usd(charge.monto)} con la referencia ${charge.referencia}.`; })}>
                  Comprar
                </Button>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}

      {overview.pagos.length ? (
        <section className="space-y-3" aria-label="Pagos">
          <h2 className="font-semibold text-ink">Pagos</h2>
          <ul className="divide-y divide-line rounded-xl border border-line bg-surface" data-testid="billing-payments">
            {overview.pagos.map((payment) => (
              <li key={payment.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
                <span>
                  {day(payment.en)} · {payment.concepto} · {usd(payment.monto)}
                  {payment.estado === "refunded" ? " · reembolsado" : ""}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={async () => {
                    try {
                      const blob = await api.billing.receipt(payment.id);
                      const url = URL.createObjectURL(blob);
                      const link = document.createElement("a");
                      link.href = url;
                      link.download = `${payment.comprobante}.pdf`;
                      link.click();
                      URL.revokeObjectURL(url);
                    } catch (caught) {
                      setNotice({ tone: "danger", text: humanMessage(caught) });
                    }
                  }}
                >
                  Comprobante {payment.comprobante}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function PendingCharge({
  charge,
  rate,
  canCancel,
  busy,
  onCancel,
}: {
  charge: BillingCharge;
  rate: string | null | undefined;
  canCancel: boolean;
  busy: boolean;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-2 rounded-xl border border-warning bg-surface p-4" data-testid="pending-charge">
      <p className="font-semibold text-ink">
        {charge.concepto}: {usd(charge.monto)}
        {soles(charge.monto, rate)}
      </p>
      {Number(charge.descuento) > 0 || Number(charge.credito) > 0 ? (
        <p className="text-xs text-ink-soft">
          Precio {usd(charge.lista)}
          {Number(charge.descuento) > 0 ? ` − cupón ${charge.cupon ?? ""} ${usd(charge.descuento)}` : ""}
          {Number(charge.credito) > 0 ? ` − lo que no usaste de tu plan ${usd(charge.credito)}` : ""}
        </p>
      ) : null}
      <p className="text-sm">
        Escribe esta referencia al pagar:{" "}
        <span className="rounded bg-surface-muted px-2 py-0.5 font-mono text-base font-semibold text-ink" data-testid="charge-reference">
          {charge.referencia}
        </span>
      </p>
      <p className="text-sm text-ink-soft">
        {charge.instrucciones || "Escríbenos y te enviamos los datos para pagar por transferencia o Yape."}
      </p>
      {charge.vence ? <p className="text-xs text-ink-faint">Vence el {day(charge.vence)}.</p> : null}
      {canCancel ? (
        <Button type="button" variant="ghost" loading={busy} onClick={onCancel}>
          Anular este cobro
        </Button>
      ) : null}
    </div>
  );
}
