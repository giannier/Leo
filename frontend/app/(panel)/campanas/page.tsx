"use client";

/**
 * Campaigns (Feature 011): broadcasts to customers who accepted promotions, the WhatsApp
 * templates they need, the contacts with their permission and tags, and the settings
 * (asking for permission after a sale, follow-ups after 24 hours).
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { CampaignsPanel } from "@/components/campaigns/campaigns";
import { ContactsPanel } from "@/components/campaigns/contacts";
import { SettingsPanel } from "@/components/campaigns/settings";
import { TemplatesPanel } from "@/components/campaigns/templates";
import { cx } from "@/components/ui";
import { MANAGES_CONFIGURATION, useHasRole } from "@/lib/session";

const TABS = [
  ["campanas", "Campañas"],
  ["plantillas", "Plantillas"],
  ["contactos", "Contactos"],
  ["ajustes", "Ajustes"],
] as const;
type Tab = (typeof TABS)[number][0];

function Campaigns() {
  const canEdit = useHasRole(...MANAGES_CONFIGURATION);
  const params = useSearchParams();
  const router = useRouter();
  const raw = params.get("tab");
  const tab: Tab = TABS.some(([key]) => key === raw) ? (raw as Tab) : "campanas";

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Campañas</h1>
        <p className="text-sm text-ink-soft">
          Promociones por WhatsApp a quienes aceptaron recibirlas, con plantillas aprobadas por Meta.
        </p>
      </header>
      <nav aria-label="Secciones de campañas" className="flex gap-2 overflow-x-auto pb-1">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-current={tab === key ? "page" : undefined}
            onClick={() => router.replace(key === "campanas" ? "/campanas" : `/campanas?tab=${key}`)}
            className={cx(
              "min-h-11 shrink-0 cursor-pointer rounded-full px-4 text-sm font-semibold",
              tab === key ? "bg-brand text-brand-fg" : "border border-line bg-surface text-ink-soft hover:bg-surface-muted",
            )}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "campanas" ? <CampaignsPanel canEdit={canEdit} /> : null}
      {tab === "plantillas" ? <TemplatesPanel canEdit={canEdit} /> : null}
      {tab === "contactos" ? <ContactsPanel canImport={canEdit} /> : null}
      {tab === "ajustes" ? <SettingsPanel canEdit={canEdit} /> : null}
    </div>
  );
}

export default function CampaignsPage() {
  return (
    <Suspense>
      <Campaigns />
    </Suspense>
  );
}
