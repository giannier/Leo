"use client";

/**
 * Asistente · Información del negocio (TASK-438, 017 RF-010).
 *
 * The business's own layer, by forms: nothing here is free text the assistant
 * has to read as a rule. One card per block; each saves on its own.
 */

import { useEffect, useState } from "react";

import { BlockContext } from "@/components/assistant/block-context";
import { Faqs, PaymentMethods, Promotions } from "@/components/assistant/info-lists";
import {
  ContactForm,
  CustomerTypesForm,
  DeliveryForm,
  DiscountPolicyForm,
  LocationsForm,
  ReturnsForm,
} from "@/components/assistant/info-sections";
import { AssistantHeader } from "@/components/assistant/section-nav";
import { CheckIcon } from "@/components/icons";
import { cx } from "@/components/ui";

const BLOCKS = [
  { key: "pagos", title: "Métodos de pago", body: <PaymentMethods /> },
  { key: "entregas", title: "Entregas", body: <DeliveryForm /> },
  { key: "locales", title: "Locales y horarios", body: <LocationsForm /> },
  { key: "faqs", title: "Preguntas frecuentes", body: null },
  { key: "promociones", title: "Promociones", body: <Promotions /> },
  { key: "descuentos", title: "Política de descuentos", body: <DiscountPolicyForm /> },
  { key: "tipos", title: "Tipos de cliente", body: <CustomerTypesForm /> },
  { key: "devoluciones", title: "Cambios y devoluciones", body: <ReturnsForm /> },
  { key: "contacto", title: "Contacto", body: <ContactForm /> },
] as const;

export default function BusinessInfoPage() {
  const [open, setOpen] = useState<string>("pagos");
  // Blocks saved in this visit: closed, with a green mark that stays in the header.
  const [done, setDone] = useState<ReadonlySet<string>>(new Set());
  const [question, setQuestion] = useState("");

  // From the simulator: "Agregar una pregunta frecuente" (Feature 018, HU-06).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const block = params.get("bloque");
    if (block && BLOCKS.some((b) => b.key === block)) setOpen(block);
    setQuestion(params.get("pregunta")?.trim() ?? "");
  }, []);
  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Información del negocio"
        description="Lo que tu asistente sabe de tu negocio. Cada dato sale de aquí: no inventa precios, zonas ni horarios."
      />
      <div className="space-y-3">
        {BLOCKS.map((block) => {
          const expanded = open === block.key;
          const justSaved = done.has(block.key) && !expanded;
          return (
            <section key={block.key} className="rounded-xl border border-line bg-surface">
              <h2>
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => {
                    setOpen(expanded ? "" : block.key);
                    // Opening it again is editing again: the mark no longer holds.
                    if (!expanded) setDone((d) => new Set([...d].filter((k) => k !== block.key)));
                  }}
                  className={cx(
                    "flex min-h-12 w-full cursor-pointer items-center justify-between px-5 text-left",
                    "text-base font-semibold text-ink",
                  )}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    {block.title}
                    {justSaved ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-success-soft px-2.5 py-0.5 text-xs font-semibold text-success">
                        <CheckIcon className="size-3.5" />
                        Guardado
                      </span>
                    ) : null}
                  </span>
                  <span aria-hidden="true" className="text-ink-faint">
                    {expanded ? "−" : "+"}
                  </span>
                </button>
              </h2>
              {expanded ? (
                <div className="border-t border-line p-5">
                  <BlockContext.Provider
                    value={{
                      saved: () => {
                        setOpen("");
                        setDone((d) => new Set(d).add(block.key));
                      },
                    }}
                  >
                    {block.key === "faqs" ? <Faqs key={question} seedQuestion={question} /> : block.body}
                  </BlockContext.Provider>
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
    </div>
  );
}
