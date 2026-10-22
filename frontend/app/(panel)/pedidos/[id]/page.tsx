"use client";

/**
 * One order (Feature 004, TASK-432): everything an operator needs to decide.
 *
 * Container: loads the order, fetches the receipt's signed link, runs the three
 * actions and tells the operator what the customer was told. The presentation
 * lives in `components/orders/*`.
 *
 * The receipt image is a signed link that lives ten minutes, so it is requested
 * when the page opens and again on demand, never kept around.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { DocumentsCard } from "@/components/aftersale/documents-card";
import { InvoiceForm } from "@/components/aftersale/invoice-form";
import { OrderReturns } from "@/components/aftersale/order-returns";
import {
  HistoryCard,
  CustomerCard,
  OrderItems,
  OrderNotes,
  ProofsCard,
  type ProofImageState,
} from "@/components/orders/order-detail";
import { HandoverBanner } from "@/components/orders/handover-banner";
import { StateBadge, formatDateTime, noticeMessage } from "@/components/orders/labels";
import { ReviewPanel } from "@/components/orders/review-panel";
import { Alert, Skeleton } from "@/components/ui";
import { api, type CustomerNotice, type OperatorState, type OrderDetail } from "@/lib/api/client";
import { ApiError, humanMessage } from "@/lib/api/errors";

type Outcome = { text: string; tone: "success" | "warning" } | null;

/** The order is a sale made: documents can be attached and a return can be asked for. */
const SOLD = new Set(["confirmado", "en_curso", "completado"]);

export default function PedidoPage() {
  const { id } = useParams<{ id: string }>();

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; missing: boolean } | null>(null);
  const [images, setImages] = useState<Record<string, ProofImageState>>({});
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  // Bumped when a receipt is issued or voided, so the list of documents reads again.
  const [documentsVersion, setDocumentsVersion] = useState(0);

  const loadImage = useCallback(
    async (proofId: string) => {
      setImages((current) => ({ ...current, [proofId]: { status: "loading" } }));
      try {
        const { url } = await api.orders.proofImage(id, proofId);
        setImages((current) => ({ ...current, [proofId]: { status: "ready", url } }));
      } catch {
        setImages((current) => ({ ...current, [proofId]: { status: "failed" } }));
      }
    },
    [id],
  );

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const detail = await api.orders.get(id);
      setOrder(detail);
      // The newest receipt is the one being decided on; the others on demand.
      const latest = detail.comprobantes[0];
      if (latest) void loadImage(latest.id);
    } catch (caught) {
      setLoadError({
        message: humanMessage(caught),
        missing: caught instanceof ApiError && caught.status === 404,
      });
    }
  }, [id, loadImage]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Runs a decision, then shows what the customer was told and reloads. */
  async function act(
    run: () => Promise<{ estado: string; aviso_cliente?: CustomerNotice | null }>,
    done: string,
  ) {
    setBusy(true);
    setActionError(null);
    setOutcome(null);
    try {
      const result = await run();
      if (result.aviso_cliente) {
        const notice = noticeMessage(result.aviso_cliente);
        setOutcome({ tone: notice.tone, text: `${done} ${notice.text}` });
      } else {
        setOutcome({ tone: "success", text: done });
      }
      await load();
    } catch (caught) {
      setActionError(humanMessage(caught));
      // A conflict means somebody else got there first: show the real state.
      if (caught instanceof ApiError && caught.status === 409) await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <Link href="/pedidos" className="inline-flex min-h-10 items-center text-sm font-medium text-accent hover:underline">
        ← Volver a pedidos
      </Link>

      {loadError ? (
        <Alert tone="danger" title={loadError.missing ? "No encontramos este pedido" : undefined}>
          {loadError.missing ? "Puede que sea de otro negocio o que ya no exista." : loadError.message}
        </Alert>
      ) : null}

      {order === null && !loadError ? (
        <div className="space-y-3">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : null}

      {order ? (
        <>
          <header className="flex flex-wrap items-center gap-3">
            <h1 className="font-heading text-2xl font-semibold text-ink">Pedido #{order.numero}</h1>
            <span data-testid="order-state">
              <StateBadge state={order.estado} />
            </span>
            <span className="text-sm text-ink-faint">{formatDateTime(order.creado)}</span>
          </header>

          {outcome ? (
            <Alert tone={outcome.tone}>
              <span data-testid="order-outcome">{outcome.text}</span>
            </Alert>
          ) : null}

          <HandoverBanner conversationId={order.conversation_id} />

          <OrderNotes order={order} />

          <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
            <div className="min-w-0 space-y-5">
              <ProofsCard
                order={order}
                images={images}
                onLoadImage={(proofId) => void loadImage(proofId)}
                onImageBroken={(proofId) =>
                  setImages((current) => ({ ...current, [proofId]: { status: "failed" } }))
                }
              />
              <OrderItems order={order} />
              <CustomerCard order={order} />
              <InvoiceForm
                orderId={order.id}
                sold={SOLD.has(order.estado)}
                onChanged={() => setDocumentsVersion((n) => n + 1)}
              />
              <DocumentsCard orderId={order.id} sold={SOLD.has(order.estado)} refreshKey={documentsVersion} />
              <OrderReturns orderId={order.id} sold={SOLD.has(order.estado)} />
            </div>
            <div className="min-w-0 space-y-5">
              <ReviewPanel
                order={order}
                busy={busy}
                error={actionError}
                onConfirm={(step) =>
                  void act(() => api.orders.confirm(order.id, step || undefined), "Pedido confirmado.")
                }
                onReject={(motivo) =>
                  void act(() => api.orders.reject(order.id, motivo), "Pedido rechazado.")
                }
                onMove={(estado: OperatorState, motivo?: string) =>
                  void act(
                    () => api.orders.setStatus(order.id, estado, motivo),
                    motivo ? "Pedido anulado." : "Estado actualizado.",
                  )
                }
              />
              <HistoryCard order={order} />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
