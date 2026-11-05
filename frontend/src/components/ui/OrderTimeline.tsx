"use client";

// Order chain — the linked PO → Delivery Note(s) → Invoice(s) → Receipt(s)
// around one document, backed by GET /documents/{id}/order (document_links.py
// links them; order_matching.py does the three-way match).
//
// Shows: the chain as a stepper, plain-language issues (partial delivery,
// over-billing, price differences, unpaid balances), a line table
// (ordered / delivered / invoiced, PO vs invoice price), invoice payments,
// suggested links to confirm or dismiss, and a picker to link a document by
// hand. Documents that aren't linked yet just get the "Link a document" entry.

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { formatMoney } from "@/lib/format";
import { confirmDialog } from "@/lib/confirm";
import type { AppLanguage } from "@/lib/i18n";

type ChainDoc = {
  document_id: string;
  document_type: string;
  number?: string;
  date?: string | null;
  supplier_name?: string;
  amount?: number | string;
  currency?: string;
};
type Line = {
  description: string;
  ordered: number | null;
  delivered: number;
  invoiced: number;
  po_unit_price: number | null;
  invoice_unit_price: number | null;
  delivery_status: "none" | "partial" | "full" | "over";
};
type InvoicePay = {
  document_id: string; number: string; total: number; paid: number; balance: number;
  status: "paid" | "partially_paid" | "unpaid"; side: "purchase" | "sale"; receipts: string[];
};
type Issue = { code: string; severity: "info" | "warning" | "error"; documents: string[]; message_en: string; message_si: string };
type Link = { from: string; to: string; link_type: string; status: string; confidence: number; reasons: string[] };
type Suggestion = Link & { other: ChainDoc };
type OrderView = {
  order_id: string;
  order_status: string;
  po_status: string | null;
  delivery_stage: "none" | "partial" | "full";
  invoiced_stage: "none" | "partial" | "full";
  payment_stage: "none" | "partial" | "full";
  documents: ChainDoc[];
  lines: Line[];
  invoices: InvoicePay[];
  totals: { ordered: number; invoiced: number; paid: number; balance_due: number; currency: string };
  counts: { po: number; dn: number; invoice: number; receipt: number };
  issues: Issue[];
  links: Link[];
  suggestions: Suggestion[];
};
type Candidate = ChainDoc & { score: number; reasons: string[] };

const LINKABLE = new Set(["po", "dn", "invoice", "receipt"]);

const TYPE_META: Record<string, { icon: string; en: string; si: string }> = {
  po:      { icon: "shopping_cart",  en: "Purchase Order", si: "මිලදී ගැනීමේ ඇණවුම" },
  dn:      { icon: "local_shipping", en: "Delivery Note",  si: "බෙදාහැරීමේ සටහන" },
  invoice: { icon: "description",    en: "Invoice",        si: "ඉන්වොයිස්" },
  receipt: { icon: "receipt_long",   en: "Receipt",        si: "රිසිට්පත" },
};

const ORDER_STATUS: Record<string, { en: string; si: string; tone: Tone }> = {
  paid:                { en: "Paid",             si: "ගෙවා ඇත",                tone: "success" },
  partially_paid:      { en: "Partly paid",      si: "අර්ධව ගෙවා ඇත",          tone: "warn" },
  invoiced:            { en: "Invoiced",         si: "ඉන්වොයිස් කර ඇත",        tone: "info" },
  delivered:           { en: "Delivered",        si: "ලැබී ඇත",                tone: "success" },
  partially_delivered: { en: "Partly delivered", si: "අර්ධව ලැබී ඇත",          tone: "warn" },
  ordered:             { en: "Ordered",          si: "ඇණවුම් කර ඇත",           tone: "info" },
  open:                { en: "Open",             si: "විවෘත",                  tone: "info" },
};

const STAGE_LABEL = {
  delivery: { none: ["Not delivered", "නොලැබුණි"], partial: ["Partly delivered", "අර්ධව ලැබී ඇත"], full: ["Delivered", "ලැබී ඇත"] },
  invoice:  { none: ["Not invoiced", "ඉන්වොයිස් කර නැත"], partial: ["Partly invoiced", "අර්ධව ඉන්වොයිස් කර ඇත"], full: ["Invoiced", "ඉන්වොයිස් කර ඇත"] },
  payment:  { none: ["Unpaid", "නොගෙවූ"], partial: ["Partly paid", "අර්ධව ගෙවා ඇත"], full: ["Paid", "ගෙවා ඇත"] },
} as const;

type Tone = "success" | "warn" | "danger" | "info" | "muted";
const TONE: Record<Tone, { c: string; bg: string; bd: string }> = {
  success: { c: "var(--success)", bg: "var(--success-tint)", bd: "var(--success-border)" },
  warn:    { c: "var(--warn)",    bg: "var(--warn-tint)",    bd: "var(--warn-border)" },
  danger:  { c: "var(--danger)",  bg: "var(--danger-tint)",  bd: "var(--danger-border)" },
  info:    { c: "var(--info)",    bg: "var(--info-tint)",    bd: "var(--info-border)" },
  muted:   { c: "var(--text-3)",  bg: "var(--surface-2)",    bd: "var(--border)" },
};
const stageTone = (s: "none" | "partial" | "full"): Tone => (s === "full" ? "success" : s === "partial" ? "warn" : "muted");

function Pill({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  const t = TONE[tone];
  return (
    <span className="inline-flex items-center rounded-md px-2 py-[2px] text-[10px] font-bold uppercase tracking-wide"
      style={{ color: t.c, background: t.bg, border: `1px solid ${t.bd}` }}>
      {children}
    </span>
  );
}

const qty = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 2 });

function getToken() {
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

export default function OrderTimeline({
  documentId, backendUrl, lang, refreshKey = 0, documentType,
}: {
  documentId: string;
  backendUrl: string;
  lang: AppLanguage;
  refreshKey?: number;
  /** Lets an unlinked PO / DN / invoice / receipt still offer "Link a document". */
  documentType?: string;
}) {
  const router = useRouter();
  const si = lang === "si";
  const [order, setOrder] = useState<OrderView | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [showLines, setShowLines] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetch(`${backendUrl}/documents/${encodeURIComponent(documentId)}/order`, {
      headers: { Authorization: `Bearer ${getToken()}` }, cache: "no-store",
    })
      .then((r) => r.json())
      .then((d) => { if (!cancelled && d.success) setOrder(d.order); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoaded(true); });
    return () => { cancelled = true; };
  }, [documentId, backendUrl, refreshKey]);

  const decide = useCallback(async (targetId: string, decision: "confirmed" | "rejected", scope: "pair" | "order" = "pair") => {
    setBusy(targetId + decision);
    setError("");
    try {
      const res = await fetch(`${backendUrl}/documents/${encodeURIComponent(documentId)}/links`, {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}`, "Content-Type": "application/json" },
        body: JSON.stringify({ target_id: targetId, decision, scope }),
      });
      const d = await res.json();
      if (!res.ok || !d.success) throw new Error(d.detail || "Failed");
      setOrder(d.order);
      setPickerOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [backendUrl, documentId]);

  const unlink = async (doc: ChainDoc) => {
    const ok = await confirmDialog({
      title: si ? "මෙම ඇණවුමෙන් ඉවත් කරන්නද?" : "Remove from this order?",
      message: si
        ? `${doc.number || doc.document_id} මෙම ඇණවුමට අයත් නොවන බව සලකුණු කෙරේ. එය නැවත යෝජනා නොකෙරේ.`
        : `${doc.number || doc.document_id} will be marked as not part of this order, and won't be suggested again.`,
      confirmLabel: si ? "ඉවත් කරන්න" : "Remove link",
      variant: "danger",
    });
    if (ok) await decide(doc.document_id, "rejected", "order");
  };

  const linkable = LINKABLE.has((documentType || "").toLowerCase());
  if (!loaded) return null;

  // ── Not linked yet ─────────────────────────────────────────────────────────
  if (!order) {
    if (!linkable) return null;
    return (
      <>
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-[18px] border px-4 py-3"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
          <div className="flex items-center gap-2.5 text-[13px] text-[var(--text-2)]">
            <span className="material-symbols-outlined" style={{ fontSize: 20, color: "var(--text-3)" }}>link_off</span>
            {si ? "තවම ඇණවුමකට සම්බන්ධ කර නැත." : "Not linked to an order yet."}
          </div>
          <button onClick={() => setPickerOpen(true)}
            className="flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-[12px] font-bold transition hover:opacity-85"
            style={{ background: "var(--brand-tint)", color: "var(--brand-mid)" }}>
            <span className="material-symbols-outlined" style={{ fontSize: 17 }}>add_link</span>
            {si ? "ලේඛනයක් සම්බන්ධ කරන්න" : "Link a document"}
          </button>
        </div>
        {pickerOpen && (
          <LinkPicker documentId={documentId} backendUrl={backendUrl} lang={lang} busy={busy}
            onPick={(id) => decide(id, "confirmed")} onClose={() => setPickerOpen(false)} error={error} />
        )}
      </>
    );
  }

  const byType = (t: string) => order.documents.filter((d) => d.document_type === t);
  const status = ORDER_STATUS[order.order_status] ?? ORDER_STATUS.open;
  const cur = order.totals.currency || "LKR";
  const hasChain = order.documents.length > 1;

  const stages: { key: string; docs: ChainDoc[]; done: boolean; label?: string; tone: Tone }[] = [
    { key: "po", docs: byType("po"), done: byType("po").length > 0,
      label: order.po_status ? order.po_status.replace(/_/g, " ") : undefined, tone: byType("po").length ? "info" : "muted" },
    { key: "dn", docs: byType("dn"), done: order.delivery_stage === "full",
      label: STAGE_LABEL.delivery[order.delivery_stage][si ? 1 : 0], tone: stageTone(order.delivery_stage) },
    { key: "invoice", docs: byType("invoice"), done: order.invoiced_stage === "full",
      label: STAGE_LABEL.invoice[order.invoiced_stage][si ? 1 : 0], tone: stageTone(order.invoiced_stage) },
    { key: "receipt", docs: byType("receipt"), done: order.payment_stage === "full",
      label: order.counts.invoice ? STAGE_LABEL.payment[order.payment_stage][si ? 1 : 0] : undefined,
      tone: stageTone(order.payment_stage) },
  ];

  const issueTone = (s: Issue["severity"]): Tone => (s === "error" ? "danger" : s === "warning" ? "warn" : "info");
  const issueIcon = (s: Issue["severity"]) => (s === "error" ? "error" : s === "warning" ? "warning" : "info");

  return (
    <div className="ui-card mb-5 overflow-hidden" data-no-swipe>
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 pt-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 20, color: "var(--brand-mid)" }}>account_tree</span>
          <p className="text-[15px] font-extrabold text-[var(--text-1)]">{si ? "ඇණවුම් දාමය" : "Order chain"}</p>
          {hasChain && (
            <span className="truncate rounded-md px-2 py-[2px] text-[11px] font-bold"
              style={{ background: "var(--surface-2)", color: "var(--text-2)" }}>{order.order_id}</span>
          )}
          {hasChain && <Pill tone={status.tone}>{si ? status.si : status.en}</Pill>}
          {busy && (
            <span className="flex items-center gap-1 text-[11px] font-semibold text-[var(--text-3)]" role="status">
              <span className="material-symbols-outlined animate-spin" style={{ fontSize: 15 }}>progress_activity</span>
              {si ? "යාවත්කාලීන වෙමින්…" : "Updating…"}
            </span>
          )}
        </div>
        <button onClick={() => setPickerOpen(true)}
          className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] font-bold transition hover:bg-[var(--brand-tint)]"
          style={{ color: "var(--brand-mid)" }}>
          <span className="material-symbols-outlined" style={{ fontSize: 17 }}>add_link</span>
          {si ? "සම්බන්ධ කරන්න" : "Link a document"}
        </button>
      </div>

      {/* Stepper */}
      {hasChain && (
        <div className="overflow-x-auto px-4 pb-1 pt-4 sm:px-5">
          <div className="flex min-w-[520px] items-start">
            {stages.map((st, i) => {
              const meta = TYPE_META[st.key];
              const has = st.docs.length > 0;
              const col = st.done ? "var(--success)" : has ? "var(--brand-mid)" : "var(--text-3)";
              return (
                <div key={st.key} className="flex flex-1 items-start">
                  <div className="flex min-w-0 flex-1 flex-col items-center text-center">
                    <div className="flex h-10 w-10 items-center justify-center rounded-full"
                      style={{ background: st.done ? "var(--success-tint)" : has ? "var(--brand-tint)" : "var(--surface-2)", border: `1.5px solid ${col}` }}>
                      <span className="material-symbols-outlined" style={{ fontSize: 20, color: col }}>
                        {st.done ? "check_circle" : meta.icon}
                      </span>
                    </div>
                    <p className="mt-1.5 text-[11px] font-bold" style={{ color: has ? "var(--text-1)" : "var(--text-3)" }}>
                      {st.key === "receipt" ? (si ? "ගෙවීම" : "Payment") : st.key === "dn" ? (si ? "බෙදාහැරීම" : "Delivery") : si ? meta.si : meta.en}
                    </p>
                    {st.label && <div className="mt-1"><Pill tone={st.tone}>{st.label}</Pill></div>}
                    <div className="mt-1.5 flex flex-col items-center gap-1">
                      {st.docs.map((d) => {
                        const isMe = d.document_id === documentId;
                        const removable = !isMe;
                        return (
                          <span key={d.document_id} className="flex max-w-[150px] items-center gap-0.5 rounded-md text-[11px] font-semibold"
                            style={{ background: isMe ? "var(--brand-tint)" : "var(--surface-2)", border: `1px solid ${isMe ? "var(--brand-mid)" : "var(--border)"}` }}>
                            <button onClick={() => !isMe && router.push(`/analysis/${d.document_id}`)}
                              className="truncate px-2 py-[3px] transition hover:opacity-80" style={{ color: "var(--brand-mid)" }}
                              title={`${d.number || d.document_id}${d.amount ? " · " + formatMoney(d.amount, d.currency) : ""}`}>
                              {d.number || d.document_id}{isMe ? (si ? " (මෙය)" : " (this)") : ""}
                            </button>
                            {removable && (
                              <button onClick={() => unlink(d)} disabled={!!busy}
                                aria-label={si ? "සම්බන්ධය ඉවත් කරන්න" : "Remove link"}
                                className="pr-1 text-[var(--text-3)] transition hover:text-[var(--danger)]">
                                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
                              </button>
                            )}
                          </span>
                        );
                      })}
                      {!has && <span className="text-[10px] text-[var(--text-3)]">—</span>}
                    </div>
                  </div>
                  {i < stages.length - 1 && (
                    <div className="mt-5 h-[2px] w-6 shrink-0 sm:w-10" style={{ background: st.done ? "var(--success)" : "var(--border)" }} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Issues */}
      {order.issues.length > 0 && (
        <div className="space-y-2 px-4 pt-4 sm:px-5">
          {order.issues.map((iss, i) => {
            const t = TONE[issueTone(iss.severity)];
            return (
              <div key={i} className="flex items-start gap-2.5 rounded-xl px-3 py-2.5 text-[13px] leading-5"
                style={{ background: t.bg, border: `1px solid ${t.bd}`, color: t.c }}>
                <span className="material-symbols-outlined mt-px shrink-0" style={{ fontSize: 18 }}>{issueIcon(iss.severity)}</span>
                <span>{si ? iss.message_si : iss.message_en}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Lines: ordered / delivered / invoiced */}
      {hasChain && order.lines.length > 0 && (
        <div className="px-4 pt-4 sm:px-5">
          <button onClick={() => setShowLines((v) => !v)}
            className="mb-2 flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)]">
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>{showLines ? "expand_less" : "expand_more"}</span>
            {si ? "අයිතම සැසඳීම" : "Item check"} ({order.lines.length})
          </button>
          {showLines && (
            <div className="overflow-x-auto rounded-xl border" style={{ borderColor: "var(--border)" }}>
              <table className="w-full min-w-[560px] text-[12px]">
                <thead style={{ background: "var(--surface-2)" }}>
                  <tr className="text-left text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--text-3)]">
                    <th className="px-3 py-2">{si ? "අයිතමය" : "Item"}</th>
                    <th className="px-3 py-2 text-right">{si ? "ඇණවුම්" : "Ordered"}</th>
                    <th className="px-3 py-2">{si ? "ලැබුණු" : "Delivered"}</th>
                    <th className="px-3 py-2 text-right">{si ? "බිල් කළ" : "Invoiced"}</th>
                    <th className="px-3 py-2 text-right">{si ? "PO මිල" : "PO price"}</th>
                    <th className="px-3 py-2 text-right">{si ? "ඉන්වොයිස් මිල" : "Invoice price"}</th>
                  </tr>
                </thead>
                <tbody>
                  {order.lines.map((l, i) => {
                    const pct = l.ordered ? Math.min(100, Math.round((l.delivered / l.ordered) * 100)) : l.delivered ? 100 : 0;
                    const tone: Tone = l.delivery_status === "full" ? "success" : l.delivery_status === "over" ? "danger"
                      : l.delivery_status === "partial" ? "warn" : "muted";
                    const basis = order.counts.dn ? l.delivered : l.ordered ?? 0;
                    const overBilled = order.counts.invoice > 0 && l.invoiced > (basis || 0) + 1e-9 && (order.counts.dn > 0 || l.ordered);
                    const priceDiff = l.po_unit_price && l.invoice_unit_price && Math.abs(l.invoice_unit_price - l.po_unit_price) > Math.max(0.5, 0.005 * l.po_unit_price);
                    return (
                      <tr key={i} className="border-t" style={{ borderColor: "var(--border)" }}>
                        <td className="max-w-[220px] px-3 py-2 font-semibold text-[var(--text-1)]">{l.description}</td>
                        <td className="px-3 py-2 text-right text-[var(--text-2)]">{qty(l.ordered)}</td>
                        <td className="px-3 py-2">
                          {order.counts.dn ? (
                            <div className="flex min-w-[110px] items-center gap-2">
                              <div className="h-1.5 flex-1 overflow-hidden rounded-full" style={{ background: "var(--surface-2)" }}>
                                <div className="h-full rounded-full" style={{ width: `${pct}%`, background: TONE[tone].c }} />
                              </div>
                              <span className="font-semibold" style={{ color: TONE[tone].c }}>{qty(l.delivered)}</span>
                            </div>
                          ) : <span className="text-[var(--text-3)]">—</span>}
                        </td>
                        <td className="px-3 py-2 text-right font-semibold" style={{ color: overBilled ? "var(--danger)" : "var(--text-2)" }}>
                          {order.counts.invoice ? qty(l.invoiced) : "—"}
                        </td>
                        <td className="px-3 py-2 text-right text-[var(--text-2)]">{l.po_unit_price ? formatMoney(l.po_unit_price, cur) : "—"}</td>
                        <td className="px-3 py-2 text-right font-semibold" style={{ color: priceDiff ? "var(--warn)" : "var(--text-2)" }}>
                          {l.invoice_unit_price ? formatMoney(l.invoice_unit_price, cur) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Payments */}
      {order.invoices.length > 0 && (
        <div className="grid gap-2 px-4 pt-4 sm:grid-cols-2 sm:px-5">
          {order.invoices.map((inv) => {
            const tone: Tone = inv.status === "paid" ? "success" : inv.status === "partially_paid" ? "warn" : "muted";
            const pct = inv.total ? Math.min(100, Math.round(((inv.status === "paid" ? inv.total : inv.paid) / inv.total) * 100)) : 0;
            return (
              <div key={inv.document_id} className="rounded-xl border px-3 py-2.5" style={{ borderColor: "var(--border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[12px] font-bold text-[var(--text-1)]">{inv.number}</span>
                  <Pill tone={tone}>{STAGE_LABEL.payment[inv.status === "paid" ? "full" : inv.status === "partially_paid" ? "partial" : "none"][si ? 1 : 0]}</Pill>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full" style={{ background: "var(--surface-2)" }}>
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: TONE[tone].c }} />
                </div>
                <p className="mt-1.5 text-[11px] text-[var(--text-3)]">
                  {si ? "ගෙවූ" : "Paid"} {formatMoney(inv.status === "paid" ? inv.total : inv.paid, cur)} / {formatMoney(inv.total, cur)}
                  {inv.balance > 0 && <> · <span className="font-semibold" style={{ color: "var(--warn)" }}>
                    {si ? "ශේෂය" : "Balance"} {formatMoney(inv.balance, cur)}</span></>}
                </p>
              </div>
            );
          })}
        </div>
      )}

      {/* Suggestions */}
      {order.suggestions.length > 0 && (
        <div className="space-y-2 px-4 pt-4 sm:px-5">
          {order.suggestions.map((s) => {
            const other = s.other;
            const meta = TYPE_META[other.document_type] ?? TYPE_META.invoice;
            return (
              <div key={`${s.from}-${s.to}`} className="flex flex-wrap items-center gap-3 rounded-xl border border-dashed px-3 py-2.5"
                style={{ borderColor: "var(--brand-mid)", background: "var(--brand-tint)" }}>
                <span className="material-symbols-outlined" style={{ fontSize: 20, color: "var(--brand-mid)" }}>{meta.icon}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-bold text-[var(--text-1)]">
                    {si ? "යෝජිත සම්බන්ධය: " : "Suggested link: "}{other.number || other.document_id}
                    <span className="ml-1.5 font-normal text-[var(--text-3)]">
                      {si ? meta.si : meta.en}{other.supplier_name ? ` · ${other.supplier_name}` : ""}{other.amount ? ` · ${formatMoney(other.amount, other.currency)}` : ""}
                    </span>
                  </p>
                  <p className="mt-0.5 text-[11px] text-[var(--text-3)]">
                    {Math.round(s.confidence * 100)}% {si ? "ගැළපීම" : "match"} · {s.reasons.join(" · ")}
                  </p>
                </div>
                <div className="flex gap-1.5">
                  <button onClick={() => decide(other.document_id, "confirmed")} disabled={!!busy}
                    className="rounded-lg px-3 py-1.5 text-[12px] font-bold text-white transition hover:opacity-90 disabled:opacity-50"
                    style={{ background: "var(--brand-mid)" }}>
                    {si ? "සම්බන්ධ කරන්න" : "Link"}
                  </button>
                  <button onClick={() => decide(other.document_id, "rejected")} disabled={!!busy}
                    className="rounded-lg px-3 py-1.5 text-[12px] font-semibold transition hover:bg-[var(--surface)] disabled:opacity-50"
                    style={{ border: "1px solid var(--border)", color: "var(--text-2)" }}>
                    {si ? "අදාළ නැත" : "Not related"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Totals */}
      {hasChain && (
        <div className="mt-4 grid grid-cols-2 border-t sm:grid-cols-4" style={{ borderColor: "var(--border)" }}>
          {[
            [si ? "ඇණවුම්" : "Ordered", order.totals.ordered],
            [si ? "බිල් කළ" : "Invoiced", order.totals.invoiced],
            [si ? "ගෙවූ" : "Paid", order.totals.paid],
            [si ? "ගෙවිය යුතු ශේෂය" : "Balance due", order.totals.balance_due],
          ].map(([label, v], i) => (
            <div key={i} className="px-4 py-3 sm:px-5" style={{ borderLeft: i % 2 || i === 2 ? "1px solid var(--border)" : undefined }}>
              <p className="text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--text-3)]">{label as string}</p>
              <p className="mt-0.5 text-[14px] font-extrabold text-[var(--text-1)]">{v ? formatMoney(v as number, cur) : "—"}</p>
            </div>
          ))}
        </div>
      )}

      {error && <p className="px-4 pb-3 pt-2 text-[12px] text-red-600 sm:px-5">{error}</p>}
      {!hasChain && <div className="pb-4" />}

      {pickerOpen && (
        <LinkPicker documentId={documentId} backendUrl={backendUrl} lang={lang} busy={busy}
          onPick={(id) => decide(id, "confirmed")} onClose={() => setPickerOpen(false)} error={error}
          exclude={new Set(order.documents.map((d) => d.document_id))} />
      )}
    </div>
  );
}

// ── "Link a document" picker ────────────────────────────────────────────────
function LinkPicker({
  documentId, backendUrl, lang, busy, onPick, onClose, error, exclude,
}: {
  documentId: string; backendUrl: string; lang: AppLanguage; busy: string | null;
  onPick: (id: string) => void; onClose: () => void; error: string; exclude?: Set<string>;
}) {
  const si = lang === "si";
  const [items, setItems] = useState<Candidate[] | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    fetch(`${backendUrl}/documents/${encodeURIComponent(documentId)}/link-candidates`, {
      headers: { Authorization: `Bearer ${getToken()}` }, cache: "no-store",
    })
      .then((r) => r.json())
      .then((d) => setItems(d.success ? d.candidates : []))
      .catch(() => setItems([]));
  }, [backendUrl, documentId]);

  const shown = (items || []).filter((c) => !exclude?.has(c.document_id)).filter((c) => {
    const s = q.trim().toLowerCase();
    return !s || [c.document_id, c.number, c.supplier_name, c.document_type].some((v) => String(v || "").toLowerCase().includes(s));
  });

  const strength = (score: number) =>
    score >= 0.8 ? { en: "Strong match", si: "ශක්තිමත් ගැළපීමක්", tone: "success" as Tone }
      : score >= 0.45 ? { en: "Possible match", si: "ගැළපිය හැක", tone: "warn" as Tone }
        : { en: "Weak match", si: "දුර්වල ගැළපීමක්", tone: "muted" as Tone };

  return (
    <div data-no-swipe className="fixed inset-0 z-[60] flex items-end justify-center bg-black/45 backdrop-blur-sm sm:items-center sm:px-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="rise-in flex max-h-[85vh] w-full max-w-[560px] flex-col overflow-hidden rounded-t-3xl shadow-2xl sm:rounded-3xl"
        style={{ background: "var(--surface)", border: "1px solid var(--border)", paddingBottom: "var(--safe-bottom)" }}>
        <div className="flex items-center justify-between gap-3 px-5 pb-3 pt-5">
          <div>
            <p className="text-[16px] font-extrabold text-[var(--text-1)]">{si ? "ලේඛනයක් සම්බන්ධ කරන්න" : "Link a document"}</p>
            <p className="text-[12px] text-[var(--text-3)]">
              {si ? "මෙම ඇණවුමට අයත් PO, බෙදාහැරීම, ඉන්වොයිසිය හෝ රිසිට්පත තෝරන්න." : "Pick the PO, delivery note, invoice or receipt that belongs to this order."}
            </p>
          </div>
          <button onClick={onClose} aria-label={si ? "වසන්න" : "Close"}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition hover:bg-[var(--surface-2)]">
            <span className="material-symbols-outlined" style={{ fontSize: 20, color: "var(--text-2)" }}>close</span>
          </button>
        </div>
        <div className="px-5 pb-3">
          <input value={q} onChange={(e) => setQ(e.target.value)} autoFocus
            placeholder={si ? "අංකය, සැපයුම්කරු හෝ වර්ගය අනුව සොයන්න" : "Search by number, supplier or type"}
            className="field-input w-full rounded-xl border px-3.5 py-2.5 text-[13px]" />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
          {items === null && <p className="px-2 py-6 text-center text-[13px] text-[var(--text-3)]">{si ? "පූරණය වෙමින්…" : "Loading…"}</p>}
          {items !== null && shown.length === 0 && (
            <p className="px-2 py-6 text-center text-[13px] text-[var(--text-3)]">{si ? "සම්බන්ධ කළ හැකි ලේඛන නැත." : "No documents that can be linked."}</p>
          )}
          {shown.map((c) => {
            const meta = TYPE_META[c.document_type] ?? TYPE_META.invoice;
            const st = strength(c.score);
            return (
              <button key={c.document_id} onClick={() => onPick(c.document_id)} disabled={!!busy}
                className="flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition hover:bg-[var(--surface-2)] disabled:opacity-50">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg" style={{ background: "var(--brand-tint)" }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 19, color: "var(--brand-mid)" }}>{meta.icon}</span>
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-[13px] font-bold text-[var(--text-1)]">{c.number || c.document_id}</span>
                    {c.score > 0 && <Pill tone={st.tone}>{si ? st.si : st.en}</Pill>}
                  </span>
                  <span className="block truncate text-[11px] text-[var(--text-3)]">
                    {si ? meta.si : meta.en} · {c.document_id}{c.supplier_name ? ` · ${c.supplier_name}` : ""}
                    {c.date ? ` · ${c.date}` : ""}{c.amount ? ` · ${formatMoney(c.amount, c.currency)}` : ""}
                  </span>
                </span>
                <span className="material-symbols-outlined shrink-0" style={{ fontSize: 18, color: "var(--text-3)" }}>add_link</span>
              </button>
            );
          })}
        </div>
        {error && <p className="px-5 pb-3 text-[12px] text-red-600">{error}</p>}
      </div>
    </div>
  );
}
