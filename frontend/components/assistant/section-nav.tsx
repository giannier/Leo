"use client";

/**
 * The tabs of "Asistente" (Feature 017): the four layers a business configures,
 * from its own data to its own rules, plus the media they use.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cx } from "@/components/ui";

const TABS = [
  { href: "/asistente", label: "Resumen" },
  { href: "/asistente/informacion", label: "Información del negocio" },
  { href: "/asistente/comportamiento", label: "Comportamiento" },
  { href: "/asistente/proceso", label: "Proceso y rubro" },
  { href: "/asistente/situaciones", label: "Situaciones" },
  { href: "/asistente/multimedia", label: "Multimedia" },
] as const;

export function AssistantNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Secciones del asistente" className="-mx-1 overflow-x-auto">
      <ul className="flex min-w-max gap-1 border-b border-line px-1">
        {TABS.map((tab) => {
          const current = tab.href === "/asistente" ? pathname === tab.href : pathname.startsWith(tab.href);
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={current ? "page" : undefined}
                className={cx(
                  "inline-flex min-h-11 items-center border-b-2 px-3 text-sm font-medium",
                  current
                    ? "border-accent text-ink"
                    : "border-transparent text-ink-soft hover:text-ink",
                )}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function AssistantHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="space-y-4">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">{title}</h1>
        <p className="mt-1 text-sm text-ink-soft">{description}</p>
      </header>
      <AssistantNav />
    </div>
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
