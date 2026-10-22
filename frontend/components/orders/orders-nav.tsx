"use client";

/**
 * The tabs of "Pedidos": the list, the comanda, the returns and the invoicing.
 *
 * They are four views of the same work (what customers bought and what the business does
 * about it), so they share one row of tabs instead of a different set of links on each page.
 * Invoicing is the owner's, like the rest of the configuration.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { cx } from "@/components/ui";
import { api } from "@/lib/api/client";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

const TABS = [
  { href: "/pedidos", label: "Pedidos", managers: false },
  { href: "/pedidos/comanda", label: "Comanda", managers: false },
  { href: "/pedidos/devoluciones", label: "Devoluciones", managers: false },
  { href: "/pedidos/facturacion", label: "Facturación electrónica", managers: true },
] as const;

export function OrdersNav() {
  const pathname = usePathname();
  const canConfigure = useHasRole(...MANAGES_CONFIGURATION);
  // Requests to return or exchange that wait for a person: the badge of their tab.
  const [waitingReturns, setWaitingReturns] = useState(0);

  useEffect(() => {
    api.aftersale
      .returns({ estado: ["solicitada"] })
      .then((list) => setWaitingReturns(list.pendientes))
      .catch(() => setWaitingReturns(0));
  }, [pathname]);

  return (
    <nav aria-label="Secciones de pedidos" className="-mx-1 overflow-x-auto border-b border-line px-1">
      <ul className="flex min-w-max gap-1">
        {TABS.filter((tab) => !tab.managers || canConfigure).map((tab) => {
          const current = tab.href === "/pedidos" ? pathname === tab.href : pathname.startsWith(tab.href);
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={current ? "page" : undefined}
                className={cx(
                  "-mb-px flex min-h-11 items-center gap-2 border-b-2 px-3.5 text-sm",
                  current
                    ? "border-accent font-semibold text-ink"
                    : "border-transparent text-ink-soft hover:border-line-strong hover:text-ink",
                )}
              >
                {tab.label}
                {tab.href === "/pedidos/devoluciones" && waitingReturns > 0 ? (
                  <span className="rounded-full bg-warning-soft px-2 py-0.5 text-xs font-semibold text-warning">
                    {waitingReturns} por revisar
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
