"use client";

/**
 * The template of the business's rubro (feature 020, F3): a starting point the business applies
 * and then owns. Applied, its situations are the business's own (Situaciones), and a newer version
 * of the template arrives as news, item by item: nothing changes behind the business's back.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/toast";
import { Alert, Button, Card, Field, Skeleton, cx } from "@/components/ui";
import { api, type RubroTemplate, type TemplateItem } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

type Values = Record<string, Record<string, string>>;

const DATA_LABELS: Record<string, string> = {
  nombres: "nombres",
  apellidos: "apellidos",
  dni: "DNI",
  ruc: "RUC",
  correo: "correo",
  telefono: "teléfono",
  direccion: "dirección",
  distrito: "distrito",
  referencia: "referencia",
  empresa: "empresa",
  fecha_nacimiento: "fecha de nacimiento",
};

/** What the template suggests for the process, in words: paths, customer data, payment. */
function optionLines(opciones: RubroTemplate["opciones"]): string[] {
  const lines: string[] = [];
  const paths = (
    opciones.calificacion as { caminos?: { titulo: string }[] } | undefined
  )?.caminos;
  if (paths?.length) {
    lines.push(
      `Pregunta primero qué busca: ${paths.map((p) => p.titulo).join(" o ")}.`,
    );
  }
  const data = opciones.datos_requeridos as string[] | undefined;
  if (data?.length) {
    lines.push(
      `Pide al cliente: ${data.map((d) => DATA_LABELS[d] ?? d).join(", ")}.`,
    );
  }
  if (opciones.modalidad_pago === "previo")
    lines.push("Cobra antes de entregar.");
  if (opciones.modalidad_pago === "contra_entrega")
    lines.push("Cobra contra entrega.");
  return lines;
}

function FieldsOf({
  item,
  values,
  onChange,
}: {
  item: TemplateItem;
  values: Record<string, string>;
  onChange: (values: Record<string, string>) => void;
}) {
  const fields = Object.entries(item.campos);
  if (!fields.length) return null;
  return (
    <div className="mt-2 grid gap-3 sm:grid-cols-2">
      {fields.map(([name, label]) => (
        <Field
          key={name}
          label={label}
          value={values[name] ?? ""}
          hint="Si lo dejas vacío, queda como borrador y lo completas después."
          onChange={(e) => onChange({ ...values, [name]: e.target.value })}
        />
      ))}
    </div>
  );
}

export function RubroTemplateCard({
  canConfigure,
  onChanged,
  compact = false,
}: {
  canConfigure: boolean;
  /** One column, the action first: the wizard's narrow column. */
  compact?: boolean;
  /** The rubro's view reloads: applying changes the options and the situations. */
  onChanged: () => void;
}) {
  const toast = useToast();
  const [template, setTemplate] = useState<RubroTemplate | null>(null);
  const [values, setValues] = useState<Values>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTemplate(await api.assistant.rubroTemplate());
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <Alert tone="danger">{error}</Alert>;
  if (!template) return <Skeleton className="h-40 w-full" />;
  if (!template.version) return null;

  const setFor = (clave: string) => (next: Record<string, string>) =>
    setValues((previous) => ({ ...previous, [clave]: next }));

  async function apply() {
    setBusy("aplicar");
    setError(null);
    try {
      const applied = await api.assistant.applyRubroTemplate(values);
      toast.success(
        "Plantilla aplicada",
        `${applied.activas} situaciones activas y ${applied.borradores} en borrador. Ya son tuyas: cámbialas en Situaciones.`,
      );
      await load();
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function accept(clave: string) {
    setBusy(clave);
    setError(null);
    try {
      const taken = await api.assistant.acceptTemplateNews(
        clave,
        values[clave] ?? {},
      );
      toast.success(
        "Novedad tomada",
        taken.estado === "activa"
          ? "Ya la usa tu asistente."
          : "Quedó en borrador: complétala en Situaciones.",
      );
      await load();
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function seen() {
    setBusy("vistas");
    try {
      await api.assistant.templateNewsSeen();
      toast.success(
        "Novedades vistas",
        "Lo que no tomaste queda como lo tienes.",
      );
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  const options = optionLines(template.opciones);

  if (template.aplicada === null) {
    return (
      <div data-testid="rubro-template">
        <Card
          title={`Plantilla de ${template.nombre ?? "tu rubro"}`}
          description="Un punto de partida con lo que suelen necesitar los negocios como el tuyo. Al aplicarla se copia a tu negocio y desde ahí es tuya: la cambias, la apagas o la borras."
        >
          <div
            className={
              compact
                ? "flex flex-col gap-6"
                : "grid items-start gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]"
            }
          >
            <div>
              <p className="text-sm font-semibold text-ink">
                {template.situaciones.length} situaciones
              </p>
              <ul className="mt-2 space-y-3">
                {template.situaciones.map((item) => (
                  <li
                    key={item.clave}
                    className="rounded-lg border border-line p-3 text-sm"
                  >
                    <p className="font-medium text-ink">{item.titulo}</p>
                    <p className="text-ink-soft">{item.que_hace}</p>
                    {canConfigure ? (
                      <FieldsOf
                        item={item}
                        values={values[item.clave] ?? {}}
                        onChange={setFor(item.clave)}
                      />
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
            {/* In the wizard's narrow column, what it does and the button come first. */}
            <div className={cx("space-y-3", compact && "order-first")}>
              {options.length ? (
                <div>
                  <p className="text-sm font-semibold text-ink">
                    Para tu proceso de venta
                  </p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-ink-soft">
                    {options.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs text-ink-faint">
                    Solo se usa en lo que todavía no configuraste: lo que ya
                    elegiste se queda.
                  </p>
                </div>
              ) : null}
              {canConfigure ? (
                <Button
                  type="button"
                  loading={busy === "aplicar"}
                  onClick={() => void apply()}
                >
                  Aplicar plantilla
                </Button>
              ) : (
                <p className="text-sm text-ink-soft">
                  Pídele a quien configura tu negocio que la aplique.
                </p>
              )}
            </div>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div data-testid="rubro-template">
      <Card
        title={`Plantilla de ${template.nombre ?? "tu rubro"}`}
        description={`Aplicaste la versión ${template.aplicada}. Sus situaciones ya son tuyas y están en Situaciones.`}
      >
        {template.novedades.length ? (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-ink">
              Novedades de la versión {template.version}: tómalas una por una o
              déjalas pasar.
            </p>
            <ul className="space-y-3">
              {template.novedades.map((news) => (
                <li
                  key={news.item.clave}
                  className="rounded-lg border border-line p-3 text-sm"
                >
                  <p className="font-medium text-ink">
                    {news.tipo === "nueva" ? "Nueva: " : "Mejorada: "}
                    {news.item.titulo}
                  </p>
                  <p className="text-ink-soft">{news.item.que_hace}</p>
                  {news.tipo === "mejorada" ? (
                    <p className="mt-1 text-xs text-ink-faint">
                      Tomarla reemplaza el texto de tu copia; su estado y sus
                      archivos se quedan.
                    </p>
                  ) : null}
                  {canConfigure ? (
                    <>
                      <FieldsOf
                        item={news.item}
                        values={values[news.item.clave] ?? {}}
                        onChange={setFor(news.item.clave)}
                      />
                      <div className="mt-2">
                        <Button
                          type="button"
                          variant="secondary"
                          loading={busy === news.item.clave}
                          onClick={() => void accept(news.item.clave)}
                        >
                          Tomar
                        </Button>
                      </div>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
            {canConfigure ? (
              <Button
                type="button"
                variant="secondary"
                loading={busy === "vistas"}
                onClick={() => void seen()}
              >
                Marcar como vistas
              </Button>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-ink-soft">
            Estás al día con la plantilla. Revisa o cambia sus situaciones en{" "}
            <Link
              href="/asistente/situaciones"
              className="font-semibold text-accent"
            >
              Situaciones
            </Link>
            .
          </p>
        )}
      </Card>
    </div>
  );
}
