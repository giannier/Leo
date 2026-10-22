"use client";

/**
 * The platform operator's shell.
 *
 * Outside the `(panel)` route group, and it has to be: a platform admin
 * belongs to no tenant, so every part of that shell - the trial banner, the
 * business name, the whole navigation - has nothing to render from.
 *
 * It looks deliberately different from the customer panel. Somebody working
 * a queue that spans every business on the platform should never be in doubt
 * about which of the two they are looking at, and a dark bar is cheaper and
 * more reliable than remembering.
 *
 * The guard here is a courtesy. Every one of these endpoints checks
 * `is_platform_admin` against the database on every request, and answers 404
 * rather than 403 so the surface is not discoverable.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import {
  AlertIcon,
  BookIcon,
  ChartIcon,
  ClockIcon,
  LogOutIcon,
  ReceiptIcon,
  UsersIcon,
} from "@/components/icons";
import { Skeleton, cx } from "@/components/ui";
import { useSession } from "@/lib/session";

const NAV = [
  { href: "/admin", label: "Solicitudes", icon: ClockIcon },
  { href: "/admin/tenants", label: "Negocios", icon: UsersIcon },
  { href: "/admin/cobros", label: "Cobros", icon: ReceiptIcon },
  { href: "/admin/metricas", label: "Métricas", icon: ChartIcon },
  { href: "/admin/tarifas", label: "Tarifas Meta", icon: ReceiptIcon },
  { href: "/admin/nps", label: "NPS", icon: ChartIcon },
  // Last, because it is the one nobody wants to have to open.
  { href: "/admin/dead-letters", label: "Sin procesar", icon: AlertIcon },
  { href: "/admin/catalogo", label: "Catálogo", icon: BookIcon },
  { href: "/admin/aprendizaje", label: "Aprendizaje", icon: BookIcon },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { status, user, isPlatformAdmin, signOut } = useSession();

  useEffect(() => {
    if (status === "anonymous") {
      router.replace("/login");
    } else if (status === "authenticated" && !isPlatformAdmin) {
      // A tenant user who typed the URL. Sent to their own panel rather than
      // shown a refusal: from their side this address does not exist.
      router.replace("/");
    }
  }, [status, isPlatformAdmin, router]);

  // `loading` is not `anonymous`. Redirecting here would bounce every
  // returning operator out for a frame before the refresh lands.
  if (status !== "authenticated" || !isPlatformAdmin) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas p-6">
        <div className="w-full max-w-sm space-y-3">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-dvh bg-canvas">
      <a
        href="#contenido"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:font-semibold"
      >
        Saltar al contenido
      </a>

      <header className="bg-brand text-brand-fg">
        <div className="mx-auto flex max-w-[1760px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6 lg:px-8 xl:px-10">
          <div className="flex items-baseline gap-2">
            <span className="font-heading text-lg font-bold tracking-tight">Leo</span>
            <span className="rounded-full bg-brand-fg/15 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide">
              Operación
            </span>
          </div>

          <nav aria-label="Principal" className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
            {NAV.map((item) => {
              const current =
                item.href === "/admin" ? pathname === "/admin" : pathname.startsWith(item.href);
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={current ? "page" : undefined}
                  className={cx(
                    "flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm transition-colors duration-150",
                    current
                      ? "bg-brand-fg/15 font-semibold"
                      : "text-brand-fg/70 hover:bg-brand-fg/10 hover:text-brand-fg",
                  )}
                >
                  <Icon className="size-4" />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="flex items-center gap-3">
            <span className="hidden text-xs text-brand-fg/70 sm:inline">{user?.email}</span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="flex size-11 cursor-pointer items-center justify-center rounded-lg text-brand-fg/70 hover:bg-brand-fg/10 hover:text-brand-fg"
              aria-label="Cerrar sesión"
            >
              <LogOutIcon className="size-5" />
            </button>
          </div>
        </div>
      </header>

      <main id="contenido" className="mx-auto w-full max-w-[1760px] px-4 py-6 sm:px-6 lg:px-8 xl:px-10">
        {children}
      </main>
    </div>
  );
}
