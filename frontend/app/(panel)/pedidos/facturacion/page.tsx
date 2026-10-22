"use client";

/**
 * Facturación electrónica (feature 019, phase G): the business's own account with its provider.
 *
 * Leo does not issue receipts by itself: a person does, from an order, with a click, through the account
 * the business has with an authorised provider (Nubefact). The route and the token are typed here and
 * never again shown; the series and the next numbers are the business's, because the receipts of a
 * series are consecutive. Off until the account is connected.
 */

import { useCallback, useEffect, useState } from "react";

import { OrdersNav } from "@/components/orders/orders-nav";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Checkbox, Field, Select, Skeleton } from "@/components/ui";
import { api, type Invoicing } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

export default function InvoicingPage() {
  const toast = useToast();
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);
  const [settings, setSettings] = useState<Invoicing | null>(null);
  const [route, setRoute] = useState("");
  const [token, setToken] = useState("");
  const [serieB, setSerieB] = useState("B001");
  const [serieF, setSerieF] = useState("F001");
  const [nextB, setNextB] = useState("1");
  const [nextF, setNextF] = useState("1");
  const [tax, setTax] = useState<"gravado" | "exonerado" | "inafecto">("gravado");
  const [igv, setIgv] = useState("18");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const current = await api.aftersale.invoicing();
      setSettings(current);
      setRoute(current.ruta ?? "");
      setSerieB(current.serie_boleta);
      setSerieF(current.serie_factura);
      setNextB(String(current.siguiente_boleta));
      setNextF(String(current.siguiente_factura));
      setTax(current.tratamiento_igv as typeof tax);
      setIgv(String(Number(current.igv_pct)));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(extra: { activo?: boolean } = {}) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.aftersale.saveInvoicing({
        ruta: route.trim() || undefined,
        ...(token.trim() ? { token: token.trim() } : {}),
        serie_boleta: serieB.trim().toUpperCase(),
        serie_factura: serieF.trim().toUpperCase(),
        siguiente_boleta: Number(nextB),
        siguiente_factura: Number(nextF),
        tratamiento_igv: tax,
        igv_pct: tax === "gravado" ? igv : "0",
        ...extra,
      });
      setSettings(updated);
      setToken("");
      toast.success("Guardado", "Facturación electrónica");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (!canConfigure) {
    return <Alert tone="info">Solo el dueño y los administradores configuran la facturación electrónica.</Alert>;
  }
  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Facturación electrónica</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Emite boletas y facturas de tus pedidos desde Leo, con tu cuenta de Nubefact. Tú pulsas «Emitir»: el asistente
          nunca emite un comprobante.
        </p>
      </header>
      <OrdersNav />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {!settings ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <div className="space-y-6">
          {settings.es_demo ? (
            <Alert tone="warning" title="Entorno de prueba">
              La ruta es la de pruebas de Nubefact: los comprobantes que emitas no son reales. Cuando pases a producción,
              pega la ruta y el token reales.
            </Alert>
          ) : null}
          {/* The account and the series on the left; the tax and the switch on the right. */}
          <div className="grid items-start gap-6 xl:grid-cols-2">
            <div className="space-y-6">
              <Card title="Tu cuenta" description="Los dos datos salen de tu cuenta de Nubefact (menú API). Escríbelos aquí, nunca por el chat.">
                <div className="space-y-4" data-testid="invoicing-account">
                  <Field
                    label="Ruta"
                    value={route}
                    placeholder="https://api.nubefact.com/api/v1/…"
                    hint="Una dirección https de Nubefact."
                    onChange={(e) => setRoute(e.target.value)}
                  />
                  <Field
                    label="Token"
                    type="password"
                    autoComplete="off"
                    value={token}
                    placeholder={settings.tiene_token ? "•••••••• (guardado)" : ""}
                    hint={settings.tiene_token ? "Déjalo vacío para conservar el guardado." : "Se guarda cifrado y no se vuelve a mostrar."}
                    onChange={(e) => setToken(e.target.value)}
                  />
                </div>
              </Card>
              <Card title="Series y números" description="Las series las registraste en SUNAT y en tu cuenta. Los números de una serie son seguidos: pon el siguiente que te toca.">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Serie de boletas" value={serieB} maxLength={4} hint="Empieza con B, como B001." onChange={(e) => setSerieB(e.target.value)} />
                  <Field label="Siguiente boleta" inputMode="numeric" value={nextB} onChange={(e) => setNextB(e.target.value)} />
                  <Field label="Serie de facturas" value={serieF} maxLength={4} hint="Empieza con F, como F001." onChange={(e) => setSerieF(e.target.value)} />
                  <Field label="Siguiente factura" inputMode="numeric" value={nextF} onChange={(e) => setNextF(e.target.value)} />
                </div>
              </Card>
            </div>
            <div className="space-y-6">
              <Card title="Impuesto" description="Tus precios incluyen el impuesto. Leo calcula la base y el IGV de cada comprobante.">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Select
                    label="Tus ventas son"
                    value={tax}
                    onChange={(e) => setTax(e.target.value as typeof tax)}
                    options={[
                      { value: "gravado", label: "Gravadas con IGV" },
                      { value: "exonerado", label: "Exoneradas" },
                      { value: "inafecto", label: "Inafectas" },
                    ]}
                  />
                  {tax === "gravado" ? (
                    <Field label="IGV (%)" inputMode="decimal" value={igv} onChange={(e) => setIgv(e.target.value)} />
                  ) : null}
                </div>
                <p className="mt-3 text-xs text-ink-faint">
                  Por ahora solo se emiten comprobantes en soles. Revisa los montos del primero con tu contador.
                </p>
              </Card>
              <Card>
                <div className="space-y-4">
                  <Checkbox
                    label="Facturación electrónica encendida"
                    hint={settings.listo ? "Apagada, el botón de emitir no aparece en los pedidos." : "Guarda primero la ruta y el token."}
                    checked={settings.activo}
                    disabled={busy || !settings.listo}
                    onChange={(e) => void save({ activo: e.target.checked })}
                  />
                  <Button type="button" loading={busy} onClick={() => void save()}>
                    Guardar
                  </Button>
                </div>
              </Card>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
