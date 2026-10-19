"use client";

/**
 * Campaigns (Feature 011, HU-03): pick an approved template, choose who gets it (only people
 * who accepted promotions), see how many and roughly what Meta will charge, and send now or
 * later within daytime hours. Each campaign shows what it achieved: delivered, read, replies,
 * opt-outs and sales within 72 hours.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { money } from "@/components/crm/lead-drawer";
import { Bubble, preview } from "@/components/campaigns/templates";
import { Alert, Button, Card, Checkbox, Field, Select, Skeleton, cx } from "@/components/ui";
import {
  api,
  type Campaign,
  type CampaignEstimate,
  type CampaignReport,
  type MessageTemplate,
  type Segment,
  type TagCount,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const STATUS: Record<string, { label: string; tone: string }> = {
  draft: { label: "Borrador", tone: "bg-surface-muted text-ink-soft" },
  scheduled: { label: "Programada", tone: "bg-info-soft text-info" },
  sending: { label: "Enviando", tone: "bg-info-soft text-info" },
  paused: { label: "En pausa", tone: "bg-warning-soft text-warning" },
  completed: { label: "Terminada", tone: "bg-success-soft text-success" },
  canceled: { label: "Cancelada", tone: "bg-surface-muted text-ink-faint" },
};
const PAUSES: Record<string, string> = {
  calidad:
    "WhatsApp bajó la calidad de tu número. Pausamos para protegerlo; podrás reanudar cuando se recupere.",
  persona: "La pausaste tú o alguien de tu equipo.",
  sin_numero: "Tu número de WhatsApp está desconectado.",
  plantilla: "Meta pausó o desactivó la plantilla.",
};
const STAGES = [
  ["nuevo", "Nuevo"],
  ["interesado", "Interesado"],
  ["por_pagar", "Por pagar"],
  ["ganado", "Ganado"],
  ["perdido", "Perdido"],
] as const;

function Chip({ status }: { status: string }) {
  const style = STATUS[status] ?? STATUS.draft;
  return <span className={cx("rounded-full px-2.5 py-0.5 text-xs font-semibold", style.tone)}>{style.label}</span>;
}

function localInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function Builder({
  initial,
  templates,
  onDone,
  onCancel,
}: {
  initial: Campaign | null;
  templates: MessageTemplate[];
  onDone: (campaign: Campaign, sent: boolean) => void;
  onCancel: () => void;
}) {
  const approved = templates.filter((t) => t.status === "approved");
  const start = (initial?.segment ?? {}) as Segment;
  const [name, setName] = useState(initial?.name ?? "");
  const [templateId, setTemplateId] = useState(initial?.template_id ?? approved[0]?.id ?? "");
  const [tags, setTags] = useState<string[]>(start.etiquetas ?? []);
  const [stages, setStages] = useState<string[]>(start.etapas ?? []);
  const [bought, setBought] = useState(start.compro === true ? "si" : start.compro === false ? "no" : "");
  const [product, setProduct] = useState(start.producto ?? "");
  const [quietMin, setQuietMin] = useState(start.dias_sin_escribir_min?.toString() ?? "");
  const [quietMax, setQuietMax] = useState(start.dias_sin_escribir_max?.toString() ?? "");
  const [from, setFrom] = useState(initial?.window_from ?? "09:00");
  const [to, setTo] = useState(initial?.window_to ?? "20:00");
  const [later, setLater] = useState(Boolean(initial?.scheduled_at));
  const [when, setWhen] = useState(initial?.scheduled_at ? localInput(new Date(initial.scheduled_at)) : localInput(new Date(Date.now() + 3600_000)));
  const [allTags, setAllTags] = useState<TagCount[]>([]);
  const [estimate, setEstimate] = useState<CampaignEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    api.contacts.tags().then(setAllTags).catch(() => setAllTags([]));
  }, []);

  const segment = useMemo<Segment>(
    () => ({
      etiquetas: tags,
      etapas: stages as Segment["etapas"],
      compro: bought === "si" ? true : bought === "no" ? false : null,
      producto: product.trim() || null,
      dias_sin_escribir_min: quietMin ? Number(quietMin) : null,
      dias_sin_escribir_max: quietMax ? Number(quietMax) : null,
    }),
    [tags, stages, bought, product, quietMin, quietMax],
  );

  useEffect(() => {
    if (!templateId) return;
    const timer = setTimeout(() => {
      api.campaigns
        .estimate(templateId, segment)
        .then((value) => {
          setEstimate(value);
          setError(null);
        })
        .catch((caught) => setError(humanMessage(caught)));
    }, 300);
    return () => clearTimeout(timer);
  }, [templateId, segment]);

  const template = approved.find((t) => t.id === templateId);

  async function save(send: boolean) {
    setBusy(true);
    setError(null);
    const body = {
      name,
      template_id: templateId,
      segment,
      window_from: from,
      window_to: to,
      scheduled_at: later ? new Date(when).toISOString() : null,
    };
    try {
      let campaign = initial ? await api.campaigns.update(initial.id, body) : await api.campaigns.create(body);
      if (send) campaign = await api.campaigns.act(campaign.id, "schedule");
      onDone(campaign, send);
    } catch (caught) {
      setError(humanMessage(caught));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  if (!approved.length) {
    return (
      <Card title="Nueva campaña">
        <Alert tone="info" title="Primero necesitas una plantilla aprobada">
          Crea una en la pestaña Plantillas y envíala a Meta. Cuando la aprueben, vuelve aquí.
        </Alert>
        <div className="mt-4">
          <Button type="button" variant="secondary" onClick={onCancel}>
            Volver
          </Button>
        </div>
      </Card>
    );
  }

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  return (
    <Card title={initial ? "Editar campaña" : "Nueva campaña"}>
      <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
        <div className="min-w-0 space-y-5">
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <Field label="Nombre de la campaña" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} required />
          <Select
            label="Plantilla"
            value={templateId}
            onChange={(e) => setTemplateId(e.target.value)}
            options={approved.map((t) => ({ value: t.id, label: t.label }))}
          />

          <fieldset className="space-y-3 rounded-lg border border-line p-4">
            <legend className="px-1 text-sm font-semibold text-ink">¿A quién?</legend>
            <p className="text-xs text-ink-soft">Siempre solo a quienes aceptaron promociones. Sin filtros, a todos ellos.</p>
            {allTags.length ? (
              <div className="space-y-1.5">
                <p className="text-sm font-medium text-ink">Con alguna de estas etiquetas</p>
                <div className="flex flex-wrap gap-2">
                  {allTags.map((t) => (
                    <label key={t.tag} className={cx("cursor-pointer rounded-full border px-3 py-1 text-sm", tags.includes(t.tag) ? "border-accent bg-info-soft text-ink" : "border-line text-ink-soft")}>
                      <input type="checkbox" className="sr-only" checked={tags.includes(t.tag)} onChange={() => setTags((l) => toggle(l, t.tag))} />
                      {t.tag}
                    </label>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="space-y-1.5">
              <p className="text-sm font-medium text-ink">Que estén o hayan estado en el CRM en</p>
              <div className="flex flex-wrap gap-2">
                {STAGES.map(([key, label]) => (
                  <label key={key} className={cx("cursor-pointer rounded-full border px-3 py-1 text-sm", stages.includes(key) ? "border-accent bg-info-soft text-ink" : "border-line text-ink-soft")}>
                    <input type="checkbox" className="sr-only" checked={stages.includes(key)} onChange={() => setStages((l) => toggle(l, key))} />
                    {label}
                  </label>
                ))}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Select
                label="Compras"
                value={bought}
                onChange={(e) => setBought(e.target.value)}
                options={[
                  { value: "", label: "Da igual" },
                  { value: "si", label: "Ya compraron" },
                  { value: "no", label: "Nunca compraron" },
                ]}
              />
              <Field label="Compraron o preguntaron por" value={product} placeholder="Ej.: torta" onChange={(e) => setProduct(e.target.value)} />
              <Field label="No escriben hace al menos (días)" inputMode="numeric" value={quietMin} onChange={(e) => setQuietMin(e.target.value.replace(/\D/g, ""))} />
              <Field label="Escribieron en los últimos (días)" inputMode="numeric" value={quietMax} onChange={(e) => setQuietMax(e.target.value.replace(/\D/g, ""))} />
            </div>
          </fieldset>

          <fieldset className="space-y-3 rounded-lg border border-line p-4">
            <legend className="px-1 text-sm font-semibold text-ink">¿Cuándo?</legend>
            <Checkbox checked={later} onChange={(e) => setLater(e.target.checked)} label="Programar para más tarde" />
            {later ? <Field label="Fecha y hora" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /> : null}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Enviar desde" type="time" min="08:00" max="21:00" value={from} onChange={(e) => setFrom(e.target.value)} />
              <Field label="Hasta" type="time" min="08:00" max="21:00" value={to} onChange={(e) => setTo(e.target.value)} />
            </div>
            <p className="text-xs text-ink-soft">Fuera de ese horario (hora de tu negocio) se espera al día siguiente. Nunca de madrugada.</p>
          </fieldset>
        </div>

        <div className="space-y-4">
          {template ? <Bubble body={preview(template.body)} footer={template.footer} /> : null}
          <div className="rounded-xl border border-line bg-surface-muted p-4 text-sm" data-testid="campaign-estimate">
            {estimate ? (
              <>
                <p className="text-ink-soft">Llegará a</p>
                <p className="font-heading text-3xl font-semibold text-ink">{estimate.destinatarios}</p>
                <p className="text-ink-soft">personas</p>
                {estimate.costo_estimado_usd !== null ? (
                  <p className="mt-2 text-ink">
                    Costo aprox. <strong>US$ {estimate.costo_estimado_usd}</strong>
                    <span className="block text-xs text-ink-faint">
                      Lo cobra Meta a través de YCloud (US$ {estimate.tarifa_usd} por mensaje {estimate.categoria === "marketing" ? "de marketing" : "de utilidad"}), no Leo.
                    </span>
                  </p>
                ) : null}
                {estimate.avisos.map((warning) => (
                  <p key={warning} className="mt-2 text-xs text-warning">
                    {warning}
                    {estimate.limites_aplicados ? "" : " (en modo pruebas no se aplica)"}
                  </p>
                ))}
              </>
            ) : (
              <Skeleton className="h-16 w-full" />
            )}
          </div>
          {confirming ? (
            <div role="dialog" aria-label="Confirmar envío" className="space-y-3 rounded-xl border border-accent p-4 text-sm">
              <p className="text-ink">
                Se enviará a <strong>{estimate?.destinatarios ?? 0}</strong> personas {later ? `el ${new Date(when).toLocaleString("es-PE")}` : "desde ahora"}, entre {from} y {to}.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button type="button" loading={busy} onClick={() => void save(true)}>
                  Confirmar
                </Button>
                <Button type="button" variant="secondary" onClick={() => setConfirming(false)}>
                  Volver
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button type="button" disabled={!name.trim() || !estimate?.destinatarios} onClick={() => setConfirming(true)}>
                {later ? "Programar" : "Enviar"}
              </Button>
              <Button type="button" variant="secondary" loading={busy} disabled={!name.trim()} onClick={() => void save(false)}>
                Guardar borrador
              </Button>
              <Button type="button" variant="ghost" onClick={onCancel}>
                Cancelar
              </Button>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function Report({ report }: { report: CampaignReport }) {
  const tiles: [string, string | number][] = [
    ["Destinatarios", report.destinatarios],
    ["Enviados", report.enviados],
    ["Leídos", report.leidos],
    ["Respondieron", report.respondieron],
    ["Ventas (72 h)", report.ventas_atribuidas],
    ["Bajas", report.bajas],
  ];
  const sold = report.monto_vendido
    ? Object.entries(report.monto_vendido).map(([currency, amount]) => money(amount, currency)).join(" · ")
    : null;
  const progress = report.destinatarios ? Math.round(((report.destinatarios - report.pendientes) / report.destinatarios) * 100) : 0;
  return (
    <div className="space-y-3">
      <div className="h-2 overflow-hidden rounded-full bg-surface-muted" aria-label={`Avance ${progress} %`}>
        <div className="h-full bg-accent" style={{ width: `${progress}%` }} />
      </div>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {tiles.map(([label, value]) => (
          <div key={label} className="rounded-lg bg-surface-muted p-2.5">
            <dt className="text-xs text-ink-faint">{label}</dt>
            <dd className="font-heading text-lg font-semibold text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      {sold ? <p className="text-sm text-ink">Vendido: <strong>{sold}</strong></p> : null}
      {Object.keys(report.motivos_omision).length ? (
        <div className="text-xs text-ink-soft">
          <p className="font-medium text-ink">No se enviaron</p>
          <ul>
            {Object.entries(report.motivos_omision).map(([reason, total]) => (
              <li key={reason}>
                {reason}: {total}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function CampaignsPanel({ canEdit }: { canEdit: boolean }) {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [editing, setEditing] = useState<Campaign | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, all] = await Promise.all([api.campaigns.list(), api.templates.list()]);
      setCampaigns(list);
      setTemplates(all);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  async function act(campaign: Campaign, action: "pause" | "resume" | "cancel" | "schedule") {
    setBusy(campaign.id);
    setError(null);
    setNotice(null);
    try {
      await api.campaigns.act(campaign.id, action);
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  if (editing) {
    return (
      <Builder
        initial={editing === "new" ? null : editing}
        templates={templates}
        onCancel={() => setEditing(null)}
        onDone={(campaign, sent) => {
          setEditing(null);
          setNotice(sent ? `«${campaign.name}» está programada. Verás el avance aquí.` : "Borrador guardado.");
          void load();
        }}
      />
    );
  }

  return (
    <div className="space-y-5">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-soft">
          Envía una promoción a tus clientes que aceptaron recibirlas. Cuando respondan, tu asistente sigue la conversación.
        </p>
        {canEdit ? (
          <Button type="button" onClick={() => setEditing("new")}>
            Nueva campaña
          </Button>
        ) : null}
      </div>
      {!campaigns ? (
        <Skeleton className="h-32 w-full" />
      ) : campaigns.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-ink-soft">
          Todavía no hay campañas.
        </p>
      ) : (
        <ul className="space-y-3" data-testid="campaign-list">
          {campaigns.map((campaign) => (
            <li key={campaign.id} className="min-w-0 space-y-3 rounded-xl border border-line bg-surface p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="truncate font-semibold text-ink">{campaign.name}</h3>
                  <p className="text-xs text-ink-faint">
                    {campaign.template_label}
                    {campaign.scheduled_at ? ` · ${new Date(campaign.scheduled_at).toLocaleString("es-PE")}` : ""}
                    {campaign.estimated_cost_usd ? ` · aprox. US$ ${Number(campaign.estimated_cost_usd).toFixed(2)}` : ""}
                  </p>
                </div>
                <Chip status={campaign.status} />
              </div>
              {campaign.status === "paused" && campaign.pause_reason ? (
                <Alert tone="warning">{PAUSES[campaign.pause_reason] ?? "En pausa."}</Alert>
              ) : null}
              {campaign.report && (campaign.report as unknown as CampaignReport).destinatarios > 0 ? (
                <Report report={campaign.report as unknown as CampaignReport} />
              ) : campaign.status !== "draft" && campaign.status !== "canceled" ? (
                <p className="text-sm text-ink-soft">
                  El envío empieza a la hora programada, dentro del horario elegido. Aquí verás el avance.
                </p>
              ) : null}
              {canEdit ? (
                <div className="flex flex-wrap gap-2">
                  {campaign.status === "draft" ? (
                    <>
                      <Button type="button" variant="secondary" onClick={() => setEditing(campaign)}>
                        Editar
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => {
                          if (window.confirm(`¿Borrar el borrador «${campaign.name}»?`)) {
                            void api.campaigns.remove(campaign.id).then(load).catch((c) => setError(humanMessage(c)));
                          }
                        }}
                      >
                        Borrar
                      </Button>
                    </>
                  ) : null}
                  {campaign.status === "scheduled" || campaign.status === "sending" ? (
                    <Button type="button" variant="secondary" loading={busy === campaign.id} onClick={() => void act(campaign, "pause")}>
                      Pausar
                    </Button>
                  ) : null}
                  {campaign.status === "paused" ? (
                    <Button type="button" loading={busy === campaign.id} onClick={() => void act(campaign, "resume")}>
                      Reanudar
                    </Button>
                  ) : null}
                  {["scheduled", "sending", "paused"].includes(campaign.status) ? (
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => {
                        if (window.confirm("¿Cancelar la campaña? Quienes aún no la recibieron ya no la recibirán.")) {
                          void act(campaign, "cancel");
                        }
                      }}
                    >
                      Cancelar
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
