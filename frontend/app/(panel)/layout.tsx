"use client";

/**
 * The signed-in shell: guard, navigation, trial banner.
 *
 * The guard is a **courtesy**, not a security boundary. It stops somebody
 * being shown a screen that would 403 anyway. Every real decision is made by
 * the backend on every request, from the database - see `require_roles` in
 * `app/api/deps.py`. Anyone who edits this file believing it protects data is
 * about to make a bad assumption.
 *
 * Layout follows the platform's own convention rather than inventing one: a
 * sidebar from `lg` up, where there is room for it, and a bottom bar on a
 * phone, where a thumb can reach it and a sidebar cannot be opened one-handed.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { TrialBanner } from "@/components/trial-banner";
import {
  BookIcon,
  BoxIcon,
  CalendarIcon,
  ChartIcon,
  ChatIcon,
  HomeIcon,
  InboxIcon,
  KanbanIcon,
  MegaphoneIcon,
  LogOutIcon,
  PhoneIcon,
  ReceiptIcon,
  SparkIcon,
  UsersIcon,
} from "@/components/icons";
import { Alert, Skeleton, cx } from "@/components/ui";
import { api, type TenantSummary } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";
import { MANAGES_CONFIGURATION, useSession, type Role } from "@/lib/session";

/** The headings of the side menu: a long flat list is read item by item; three groups are scanned. */
type NavGroup = "work" | "agent" | "business";

const GROUP_LABELS: Record<NavGroup, string | null> = {
  work: null,
  agent: "Tu agente",
  business: "Tu negocio",
};

interface NavItem {
  href: string;
  label: string;
  icon: typeof HomeIcon;
  /** Where it sits in the side menu; the first group has no heading. */
  group: NavGroup;
  /** Omitted means every role sees it. */
  roles?: Role[];
  /** Shown in the bottom bar on phones. Material caps that at five. */
  primary?: boolean;
}

const NAV: NavItem[] = [
  { href: "/", group: "work", label: "Inicio", icon: HomeIcon, primary: true },
  // Every role: confirming a payment is exactly the work an operator is here for.
  { href: "/pedidos", group: "work", label: "Pedidos", icon: ReceiptIcon, primary: true },
  // Every role: answering customers is the operator's job (Feature 007).
  { href: "/bandeja", group: "work", label: "Bandeja", icon: InboxIcon, primary: true },
  // Every role (Feature 012): working leads is the operator's job too; no money for them.
  { href: "/crm", group: "work", label: "CRM", icon: KanbanIcon },
  // Every role (feature 019, phase D): the day's appointments are the front desk's work.
  { href: "/agenda", group: "work", label: "Agenda", icon: CalendarIcon },
  { href: "/campanas", group: "work", label: "Campañas", icon: MegaphoneIcon },
  // Every role (Feature 010); an operator sees it without money.
  { href: "/resultados", group: "work", label: "Resultados", icon: ChartIcon },
  { href: "/asistente", group: "agent", label: "Asistente", icon: SparkIcon, roles: MANAGES_CONFIGURATION, primary: true },
  { href: "/onboarding", group: "agent", label: "Configuración inicial", icon: SparkIcon, roles: MANAGES_CONFIGURATION },
  { href: "/sandbox", group: "agent", label: "Modo prueba", icon: ChatIcon, primary: true },
  { href: "/catalog", group: "agent", label: "Catálogo", icon: BoxIcon, roles: MANAGES_CONFIGURATION },
  { href: "/channel", group: "business", label: "Mi número", icon: PhoneIcon, roles: MANAGES_CONFIGURATION },
  { href: "/knowledge", group: "agent", label: "Conocimiento", icon: BookIcon, roles: MANAGES_CONFIGURATION },
  { href: "/team", group: "business", label: "Equipo", icon: UsersIcon, roles: MANAGES_CONFIGURATION },
  // Feature 009: money is for the owner and the admins; operators work the inbox.
  { href: "/plans", group: "business", label: "Mi plan", icon: ReceiptIcon, roles: MANAGES_CONFIGURATION },
];

function visibleTo(role: Role | null): NavItem[] {
  return NAV.filter((item) => !item.roles || (role !== null && item.roles.includes(role)));
}

/** The visible items in their groups, in menu order; a group nobody may see is left out. */
function grouped(items: NavItem[]): { group: NavGroup; items: NavItem[] }[] {
  return (Object.keys(GROUP_LABELS) as NavGroup[])
    .map((group) => ({ group, items: items.filter((item) => item.group === group) }))
    .filter((entry) => entry.items.length > 0);
}

function isCurrent(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export default function PanelLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { status, user, role, isPlatformAdmin, onlyPlatformAdmin, signOut } = useSession();

  const [tenant, setTenant] = useState<TenantSummary | null>(null);
  const [tenantError, setTenantError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "anonymous") {
      router.replace("/login");
    }
  }, [status, router]);

  // Only when they have no business of their own - this shell would have
  // nothing to render. An operator who also runs a business keeps their
  // panel and reaches the queue through the link below; forcing the redirect
  // would take their own account away from them.
  useEffect(() => {
    if (status === "authenticated" && onlyPlatformAdmin) {
      router.replace("/admin");
    }
  }, [status, onlyPlatformAdmin, router]);

  const loadTenant = useCallback(async () => {
    setTenantError(null);
    try {
      setTenant(await api.tenant.current());
    } catch (caught) {
      setTenantError(humanMessage(caught));
    }
  }, []);

  // A platform operator who also runs a business has one to load: only the one with no business of
  // their own (who is sent to `/admin`) has nothing here. Skipping every operator left the banner's
  // placeholder grey for good and the business's name out of the menu.
  useEffect(() => {
    if (status === "authenticated" && !onlyPlatformAdmin) {
      void loadTenant();
    }
  }, [status, onlyPlatformAdmin, loadTenant]);

  // `loading` is not `anonymous`. Rendering the login redirect here would
  // bounce every returning user out for a frame before the refresh lands.
  if (status !== "authenticated" || onlyPlatformAdmin) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas p-6">
        <div className="w-full max-w-sm space-y-3">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    );
  }

  const items = visibleTo(role);
  const bottomItems = items.filter((item) => item.primary).slice(0, 5);

  return (
    <div className="min-h-dvh bg-canvas">
      {/* Keyboard users should not have to tab through the whole nav on
       * every page to reach the content. */}
      <a
        href="#contenido"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:font-semibold"
      >
        Saltar al contenido
      </a>

      <div className="lg:flex">
        {/* Sidebar: large screens only. */}
        {/* Pinned to the screen: the menu and "Cerrar sesión" stay in reach however long the page is. */}
        <aside className="hidden w-64 shrink-0 border-r border-line bg-surface lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col">
          <div className="px-6 py-4">
            <span className="font-heading text-xl font-bold tracking-tight text-brand">Leo</span>
            {tenant ? (
              <p className="mt-0.5 truncate text-sm text-ink-faint" title={tenant.name}>
                {tenant.name}
              </p>
            ) : null}
          </div>

          <nav aria-label="Principal" className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
            {grouped(items).map(({ group, items: inGroup }) => (
              <div key={group} className={group === "work" ? undefined : "mt-4"}>
                {GROUP_LABELS[group] ? (
                  <p className="mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                    {GROUP_LABELS[group]}
                  </p>
                ) : null}
                <ul className="space-y-0.5">
                  {inGroup.map((item) => {
                    const current = isCurrent(pathname, item.href);
                    const Icon = item.icon;
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          // The one thing a nav must always answer: where am I?
                          aria-current={current ? "page" : undefined}
                          className={cx(
                            "flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm transition-colors duration-150",
                            current
                              ? "bg-accent/10 font-semibold text-accent"
                              : "text-ink-soft hover:bg-surface-muted hover:text-ink",
                          )}
                        >
                          <Icon className="size-5 shrink-0" />
                          {item.label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>

          <div className="border-t border-line p-3">
            {isPlatformAdmin ? (
              <Link
                href="/admin"
                className="mb-2 flex min-h-11 items-center gap-3 rounded-lg bg-brand px-3 text-sm font-semibold text-brand-fg hover:opacity-90"
              >
                <SparkIcon className="size-5 shrink-0" />
                Panel de operación
              </Link>
            ) : null}
            <p className="truncate px-3 pb-2 text-xs text-ink-faint" title={user?.email}>
              {user?.full_name}
            </p>
            {/* Separated from the navigation above by a rule: signing out is
             * not a destination, and putting it in the same list is how
             * people log themselves out by mistake. */}
            <button
              type="button"
              onClick={() => void signOut()}
              className="flex min-h-10 w-full cursor-pointer items-center gap-3 rounded-lg px-3 text-sm text-ink-soft transition-colors duration-150 hover:bg-surface-muted hover:text-ink"
            >
              <LogOutIcon className="size-5 shrink-0" />
              Cerrar sesión
            </button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* Top bar: phones and tablets. */}
          <header className="flex items-center justify-between border-b border-line bg-surface px-4 py-3 lg:hidden">
            <div className="min-w-0">
              <span className="font-heading text-lg font-bold tracking-tight text-brand">Leo</span>
              {tenant ? (
                <p className="truncate text-xs text-ink-faint" title={tenant.name}>
                  {tenant.name}
                </p>
              ) : null}
            </div>
            <div className="flex items-center gap-1">
              {/* The sidebar holds this link on a computer; a platform operator on a
               * phone had no way to reach the queue. */}
              {isPlatformAdmin ? (
                <Link
                  href="/admin"
                  className="flex min-h-11 items-center rounded-lg bg-brand px-3 text-xs font-semibold text-brand-fg"
                >
                  Panel de operación
                </Link>
              ) : null}
              <button
                type="button"
                onClick={() => void signOut()}
                className="flex size-11 cursor-pointer items-center justify-center rounded-lg text-ink-soft hover:bg-surface-muted"
                aria-label="Cerrar sesión"
              >
                <LogOutIcon className="size-5" />
              </button>
            </div>
          </header>

          <main
            id="contenido"
            // Bottom padding clears the fixed nav bar on phones, so the last
            // row of content is never hidden underneath it.
            // As wide as the screen allows (up to a very large monitor), with room at the sides that
            // grows with it: a column of 1 000 px on a 1 900 px screen leaves the information crowded
            // in the middle and the rest empty. Pages decide how to use the width (grids, tables).
            className="mx-auto w-full min-w-0 max-w-[1760px] flex-1 px-4 pb-24 pt-4 sm:px-6 lg:px-8 lg:pb-10 lg:pt-6 xl:px-10"
          >
            {tenantError ? (
              <div className="mb-4">
                <Alert tone="danger" title="No pudimos cargar tu negocio">
                  {tenantError}{" "}
                  <button
                    type="button"
                    onClick={() => void loadTenant()}
                    className="cursor-pointer font-semibold text-accent underline"
                  >
                    Reintentar
                  </button>
                </Alert>
              </div>
            ) : null}

            {tenant ? (
              <div className="mb-5">
                <TrialBanner tenant={tenant} />
              </div>
            ) : (
              <Skeleton className="mb-5 h-16 w-full" />
            )}

            {children}
          </main>
        </div>
      </div>

      {/* Bottom navigation: phones. Five items maximum, each with an icon and
       * a word - an icon-only bar is a guessing game. */}
      <nav
        aria-label="Principal"
        className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-5 border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] lg:hidden"
      >
        {bottomItems.map((item) => {
          const current = isCurrent(pathname, item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={current ? "page" : undefined}
              className={cx(
                "flex min-h-14 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[11px] transition-colors duration-150",
                current ? "font-semibold text-accent" : "text-ink-faint",
              )}
            >
              <Icon className="size-5" />
              <span className="max-w-full truncate">{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
