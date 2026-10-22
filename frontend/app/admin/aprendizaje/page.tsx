"use client";

/**
 * Platform · Aprendizaje (feature 017, phase 4).
 *
 * Situations that at least two businesses of the same rubro wrote with the same meaning, and that those
 * businesses allowed Leo to see. Only the text of the rule ever shows here, never a conversation or a
 * customer. Adopting one writes it to the rubro's open draft as a suggested situation; it still goes
 * through the evals before it is published, like any other change.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/toast";
import { Alert, Button, Card, Select, Skeleton } from "@/components/ui";
import { api, type CatalogList, type LearnedCandidate } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

export default function LearnedPage() {
  const toast = useToast();
  const [rubros, setRubros] = useState<{ key: string; nombre: string }[]>([]);
  const [rubro, setRubro] = useState("");
  const [items, setItems] = useState<LearnedCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.catalog
      .list()
      .then((list: CatalogList) => {
        const found = list.items.filter((e) => e.kind === "rubro").map((e) => ({ key: e.key, nombre: e.nombre }));
        setRubros(found);
        setRubro((current) => current || found[0]?.key || "");
      })
      .catch((caught) => setError(humanMessage(caught)));
  }, []);

  const load = useCallback(async () => {
    if (!rubro) return;
    setItems(null);
    try {
      setItems((await api.catalog.learnedCandidates(rubro)).items);
      setError(null);
    } catch (caught) {
      setError(humanMessage(caught));
      setItems([]);
    }
  }, [rubro]);

  useEffect(() => {
    void load();
  }, [load]);

  async function adopt(candidate: LearnedCandidate) {
    setBusy(candidate.titulo);
    setError(null);
    try {
      const result = await api.catalog.adoptLearned({
        rubro,
        titulo: candidate.titulo,
        cuando_aplica: candidate.cuando_aplica,
        que_hace: candidate.que_hace,
      });
      toast.success("Agregada al borrador", `Versión ${result.version} del rubro`);
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Lo que aprenden los rubros</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Situaciones que varios negocios del mismo rubro escribieron igual y que dejaron compartir. Solo el texto de la regla.
          Al ofrecer una, queda en el borrador del rubro; antes de publicarla pasa por los evals en{" "}
          <Link href="/admin/catalogo" className="font-semibold text-accent underline">
            Catálogo
          </Link>
          .
        </p>
      </header>
      <div className="w-64">
        <Select
          label="Rubro"
          value={rubro}
          onChange={(e) => setRubro(e.target.value)}
          options={rubros.map((r) => ({ value: r.key, label: r.nombre }))}
        />
      </div>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {items === null ? <Skeleton className="h-32 w-full" /> : null}
      {items && items.length === 0 ? (
        <p className="text-sm text-ink-soft">
          Todavía no hay situaciones parecidas en dos negocios que hayan permitido compartirlas.
        </p>
      ) : null}
      {items && items.length > 0 ? (
        <ul className="space-y-3">
          {items.map((candidate) => (
            <li key={`${candidate.titulo}-${candidate.cuando_aplica}`}>
              <Card>
                <div className="space-y-2 text-sm" data-testid="learned-candidate">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-ink">{candidate.titulo}</span>
                    <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-semibold text-accent">
                      {candidate.negocios} negocios
                    </span>
                    {candidate.con_datos ? (
                      <span className="rounded-full bg-danger-soft px-2 py-0.5 text-xs font-semibold text-danger">
                        Parece traer datos de una persona
                      </span>
                    ) : null}
                  </div>
                  <p className="text-ink-soft">
                    <span className="font-medium text-ink">Cuándo:</span> {candidate.cuando_aplica}
                  </p>
                  <p className="text-ink-soft">
                    <span className="font-medium text-ink">Qué hace:</span> {candidate.que_hace}
                  </p>
                  {candidate.variantes.length > 0 ? (
                    <details>
                      <summary className="cursor-pointer text-xs text-ink-faint">Cómo la escribieron otros</summary>
                      <ul className="mt-1 list-disc pl-5 text-ink-soft">
                        {candidate.variantes.map((variant, index) => (
                          <li key={index}>{variant}</li>
                        ))}
                      </ul>
                    </details>
                  ) : null}
                  <Button
                    type="button"
                    variant="secondary"
                    loading={busy === candidate.titulo}
                    disabled={candidate.con_datos}
                    onClick={() => void adopt(candidate)}
                  >
                    Ofrecer al rubro
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
