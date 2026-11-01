"use client";

// Order Timeline — the PO → DN → Invoice chain for the order a document belongs
// to, with the reconciled PO status. Backed by GET /documents/{id}/order, which
// computes the reconciled fulfilment status server-side (order_reconciliation).
// Renders nothing when the document isn't linked to any other document.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { formatMoney } from "@/lib/format";
import type { AppLanguage } from "@/lib/i18n";

type OrderDoc = {
  document_id: string;
  document_type: string;
  date?: string;
  supplier_name?: string;
  amount?: string | number;
  currency?: string;
  po_status?: string | null;
  dn_status?: string | null;
  invoice_status?: string | null;
};

type OrderLine = { description: string; ordered: number; delivered: number };

type OrderView = {
  order_id: string;
  documents: OrderDoc[];
  po_status: string | null;
  delivery_stage: "none" | "partial" | "full";
  // "quantity": fulfilment compared PO line items with delivered quantities;
  // "status": fell back to DN statuses (no quantities to compare).
  fulfilment_mode?: "quantity" | "status";
  lines?: OrderLine[];
  unmatched_items?: { description: string; quantity: number }[];
  invoiced: boolean;
  paid: boolean;
  counts: { po: number; dn: number; invoice: number };
};

// status → semantic colour token
function statusColor(s?: string | null): { c: string; bg: string; bd: string } {
  const v = (s || "").toLowerCase();
  if (["fulfilled", "delivered", "paid", "received"].includes(v))
    return { c: "var(--success)", bg: "var(--success-tint)", bd: "var(--success-border)" };
  if (v.includes("partial"))
    return { c: "var(--warn)", bg: "var(--warn-tint)", bd: "var(--warn-border)" };
  if (["rejected", "cancelled", "failed", "returned", "overdue", "delayed"].includes(v))
    return { c: "var(--danger)", bg: "var(--danger-tint)", bd: "var(--danger-border)" };
  return { c: "var(--info)", bg: "var(--info-tint)", bd: "var(--info-border)" };
}

const STAGE_META: Record<string, { icon: string; en: string; si: string }> = {
  po:      { icon: "shopping_cart",  en: "Purchase Order", si: "මිලදී ගැනීමේ ඇණවුම" },
  dn:      { icon: "local_shipping", en: "Delivery",       si: "බෙදාහැරීම" },
  invoice: { icon: "description",    en: "Invoice",        si: "ඉන්වොයිස්" },
};

function StatusBadge({ status, lang }: { status?: string | null; lang: AppLanguage }) {
  if (!status || status === "NULL") return null;
  const { c, bg, bd } = statusColor(status);
  const label = status.replace(/_/g, " ");
  return (
    <span
      className="rounded-md px-2 py-[2px] text-[10px] font-bold uppercase tracking-wide"
      style={{ color: c, background: bg, border: `1px solid ${bd}` }}
    >
      {label}
    </span>
  );
}

export default function OrderTimeline({
  documentId, backendUrl, lang, refreshKey = 0,
}: {
  documentId: string;
  backendUrl: string;
  lang: AppLanguage;
  refreshKey?: number;
}) {
  const router = useRouter();
  const [order, setOrder] = useState<OrderView | null>(null);

  useEffect(() => {
    let cancelled = false;
    const token = localStorage.getItem("token") || sessionStorage.getItem("token") || "";
    fetch(`${backendUrl}/documents/${encodeURIComponent(documentId)}/order`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
    })
      .then((r) => r.json())
      .then((d) => { if (!cancelled && d.success) setOrder(d.order); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [documentId, backendUrl, refreshKey]);

  // No order link → render nothing (most receipts / standalone docs).
  if (!order || order.documents.length <= 1) return null;

  const byType = (t: string) => order.documents.filter((d) => d.document_type === t);
  const stages: Array<{ key: string; docs: OrderDoc[]; done: boolean; stageStatus?: string | null }> = [
    { key: "po", docs: byType("po"), done: order.counts.po > 0, stageStatus: order.po_status },
    {
      key: "dn", docs: byType("dn"),
      done: order.delivery_stage === "full",
      stageStatus: order.delivery_stage === "full" ? "delivered"
        : order.delivery_stage === "partial" ? "partially_delivered" : "pending",
    },
    { key: "invoice", docs: byType("invoice"), done: order.paid, stageStatus: order.paid ? "paid" : (order.invoiced ? "pending" : null) },
  ];

  return (
    <div className="mb-5 rounded-[18px] border p-4 sm:p-5" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-[18px]" style={{ color: "var(--brand-mid)" }}>account_tree</span>
          <p className="text-[14px] font-extrabold text-[var(--text-1)]">
            {lang === "si" ? "ඇණවුම් කාලරේඛාව" : "Order Timeline"}
          </p>
          <span className="rounded-md px-2 py-[2px] text-[11px] font-bold" style={{ background: "var(--surface-2)", color: "var(--text-2)" }}>
            {order.order_id}
          </span>
        </div>
        {order.po_status && (
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-[var(--text-3)]">{lang === "si" ? "ඇණවුම් තත්ත්වය" : "PO status"}</span>
            <StatusBadge status={order.po_status} lang={lang} />
          </div>
        )}
      </div>

      {/* Stepper */}
      <div className="flex items-stretch gap-1">
        {stages.map((st, i) => {
          const meta = STAGE_META[st.key];
          const has = st.docs.length > 0;
          const col = st.done ? "var(--success)" : has ? "var(--brand-mid)" : "var(--text-3)";
          return (
            <div key={st.key} className="flex flex-1 items-start gap-1">
              <div className="flex min-w-0 flex-1 flex-col items-center text-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full"
                  style={{ background: st.done ? "var(--success-tint)" : has ? "var(--brand-tint)" : "var(--surface-2)", border: `1.5px solid ${col}` }}>
                  <span className="material-symbols-outlined text-[20px]" style={{ color: col }}>
                    {st.done ? "check_circle" : meta.icon}
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] font-bold" style={{ color: has ? "var(--text-1)" : "var(--text-3)" }}>
                  {lang === "si" ? meta.si : meta.en}
                </p>
                {st.stageStatus && <div className="mt-1"><StatusBadge status={st.stageStatus} lang={lang} /></div>}
                {/* Clickable docs in this stage */}
                <div className="mt-1.5 flex flex-col items-center gap-1">
                  {st.docs.map((d) => (
                    <button key={d.document_id} onClick={() => router.push(`/analysis/${d.document_id}`)}
                      className="max-w-full truncate rounded-md px-2 py-[3px] text-[11px] font-semibold transition hover:opacity-80"
                      style={{ background: "var(--surface-2)", color: "var(--brand-mid)", border: "1px solid var(--border)" }}
                      title={`${d.document_id}${d.amount ? " · " + formatMoney(d.amount, d.currency) : ""}`}>
                      {d.document_id}
                    </button>
                  ))}
                  {!has && <span className="text-[10px] text-[var(--text-3)]">{lang === "si" ? "නැත" : "—"}</span>}
                </div>
              </div>
              {/* Connector line between stages */}
              {i < stages.length - 1 && (
                <div className="mt-5 h-[2px] w-full max-w-[40px] shrink-0 self-start"
                  style={{ background: st.done ? "var(--success)" : "var(--border)" }} />
              )}
            </div>
          );
        })}
      </div>

      {/* Per-item delivery progress — why the PO is (not yet) fulfilled */}
      {order.fulfilment_mode === "quantity" && order.lines && order.lines.length > 0 && (
        <div className="mt-4 border-t pt-3" style={{ borderColor: "var(--border)" }}>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.08em] text-[var(--text-3)]">
            {lang === "si" ? "බෙදා හරින ලද අයිතම" : "Items delivered"}
          </p>
          <ul className="space-y-2">
            {order.lines.map((line) => {
              const pct = line.ordered > 0 ? Math.min(line.delivered / line.ordered, 1) * 100 : 0;
              const complete = line.delivered >= line.ordered;
              return (
                <li key={line.description}>
                  <div className="flex items-center justify-between gap-3 text-[12px]">
                    <span className="min-w-0 truncate text-[var(--text-2)]">{line.description}</span>
                    <span className="shrink-0 font-semibold tabular-nums"
                      style={{ color: complete ? "var(--success)" : "var(--text-1)" }}>
                      {line.delivered} / {line.ordered}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full" style={{ background: "var(--surface-2)" }}>
                    <div className="h-full rounded-full"
                      style={{ width: `${pct}%`, background: complete ? "var(--success)" : "var(--warn)" }} />
                  </div>
                </li>
              );
            })}
          </ul>
          {order.unmatched_items && order.unmatched_items.length > 0 && (
            <p className="mt-3 rounded-lg px-3 py-2 text-[12px]"
              style={{ background: "var(--warn-tint)", border: "1px solid var(--warn-border)", color: "var(--warn)" }}>
              {lang === "si"
                ? "PO එකේ නොමැති බෙදාහැරීම් අයිතම: "
                : "Delivered items not on the PO: "}
              {order.unmatched_items.map((u) => `${u.description} (${u.quantity})`).join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
