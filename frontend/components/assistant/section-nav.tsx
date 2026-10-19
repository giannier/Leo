"use client";

/**
 * "Asistente" in one place (Feature 017, made a side menu in Feature 018 HU-04).
 *
 * Everything the assistant knows and does, as one menu: the business's data, how
 * it talks, the sales process, the products, its own rules, the files and the
 * knowledge. Each entry shows its state (complete, half, pending) from the
 * overview. On a computer it is a column on the left; on a phone, tabs that
 * scroll. Productos and Conocimiento live at their own addresses and show the
 * same menu, so the business never feels it left the assistant.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { cx } from "@/components/ui";
import { api } from "@/lib/api/client";

const SECTIONS = [
  { href: "/asistente", label: "Resumen", detail: "Qué falta para activarlo", clave: null },
  {
    href: "/asistente/informacion",
    label: "Información del negocio",
    detail: "Pagos, locales, entregas y políticas",
    clave: "informacion_negocio",
  },
  {
    href: "/asistente/comportamiento",
    label: "Comportamiento",
    detail: "Tono, objetivo y derivación",
    clave: "comportamiento",
  },
  {
    href: "/asistente/proceso",
    label: "Proceso y rubro",
    detail: "Cómo vendes, paso a paso",
    clave: "proceso_venta",
  },
  { href: "/catalog", label: "Productos", detail: "Catálogo, fotos y etiquetas", clave: "productos" },
  {
    href: "/asistente/situaciones",
    label: "Situaciones",
    detail: "Tus propias reglas",
    clave: "situaciones",
  },
  {
    href: "/asistente/multimedia",
    label: "Multimedia",
    detail: "Imágenes y documentos",
    clave: "multimedia",
  },
  { href: "/knowledge", label: "Conocimiento", detail: "Documentos y tu web", clave: "conocimiento" },
] as const;

const DOT: Record<string, { tone: string; text: string }> = {
  completa: { tone: "bg-success", text: "completa" },
  parcial: { tone: "bg-warning", text: "a medias" },
  vacia: { tone: "bg-line-strong", text: "pendiente" },
  opcional: { tone: "bg-line", text: "opcional" },
};

function isCurrent(href: string, pathname: string): boolean {
  return href === "/asistente" ? pathname === href : pathname.startsWith(href);
}

/** The state of each section, read again on every page change (a save may have moved it). */
function useSectionStates(pathname: string): Record<string, string> {
  const [states, setStates] = useState<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    api.assistant
      .overview()
      .then((overview) => {
        if (alive) setStates(Object.fromEntries(overview.secciones.map((s) => [s.clave, s.estado])));
      })
      .catch(() => {
        // The menu works without the dots.
      });
    return () => {
      alive = false;
    };
  }, [pathname]);
  return states;
}

export function AssistantShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const states = useSectionStates(pathname);

  return (
    <div className="lg:grid lg:grid-cols-[14rem_minmax(0,1fr)] lg:gap-6">
      <nav aria-label="Secciones del asistente" className="mb-5 lg:mb-0">
        <p className="mb-2 hidden text-xs font-semibold uppercase tracking-wide text-ink-faint lg:block">
          Tu asistente
        </p>
        <ul className="-mx-1 flex gap-1 overflow-x-auto border-b border-line px-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:border-0 lg:px-0">
          {SECTIONS.map((section) => {
            const current = isCurrent(section.href, pathname);
            const state = section.clave ? DOT[states[section.clave] ?? ""] : undefined;
            return (
              <li key={section.href} className="shrink-0">
                <Link
                  href={section.href}
                  aria-current={current ? "page" : undefined}
                  className={cx(
                    "flex min-h-11 items-center gap-2 px-3 text-sm",
                    "border-b-2 lg:rounded-lg lg:border-b-0 lg:py-2",
                    current
                      ? "border-accent font-medium text-ink lg:bg-accent/10"
                      : "border-transparent text-ink-soft hover:text-ink lg:hover:bg-surface-muted",
                  )}
                >
                  {state ? (
                    <span
                      className={cx("size-2 shrink-0 rounded-full", state.tone)}
                      aria-label={`Sección ${state.text}`}
                      role="img"
                    />
                  ) : (
                    <span className="hidden size-2 shrink-0 lg:block" aria-hidden="true" />
                  )}
                  <span className="lg:flex lg:flex-col">
                    <span className="whitespace-nowrap">{section.label}</span>
                    <span className="hidden text-xs font-normal text-ink-faint lg:block">
                      {section.detail}
                    </span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function AssistantHeader({ title, description }: { title: string; description: string }) {
  return (
    <header>
      <h1 className="font-heading text-2xl font-semibold text-ink">{title}</h1>
      <p className="mt-1 text-sm text-ink-soft">{description}</p>
    </header>
  );
}

/** "Guardado" next to a save button, for a moment. */
export function Saved({ show }: { show: boolean }) {
  return show ? (
    <span role="status" className="text-sm text-success">
      Guardado
    </span>
  ) : null;
}
