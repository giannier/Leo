"use client";

/**
 * The list blocks of "Información del negocio" (TASK-438): payment methods with
 * their holder and QR, frequent questions and promotions.
 */

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Checkbox, Field, Select, TextArea } from "@/components/ui";
import {
  api,
  type Faq,
  type MediaAsset,
  type PaymentMethod,
  type PaymentMethodBody,
  type Promotion,
  type PromotionBody,
} from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const METHODS = [
  { value: "yape", label: "Yape" },
  { value: "plin", label: "Plin" },
  { value: "transferencia", label: "Transferencia bancaria" },
  { value: "tarjeta", label: "Tarjeta" },
  { value: "efectivo", label: "Efectivo" },
  { value: "link", label: "Link de pago" },
] as const;
const METHOD_LABEL = new Map<string, string>(METHODS.map((m) => [m.value, m.label]));

/* ----------------------------------------------------------- Métodos de pago */

function MethodRow({
  method,
  images,
  onChanged,
}: {
  method: PaymentMethod;
  images: MediaAsset[];
  onChanged: () => void;
}) {
  const [instructions, setInstructions] = useState(method.instructions);
  const [holder, setHolder] = useState(method.titular ?? "");
  const [qr, setQr] = useState(method.media_asset_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.payments.update(method.id, {
        instructions,
        titular: holder.trim() || null,
        media_asset_id: qr || null,
      });
      onChanged();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-line p-3">
      <p className="text-sm font-semibold text-ink">{METHOD_LABEL.get(method.method) ?? method.method}</p>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <TextArea
        label="Lo que el asistente le envía al cliente"
        rows={2}
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
      />
      <Field
        label="Titular de la cuenta"
        hint="Como aparece en el comprobante. Si un comprobante sale a otro nombre, el pedido se marca para revisar."
        value={holder}
        onChange={(e) => setHolder(e.target.value)}
      />
      <Select
        label="QR o imagen de la cuenta (opcional)"
        options={[{ value: "", label: "Sin imagen" }, ...images.map((m) => ({ value: m.id, label: m.titulo }))]}
        value={qr}
        onChange={(e) => setQr(e.target.value)}
        hint="Súbela antes en Multimedia."
      />
      <div className="flex gap-2">
        <Button type="button" loading={busy} onClick={() => void save()}>
          Guardar
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={async () => {
            await api.payments.remove(method.id);
            onChanged();
          }}
        >
          Quitar
        </Button>
      </div>
    </div>
  );
}

export function PaymentMethods() {
  const [methods, setMethods] = useState<PaymentMethod[] | null>(null);
  const [images, setImages] = useState<MediaAsset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newMethod, setNewMethod] = useState("");
  const [newInstructions, setNewInstructions] = useState("");

  const load = useCallback(async () => {
    try {
      const [list, media] = await Promise.all([api.payments.list(), api.assistant.media()]);
      setMethods(list);
      setImages(media.items.filter((m) => m.tipo === "imagen"));
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const free = METHODS.filter((m) => !methods?.some((x) => x.method === m.value));
  return (
    <div className="space-y-3">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {methods?.map((method) => (
        <MethodRow key={method.id} method={method} images={images} onChanged={() => void load()} />
      ))}
      {free.length ? (
        <form
          className="grid gap-2 rounded-lg border border-dashed border-line-strong p-3"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(null);
            try {
              await api.payments.create({
                method: newMethod as PaymentMethodBody["method"],
                instructions: newInstructions,
                validate_receipts: true,
                is_active: true,
              });
              setNewMethod("");
              setNewInstructions("");
              await load();
            } catch (caught) {
              setError(humanMessage(caught));
            }
          }}
        >
          <Select
            label="Agregar un método"
            options={[{ value: "", label: "Elige uno" }, ...free]}
            value={newMethod}
            onChange={(e) => setNewMethod(e.target.value)}
          />
          {newMethod ? (
            <>
              <TextArea
                label="Instrucciones"
                rows={2}
                placeholder="Yape al 987654321 a nombre de ... Cuando pagues, envía la foto del comprobante."
                value={newInstructions}
                onChange={(e) => setNewInstructions(e.target.value)}
              />
              <Button type="submit" disabled={!newInstructions.trim()}>
                Agregar
              </Button>
            </>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------------- FAQ */

export function Faqs({ seedQuestion = "" }: { seedQuestion?: string }) {
  const [items, setItems] = useState<Faq[] | null>(null);
  const [question, setQuestion] = useState(seedQuestion);
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems((await api.assistant.faqs()).items);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-soft">
        El asistente las responde tal cual cuando el cliente pregunta algo parecido.
      </p>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <ul className="divide-y divide-line rounded-lg border border-line">
        {items?.map((faq) => (
          <li key={faq.id} className="flex items-start gap-3 p-3 text-sm">
            <div className="flex-1">
              <p className="font-medium text-ink">{faq.pregunta}</p>
              <p className="mt-0.5 text-ink-soft">{faq.respuesta}</p>
            </div>
            <Button
              type="button"
              variant="ghost"
              onClick={async () => {
                await api.assistant.removeFaq(faq.id);
                await load();
              }}
            >
              Borrar
            </Button>
          </li>
        ))}
        {items && !items.length ? <li className="p-3 text-sm text-ink-faint">Aún no hay preguntas.</li> : null}
      </ul>
      <form
        className="grid gap-2"
        onSubmit={async (event) => {
          event.preventDefault();
          setError(null);
          try {
            await api.assistant.createFaq({ pregunta: question, respuesta: answer });
            setQuestion("");
            setAnswer("");
            await load();
          } catch (caught) {
            setError(humanMessage(caught));
          }
        }}
      >
        <Field label="Pregunta" value={question} onChange={(e) => setQuestion(e.target.value)} />
        <TextArea label="Respuesta" rows={2} value={answer} onChange={(e) => setAnswer(e.target.value)} />
        <div>
          <Button type="submit" disabled={question.trim().length < 3 || !answer.trim()}>
            Agregar pregunta
          </Button>
        </div>
      </form>
    </div>
  );
}

/* --------------------------------------------------------------- Promociones */

const KINDS = [
  { value: "porcentaje", label: "Porcentaje de descuento" },
  { value: "monto_fijo", label: "Monto fijo de descuento" },
  { value: "nxm", label: "Lleva N, paga M (2x1, 3x2)" },
  { value: "precio_cantidad", label: "Precio por cantidad (3 por S/ 50)" },
] as const;

function describe(p: Promotion): string {
  switch (p.tipo) {
    case "porcentaje":
      return `${p.valor} % de descuento`;
    case "monto_fijo":
      return `${p.valor} menos`;
    case "nxm":
      return `Lleva ${p.n}, paga ${p.m}`;
    default:
      return `${p.m} por ${p.valor}`;
  }
}

export function Promotions() {
  const [items, setItems] = useState<Promotion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    nombre: "",
    tipo: "porcentaje",
    valor: "",
    n: "2",
    m: "1",
    uso: "automatica",
    vigencia_horas: "24",
    termina_en: "",
  });

  const load = useCallback(async () => {
    try {
      setItems((await api.assistant.promotions()).items);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch });

  async function create() {
    setError(null);
    const body: Record<string, unknown> = { nombre: form.nombre, tipo: form.tipo, uso: form.uso };
    if (form.tipo === "nxm") {
      body.n = Number(form.n);
      body.m = Number(form.m);
    } else {
      body.valor = form.valor;
      if (form.tipo === "precio_cantidad") body.m = Number(form.m);
    }
    if (form.uso === "cierre") body.vigencia_horas = Number(form.vigencia_horas);
    if (form.termina_en) body.termina_en = new Date(form.termina_en).toISOString();
    try {
      await api.assistant.createPromotion(body as PromotionBody);
      set({ nombre: "", valor: "" });
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }

  return (
    <div className="space-y-3">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <ul className="divide-y divide-line rounded-lg border border-line">
        {items?.map((promo) => (
          <li key={promo.id} className="flex items-center gap-3 p-3 text-sm">
            <div className="flex-1">
              <p className="font-medium text-ink">
                {promo.nombre} {promo.is_active ? null : <span className="text-ink-faint">(apagada)</span>}
              </p>
              <p className="text-ink-soft">
                {describe(promo)} · {promo.uso === "cierre" ? `de cierre, ${promo.vigencia_horas} h` : "automática"}
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              onClick={async () => {
                await api.assistant.removePromotion(promo.id);
                await load();
              }}
            >
              Quitar
            </Button>
          </li>
        ))}
        {items && !items.length ? <li className="p-3 text-sm text-ink-faint">Sin promociones.</li> : null}
      </ul>
      <form
        className="grid gap-3 rounded-lg border border-dashed border-line-strong p-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <Field label="Nombre" value={form.nombre} onChange={(e) => set({ nombre: e.target.value })} />
        <Select label="Tipo" options={KINDS} value={form.tipo} onChange={(e) => set({ tipo: e.target.value })} />
        {form.tipo === "nxm" ? (
          <>
            <Field label="Lleva (N)" inputMode="numeric" value={form.n} onChange={(e) => set({ n: e.target.value })} />
            <Field label="Paga (M)" inputMode="numeric" value={form.m} onChange={(e) => set({ m: e.target.value })} />
          </>
        ) : (
          <Field
            label={form.tipo === "porcentaje" ? "Porcentaje" : form.tipo === "precio_cantidad" ? "Precio del grupo" : "Monto"}
            inputMode="decimal"
            value={form.valor}
            onChange={(e) => set({ valor: e.target.value })}
          />
        )}
        {form.tipo === "precio_cantidad" ? (
          <Field label="Cantidad del grupo" inputMode="numeric" value={form.m} onChange={(e) => set({ m: e.target.value })} />
        ) : null}
        <Checkbox
          label="Promoción de cierre"
          hint="Solo se ofrece una vez, cuando el cliente duda por el precio."
          checked={form.uso === "cierre"}
          onChange={(e) => set({ uso: e.target.checked ? "cierre" : "automatica" })}
        />
        {form.uso === "cierre" ? (
          <Field
            label="Vigencia desde que se ofrece (horas)"
            inputMode="numeric"
            value={form.vigencia_horas}
            onChange={(e) => set({ vigencia_horas: e.target.value })}
          />
        ) : null}
        <Field label="Termina (opcional)" type="datetime-local" value={form.termina_en} onChange={(e) => set({ termina_en: e.target.value })} />
        <div className="sm:col-span-2">
          <Button type="submit" disabled={!form.nombre.trim()}>
            Agregar promoción
          </Button>
        </div>
      </form>
    </div>
  );
}
