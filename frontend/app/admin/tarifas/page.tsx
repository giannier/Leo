"use client";

/**
 * Meta's rates (Feature 011, C-107): what Meta charges per template message, by country and
 * category, from a date. The campaigns' estimate reads the latest one in force. Verify them
 * against Meta's published table before the pilot.
 */

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, Field, Select, Skeleton } from "@/components/ui";
import { api, type MetaRate } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

export default function TarifasPage() {
  const [rates, setRates] = useState<MetaRate[] | null>(null);
  const [country, setCountry] = useState("PE");
  const [category, setCategory] = useState<"marketing" | "utility">("marketing");
  const [from, setFrom] = useState(new Date().toISOString().slice(0, 10));
  const [rate, setRate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRates(await api.admin.metaRates());
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      setRates(
        await api.admin.saveMetaRate({
          country: country.toUpperCase(),
          category,
          valid_from: from,
          rate_usd: rate,
        }),
      );
      setRate("");
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Tarifas de Meta</h1>
        <p className="text-sm text-ink-soft">
          Costo por plantilla entregada que se muestra como estimado en las campañas. Lo cobra Meta vía YCloud al negocio.
        </p>
      </header>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Card title="Agregar o corregir una tarifa">
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="País" value={country} maxLength={2} onChange={(e) => setCountry(e.target.value)} />
          <Select
            label="Categoría"
            value={category}
            onChange={(e) => setCategory(e.target.value as "marketing" | "utility")}
            options={[
              { value: "marketing", label: "Marketing" },
              { value: "utility", label: "Utilidad" },
            ]}
          />
          <Field label="Vigente desde" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          <Field label="US$ por mensaje" inputMode="decimal" value={rate} placeholder="0.07030" onChange={(e) => setRate(e.target.value)} />
        </div>
        <div className="mt-4">
          <Button type="button" loading={busy} disabled={!rate} onClick={() => void save()}>
            Guardar tarifa
          </Button>
        </div>
      </Card>
      {!rates ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <table className="w-full text-left text-sm" data-testid="meta-rates">
          <thead className="text-xs text-ink-faint">
            <tr>
              <th className="py-2">País</th>
              <th>Categoría</th>
              <th>Desde</th>
              <th>US$</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rates.map((r) => (
              <tr key={`${r.country}-${r.category}-${r.valid_from}`}>
                <td className="py-2">{r.country}</td>
                <td>{r.category === "marketing" ? "Marketing" : "Utilidad"}</td>
                <td>{r.valid_from}</td>
                <td>{r.rate_usd}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
