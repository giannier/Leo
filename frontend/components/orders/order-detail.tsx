"use client";

/** The body of one order (presentational): items, customer, receipts, history. */

import { AlertIcon } from "@/components/icons";
import { Alert, Button, Card, Skeleton, cx } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { OrderDetail, PaymentProof } from "@/lib/api/client";

import {
  HISTORY_LABELS,
  OBSERVATION_LABELS,
  STATE_LABELS,
  dataLabel,
  formatDate,
  formatDateTime,
  formatMoney,
} from "./labels";

/* ------------------------------------------------------------------ items */

export function OrderItems({ order }: { order: OrderDetail }) {
  const promotion = order.promocion as { nombre?: string; ahorro?: string } | null;
  return (
    <Card title="Productos">
      <ul className="divide-y divide-line">
        {order.items.map((line, index) => (
          <li key={`${line.producto_id ?? "libre"}-${index}`} className="flex justify-between gap-4 py-2.5 text-sm">
            <div className="min-w-0">
              <p className="text-ink">
                {line.cantidad > 1 ? `${line.cantidad} × ` : ""}
                {line.nombre}
              </p>
              {line.selecciones.length > 0 ? (
                <p className="text-xs text-ink-soft">Incluye: {line.selecciones.join(", ")}</p>
              ) : null}
              {line.faltan_selecciones > 0 ? (
                <p className="flex items-center gap-1 text-xs font-medium text-warning">
                  <AlertIcon className="size-3.5" />
                  Faltan {line.faltan_selecciones} por elegir
                </p>
              ) : null}
            </div>
            <span className="tabular shrink-0 font-medium text-ink">
              {formatMoney(line.subtotal, order.moneda)}
            </span>
          </li>
        ))}
      </ul>

      <dl className="tabular mt-3 space-y-1 border-t border-line pt-3 text-sm">
        <Row label="Subtotal" value={formatMoney(order.subtotal, order.moneda)} />
        {promotion ? (
          <Row
            label={`Promoción${promotion.nombre ? `: ${promotion.nombre}` : ""}`}
            value={`− ${formatMoney(promotion.ahorro ?? order.descuento, order.moneda)}`}
          />
        ) : null}
        {order.tipo_entrega === "delivery" ? (
          <Row label="Envío" value={formatMoney(order.envio, order.moneda)} />
        ) : null}
        <div className="flex justify-between border-t border-line pt-2 text-base font-semibold text-ink">
          <dt>Total</dt>
          <dd data-testid="order-total">{formatMoney(order.total, order.moneda)}</dd>
        </div>
      </dl>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between text-ink-soft">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/* ---------------------------------------------------------------- customer */

export function CustomerCard({ order }: { order: OrderDetail }) {
  const entries = Object.entries(order.datos_cliente).filter(
    ([, value]) => value !== null && value !== "" && typeof value !== "object",
  );
  const delivery =
    order.tipo_entrega === "delivery"
      ? `Delivery${order.zona_envio ? ` · ${order.zona_envio}` : ""}`
      : order.tipo_entrega === "recojo"
        ? "Recojo en tienda"
        : null;
  return (
    <Card title="Cliente y entrega">
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt className="text-xs text-ink-faint">{dataLabel(key)}</dt>
            <dd className="break-words text-ink">{String(value)}</dd>
          </div>
        ))}
        {delivery ? (
          <div>
            <dt className="text-xs text-ink-faint">Entrega</dt>
            <dd className="text-ink">{delivery}</dd>
          </div>
        ) : null}
        <div>
          <dt className="text-xs text-ink-faint">Forma de pago</dt>
          <dd className="text-ink">
            {order.modalidad_pago === "contra_entrega" ? "Contra entrega" : "Pago previo"}
          </dd>
        </div>
      </dl>
      {entries.length === 0 ? (
        <p className="text-sm text-ink-soft">El cliente aún no dejó sus datos.</p>
      ) : null}
    </Card>
  );
}

/* ------------------------------------------------------------------ proofs */

export type ProofImageState =
  | { status: "loading" }
  | { status: "ready"; url: string }
  | { status: "failed" };

export function ProofsCard({
  order,
  images,
  onLoadImage,
  onImageBroken,
}: {
  order: OrderDetail;
  /** Signed links already fetched, by proof id. */
  images: Record<string, ProofImageState>;
  onLoadImage: (proofId: string) => void;
  /** The browser could not draw the image (expired link, missing object). */
  onImageBroken: (proofId: string) => void;
}) {
  if (order.comprobantes.length === 0) {
    return (
      <Card title="Comprobante">
        <p className="text-sm text-ink-soft">
          {order.modalidad_pago === "contra_entrega"
            ? "Este pedido se paga contra entrega: no lleva comprobante."
            : "El cliente aún no envió su comprobante de pago."}
        </p>
      </Card>
    );
  }
  return (
    <Card title="Comprobante">
      <div className="space-y-5">
        {order.comprobantes.map((proof, index) => (
          <Proof
            key={proof.id}
            proof={proof}
            orderTotal={formatMoney(order.total, order.moneda)}
            latest={index === 0}
            image={images[proof.id]}
            onLoadImage={() => onLoadImage(proof.id)}
            onImageBroken={() => onImageBroken(proof.id)}
          />
        ))}
      </div>
    </Card>
  );
}

const PROOF_STATE: Record<string, string> = {
  por_verificar: "Por verificar",
  aprobado: "Aprobado",
  rechazado: "Rechazado",
  anulado: "Anulado",
};

function Proof({
  proof,
  orderTotal,
  latest,
  image,
  onLoadImage,
  onImageBroken,
}: {
  proof: PaymentProof;
  orderTotal: string;
  latest: boolean;
  image: ProofImageState | undefined;
  onLoadImage: () => void;
  onImageBroken: () => void;
}) {
  const read: [string, string][] = [
    ["Monto leído", formatMoney(proof.monto, proof.moneda)],
    ["Fecha de la operación", formatDate(proof.fecha_operacion)],
    ["Código de operación", proof.codigo_operacion ?? "—"],
    ["Entidad", proof.entidad ?? "—"],
    ["Destinatario", proof.destinatario ?? "—"],
    [
      "Seguridad de la lectura",
      proof.confianza === null ? "—" : `${Math.round(Number(proof.confianza) * 100)} %`,
    ],
  ];

  return (
    <div data-testid="proof" className="grid gap-4 sm:grid-cols-[minmax(0,14rem)_1fr]">
      <div>
        {image?.status === "ready" ? (
          // A signed link to a private object: a plain <img>, because next/image
          // would proxy it through the app server and defeat the short expiry.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={image.url}
            alt="Foto del comprobante enviada por el cliente"
            className="max-h-80 w-full rounded-lg border border-line object-contain"
            onError={onImageBroken}
          />
        ) : image?.status === "loading" ? (
          <Skeleton className="h-48 w-full" />
        ) : (
          <div className="flex h-32 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong p-3 text-center text-xs text-ink-soft">
            {image?.status === "failed" ? "No pudimos cargar la imagen." : "Imagen del comprobante"}
            <Button variant="secondary" onClick={onLoadImage}>
              {image?.status === "failed" ? "Reintentar" : "Ver imagen"}
            </Button>
          </div>
        )}
      </div>

      <div className="space-y-3">
        <p className="text-sm">
          <span className="font-semibold text-ink">{latest ? "Último comprobante" : "Anterior"}</span>{" "}
          <span className="text-ink-faint">
            · {PROOF_STATE[proof.estado] ?? proof.estado} · {formatDateTime(proof.creado)}
          </span>
        </p>

        {proof.observaciones.length > 0 ? (
          <ul data-testid="proof-observations" className="space-y-1.5">
            {proof.observaciones.map((code) => (
              <li
                key={code}
                className="flex gap-2 rounded-lg border border-warning/40 bg-warning-soft p-2.5 text-sm"
              >
                <AlertIcon className="mt-0.5 size-4 shrink-0 text-warning" />
                <span>
                  <span className="font-semibold text-warning">
                    {OBSERVATION_LABELS[code]?.label ?? code}.
                  </span>{" "}
                  <span className="text-ink-soft">
                    {code === "monto_distinto"
                      ? `El pedido es de ${orderTotal}.`
                      : (OBSERVATION_LABELS[code]?.detail ?? "")}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        ) : null}

        <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
          {read.map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-ink-faint">{label}</dt>
              <dd className={cx("break-words text-ink", value === "—" && "text-ink-faint")}>{value}</dd>
            </div>
          ))}
        </dl>

        {proof.motivo_rechazo ? (
          <p className="text-sm text-danger">Motivo del rechazo: {proof.motivo_rechazo}</p>
        ) : null}
        <p className="text-xs text-ink-faint">
          Estos datos los leyó el sistema de la imagen y pueden tener errores: compáralos con la foto.
        </p>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- history */

export function HistoryCard({ order }: { order: OrderDetail }) {
  return (
    <Card title="Historial">
      <ol className="space-y-2 text-sm">
        {order.historial.map((entry, index) => (
          <li key={`${entry.evento}-${entry.en}-${index}`} className="flex justify-between gap-3">
            <span className="text-ink">
              {HISTORY_LABELS[entry.evento] ?? entry.evento}
              {entry.estado && entry.evento === "order.status_changed" ? (
                <span className="text-ink-soft"> → {STATE_LABELS[entry.estado] ?? entry.estado}</span>
              ) : null}
            </span>
            <span className="shrink-0 text-xs text-ink-faint">{formatDateTime(entry.en)}</span>
          </li>
        ))}
      </ol>
    </Card>
  );
}

/* ---------------------------------------------------------------- warnings */

/** Opens a kept image in a new tab; the link is signed and lives ten minutes. */
async function openNoteImage(orderId: string, imageId: string) {
  const tab = window.open("", "_blank");
  try {
    const { url } = await api.orders.noteImage(orderId, imageId);
    if (tab) tab.location.href = url;
  } catch {
    tab?.close();
  }
}

/** What the operator must know before deciding. */
export function OrderNotes({ order }: { order: OrderDetail }) {
  const awaiting = order.estado === "por_confirmar";
  const customerNotes = order.notas.filter((note) => note.origen === "cliente");
  const imageNotes = order.notas.filter((note) => note.imagen_id);
  return (
    <div className="space-y-3">
      {awaiting && !order.ventana_abierta ? (
        <Alert tone="warning" title="La ventana de 24 h con este cliente está cerrada">
          Puedes confirmar o rechazar igual, pero el aviso no saldrá por WhatsApp: escríbele tú por otro
          medio.
        </Alert>
      ) : null}
      {order.devolucion_pendiente ? (
        <Alert tone="danger" title="Devolución pendiente">
          <span data-testid="refund-pending">
            Este pedido se descartó después de confirmar el pago. Devuélvele el dinero al cliente.
          </span>
        </Alert>
      ) : null}
      {customerNotes.length > 0 ? (
        <Alert tone="warning" title="El cliente pidió cambios mientras revisabas">
          <ul data-testid="order-notes" className="space-y-1">
            {customerNotes.map((note) => (
              <li key={note.en}>
                <span className="text-ink-faint">{formatDateTime(note.en)}:</span> {note.texto}
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}
      {imageNotes.length > 0 ? (
        <Alert tone="info" title="Imágenes que no parecían un comprobante">
          <ul data-testid="unrecognised-images" className="space-y-1">
            {imageNotes.map((note) => (
              <li key={note.en} className="flex flex-wrap items-center gap-2">
                <span className="text-ink-faint">{formatDateTime(note.en)}</span>
                <button
                  type="button"
                  className="cursor-pointer font-medium text-accent hover:underline"
                  onClick={() => void openNoteImage(order.id, note.imagen_id ?? "")}
                >
                  Ver imagen
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-ink-faint">
            El sistema no la tomó como pago. Revísala por si era un comprobante borroso.
          </p>
        </Alert>
      ) : null}
      {order.cliente_posible_duplicado ? (
        <Alert tone="warning" title="Este cliente podría estar registrado dos veces">
          <span data-testid="duplicate-contact">
            Escribió con un identificador de WhatsApp y un teléfono que pertenecen a dos contactos
            distintos. No los unimos: revisa sus pedidos anteriores antes de decidir.
          </span>
        </Alert>
      ) : null}
      {order.es_sandbox ? (
        <Alert tone="info">Pedido del modo prueba: no es una venta real.</Alert>
      ) : null}
      {order.estado === "borrador" && order.motivo_rechazo ? (
        <Alert tone="warning" title="Fue rechazado">
          {order.motivo_rechazo}
          {order.rechazo_vigente ? " El cliente aún no corrigió nada." : ""}
        </Alert>
      ) : null}
      {order.siguiente_paso && order.estado !== "borrador" ? (
        <Alert tone="success" title="Siguiente paso comunicado al cliente">
          {order.siguiente_paso}
        </Alert>
      ) : null}
    </div>
  );
}
