"use client";
/**
 * Payables & Receivables Analysis.
 *
 * Page is split into two independent horizontal sections, each with its own tabs:
 *   TOP    — Payables:    Outstanding Invoices | Committed POs | Settled
 *   BOTTOM — Receivables: Outstanding | Settled | Overdue (30+ days)
 *
 * PO + Invoice double-counting avoidance: a PO in 'fulfilled' state is excluded
 * from Committed because its Invoice should already exist in Outstanding.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import PageShell from "@/components/layout/PageShell";
import { getStoredLanguage, AppLanguage } from "@/lib/i18n";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type PayableRow = {
  document_id: string; document_type: string; supplier_name: string;
  amount: number; currency: string; date: string; days_old: number;
  paid_status: string; po_status: string; order_id: string; notes: string;
};
type ReceivableRow = {
  document_id: string; document_type: string; supplier_name: string;
  amount: number; currency: string; date: string; days_old: number;
  received_status: string;
};
type Summary = {
  outstanding_total: number; committed_total: number; settled_total: number;
  outstanding_count: number; committed_count: number; settled_count: number;
  recv_outstanding_total: number; recv_settled_total: number; recv_overdue_total: number;
  recv_outstanding_count: number; recv_settled_count: number; recv_overdue_count: number;
};

type PayTab  = "outstanding" | "committed" | "settled";
type RecvTab = "recv_outstanding" | "recv_settled" | "recv_overdue";

function getToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

function StatusBadge({ status }: { status: string }) {
  if (!status || status === "NULL" || status === "") return null;
  const colors: Record<string, string> = {
    paid: "#16a34a", not_paid: "#ea6c0a", overdue: "#dc2626",
    received: "#16a34a", not_received: "#ea6c0a",
    pending: "#ea6c0a", approved: "#2252b5", fulfilled: "#16a34a",
    partially_delivered: "#0891b2", cancelled: "#dc2626", rejected: "#dc2626",
  };
  const c = colors[status.toLowerCase()] || "#64748b";
  return (
    <span className="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase"
      style={{ background: `${c}15`, color: c }}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

function AgeBadge({ days }: { days: number }) {
  if (!days) return null;
  const color = days > 60 ? "#dc2626" : days > 30 ? "#ea6c0a" : "#64748b";
  return (
    <span className="text-[10px] font-semibold" style={{ color }}>
      {days}d old
    </span>
  );
}

// Pulsing placeholder for the two report sections (title + tab pills + table
// rows each), shown while the payables/receivables data is still fetching.
function ReportSkeleton() {
  return (
    <div className="space-y-8" aria-busy>
      {[0, 1].map((sec) => (
        <section key={sec}>
          <div className="mb-3 h-[15px] w-40 animate-pulse rounded" style={{ background: "var(--border)" }} aria-hidden />
          <div className="mb-4 flex gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-[34px] w-32 animate-pulse rounded-xl" style={{ background: "var(--border)" }} aria-hidden />
            ))}
          </div>
          <div className="space-y-3 rounded-2xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-[18px] w-full animate-pulse rounded" style={{ background: "var(--border)" }} aria-hidden />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function PayableTable({ rows, currency = "LKR", lang, router, emptyMsg }: {
  rows: PayableRow[]; currency?: string; lang: string;
  router: ReturnType<typeof useRouter>; emptyMsg: string;
}) {
  if (rows.length === 0) {
    return (
      <div className="rounded-2xl py-10 text-center text-[13px] text-[var(--text-3)]"
        style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        {emptyMsg}
      </div>
    );
  }

  return (
    <div className="rounded-2xl overflow-hidden" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="grid gap-3 px-5 py-3 text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--text-3)]"
        style={{ gridTemplateColumns: "1fr 1fr 100px 80px 80px", borderBottom: "1px solid var(--border)" }}>
        <span>{lang === "si" ? "ලේඛනය" : "Document"}</span>
        <span>{lang === "si" ? "සැපයුම්කරු" : "Supplier / Party"}</span>
        <span className="text-right">{lang === "si" ? "මුදල" : "Amount"}</span>
        <span className="text-center">{lang === "si" ? "දිනය" : "Date"}</span>
        <span className="text-center">{lang === "si" ? "තත්ත්වය" : "Status"}</span>
      </div>

      {rows.map((row, i) => (
        <button key={i} onClick={() => router.push(`/analysis/${row.document_id}`)}
          className="grid w-full gap-3 px-5 py-4 text-left transition hover:bg-[var(--surface-2)]"
          style={{ gridTemplateColumns: "1fr 1fr 100px 80px 80px", borderBottom: i < rows.length - 1 ? "1px solid var(--border)" : "none" }}>

          <div className="min-w-0">
            <p className="text-[13px] font-semibold text-[var(--text-1)]">{row.document_id}</p>
            <p className="text-[11px] uppercase font-medium" style={{ color: row.document_type === "po" ? "#7c3aed" : "#2252b5" }}>
              {row.document_type?.toUpperCase()}
              {row.order_id && row.order_id !== row.document_id && (
                <span className="ml-1 normal-case font-normal text-[var(--text-3)]">· {row.order_id}</span>
              )}
            </p>
          </div>

          <div className="min-w-0">
            <p className="truncate text-[13px] text-[var(--text-1)]">{row.supplier_name}</p>
            {row.notes && <p className="truncate text-[11px] text-[var(--text-3)]">{row.notes}</p>}
          </div>

          <div className="text-right">
            <p className="text-[14px] font-extrabold text-[var(--text-1)]">
              {row.currency || currency} {row.amount.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </p>
          </div>

          <div className="flex flex-col items-center gap-0.5">
            <p className="text-[11px] text-[var(--text-2)]">{row.date || "—"}</p>
            <AgeBadge days={row.days_old} />
          </div>

          <div className="flex items-center justify-center gap-1">
            <StatusBadge status={row.document_type === "po" ? row.po_status : row.paid_status} />
          </div>
        </button>
      ))}
    </div>
  );
}

function ReceivableTable({ rows, currency = "LKR", lang, router, emptyMsg }: {
  rows: ReceivableRow[]; currency?: string; lang: string;
  router: ReturnType<typeof useRouter>; emptyMsg: string;
}) {
  if (rows.length === 0) {
    return (
      <div className="rounded-2xl py-10 text-center text-[13px] text-[var(--text-3)]"
        style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        {emptyMsg}
      </div>
    );
  }

  return (
    <div className="rounded-2xl overflow-hidden" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="grid gap-3 px-5 py-3 text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--text-3)]"
        style={{ gridTemplateColumns: "1fr 1fr 100px 80px 80px", borderBottom: "1px solid var(--border)" }}>
        <span>{lang === "si" ? "ලේඛනය" : "Document"}</span>
        <span>{lang === "si" ? "ගනුදෙනුකරු" : "Customer"}</span>
        <span className="text-right">{lang === "si" ? "මුදල" : "Amount"}</span>
        <span className="text-center">{lang === "si" ? "දිනය" : "Date"}</span>
        <span className="text-center">{lang === "si" ? "තත්ත්වය" : "Status"}</span>
      </div>

      {rows.map((row, i) => (
        <button key={i} onClick={() => router.push(`/analysis/${row.document_id}`)}
          className="grid w-full gap-3 px-5 py-4 text-left transition hover:bg-[var(--surface-2)]"
          style={{ gridTemplateColumns: "1fr 1fr 100px 80px 80px", borderBottom: i < rows.length - 1 ? "1px solid var(--border)" : "none" }}>
          <div className="min-w-0">
            <p className="text-[13px] font-semibold text-[var(--text-1)]">{row.document_id}</p>
            <p className="text-[11px] uppercase font-medium text-[#0891b2]">{row.document_type?.toUpperCase()}</p>
          </div>
          <div className="min-w-0">
            <p className="truncate text-[13px] text-[var(--text-1)]">{row.supplier_name}</p>
          </div>
          <div className="text-right">
            <p className="text-[14px] font-extrabold text-[var(--text-1)]">
              {row.currency || currency} {row.amount.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </p>
          </div>
          <div className="flex flex-col items-center gap-0.5">
            <p className="text-[11px] text-[var(--text-2)]">{row.date || "—"}</p>
            <AgeBadge days={row.days_old} />
          </div>
          <div className="flex items-center justify-center gap-1">
            <StatusBadge status={row.received_status} />
          </div>
        </button>
      ))}
    </div>
  );
}

export default function PayablesPage() {
  const router = useRouter();
  const [lang, setLang]       = useState<AppLanguage>("en");
  const [payTab, setPayTab]   = useState<PayTab>("outstanding");
  const [recvTab, setRecvTab] = useState<RecvTab>("recv_outstanding");
  const [data, setData] = useState<{
    outstanding: PayableRow[]; committed: PayableRow[]; settled: PayableRow[];
    recv_outstanding: ReceivableRow[]; recv_settled: ReceivableRow[]; recv_overdue: ReceivableRow[];
    summary: Summary;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState("");

  useEffect(() => {
    setLang(getStoredLanguage());
    const token = getToken();
    if (!token) { router.push("/login"); return; }
    fetch(`${BACKEND_URL}/reports/payables`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then(d => { if (!d.success) throw new Error(d.message); setData(d); })
      .catch(e => setError(e.message || "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

  const s = data?.summary;

  const PAY_TABS: { key: PayTab; label_en: string; label_si: string; count: number; total: number; color: string }[] = [
    { key: "outstanding", color: "#dc2626", label_en: "Outstanding Invoices", label_si: "ගෙවීමට ඇති ඉන්වොයිස්", count: s?.outstanding_count ?? 0, total: s?.outstanding_total ?? 0 },
    { key: "committed",   color: "#7c3aed", label_en: "Committed POs",       label_si: "ගෙවීමට ඇති PO",        count: s?.committed_count   ?? 0, total: s?.committed_total   ?? 0 },
    { key: "settled",     color: "#16a34a", label_en: "Settled",             label_si: "ගෙව්වා",               count: s?.settled_count     ?? 0, total: s?.settled_total     ?? 0 },
  ];

  const RECV_TABS: { key: RecvTab; label_en: string; label_si: string; count: number; total: number; color: string }[] = [
    { key: "recv_outstanding", color: "#0891b2", label_en: "Outstanding Invoices", label_si: "ලැබීමට ඇති ඉන්වොයිස්", count: s?.recv_outstanding_count ?? 0, total: s?.recv_outstanding_total ?? 0 },
    { key: "recv_settled",     color: "#16a34a", label_en: "Settled",             label_si: "ලැබුණා",              count: s?.recv_settled_count     ?? 0, total: s?.recv_settled_total     ?? 0 },
    { key: "recv_overdue",     color: "#dc2626", label_en: "Overdue (30+ days)",  label_si: "ප‍රමාද (දින 30+)",     count: s?.recv_overdue_count     ?? 0, total: s?.recv_overdue_total     ?? 0 },
  ];

  const payRows = payTab === "outstanding" ? data?.outstanding : payTab === "committed" ? data?.committed : data?.settled;
  const recvRows = recvTab === "recv_outstanding" ? data?.recv_outstanding : recvTab === "recv_settled" ? data?.recv_settled : data?.recv_overdue;

  return (
    <PageShell
      backLabel={lang === "si" ? "ආපසු" : "Back"}
      title={lang === "si" ? "ගෙවිය යුතු සහ ලැබිය යුතු විශ්ලේෂණය" : "Payables & Receivables Analysis"}
      subtitle={lang === "si"
        ? "PO සහ ඉන්වොයිස් දෙගුණ ගණනය නොකර නිවැරදි ශේෂය"
        : "True outstanding balances — POs and Invoices are kept separate to avoid double-counting the same transaction."}
      width="full"
    >
      {loading ? (
        <ReportSkeleton />
      ) : error ? (
        <div className="rounded-2xl px-5 py-4 text-[13px] text-red-600"
          style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.2)" }}>
          {error}
        </div>
      ) : (
        <div className="report-halves space-y-8">

          {/* ══════════════ TOP HALF — PAYABLES ══════════════ */}
          <section>
            <div className="mb-3 flex items-center gap-2">
              <span className="material-symbols-outlined text-[18px]" style={{ color: "#dc2626" }}>arrow_upward</span>
              <h2 className="text-[15px] font-extrabold text-[var(--text-1)]">
                {lang === "si" ? "ගෙවිය යුතු විශ්ලේෂණය" : "Payables Analysis"}
              </h2>
            </div>

            <div className="mb-4 flex gap-2">
              {PAY_TABS.map(tb => (
                <button key={tb.key} onClick={() => setPayTab(tb.key)}
                  className="flex items-center gap-2 rounded-xl px-4 py-2 text-[12px] font-semibold transition"
                  style={payTab === tb.key
                    ? { background: tb.color, color: "#fff", boxShadow: `0 2px 8px ${tb.color}40` }
                    : { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
                  {lang === "si" ? tb.label_si : tb.label_en}
                  <span className="rounded-full px-1.5 py-0.5 text-[10px] font-bold"
                    style={payTab === tb.key ? { background: "rgba(255,255,255,0.25)" } : { background: "var(--bg)" }}>
                    {tb.count}
                  </span>
                </button>
              ))}
            </div>

            <p className="mb-3 text-[12px] text-[var(--text-3)]">
              {payTab === "outstanding" && (lang === "si"
                ? "ඉදිරි ගෙවිය යුතු ඉන්වොයිස් — ඔබ ඇත්තෙන්ම ගෙවිය යුතු මුදල"
                : "Unpaid invoices and receipts — the actual confirmed amount your business owes right now.")}
              {payTab === "committed" && (lang === "si"
                ? "ඔබ ඔතා ඇති PO — ඉන්වොයිසය ලැබෙන විට ගෙවිය යුතු වේ."
                : "Active POs you have raised — funds will be owed when the Invoice arrives. Fulfilled POs are excluded (their Invoice is in Outstanding).")}
              {payTab === "settled" && (lang === "si"
                ? "ගෙව්වා ලේඛන"
                : "Documents where payment has been recorded as settled.")}
            </p>

            <PayableTable rows={payRows ?? []} lang={lang} router={router}
              emptyMsg={lang === "si" ? "ලේඛන නොමැත" : "Nothing here."} />
          </section>

          {/* Divider (hidden when the halves sit side by side) */}
          <div className="report-divider" style={{ height: 1, background: "var(--border)" }} />

          {/* ══════════════ BOTTOM HALF — RECEIVABLES ══════════════ */}
          <section>
            <div className="mb-3 flex items-center gap-2">
              <span className="material-symbols-outlined text-[18px]" style={{ color: "#0891b2" }}>arrow_downward</span>
              <h2 className="text-[15px] font-extrabold text-[var(--text-1)]">
                {lang === "si" ? "ලැබිය යුතු විශ්ලේෂණය" : "Receivables Analysis"}
              </h2>
            </div>

            <div className="mb-4 flex gap-2">
              {RECV_TABS.map(tb => (
                <button key={tb.key} onClick={() => setRecvTab(tb.key)}
                  className="flex items-center gap-2 rounded-xl px-4 py-2 text-[12px] font-semibold transition"
                  style={recvTab === tb.key
                    ? { background: tb.color, color: "#fff", boxShadow: `0 2px 8px ${tb.color}40` }
                    : { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
                  {lang === "si" ? tb.label_si : tb.label_en}
                  <span className="rounded-full px-1.5 py-0.5 text-[10px] font-bold"
                    style={recvTab === tb.key ? { background: "rgba(255,255,255,0.25)" } : { background: "var(--bg)" }}>
                    {tb.count}
                  </span>
                </button>
              ))}
            </div>

            <p className="mb-3 text-[12px] text-[var(--text-3)]">
              {recvTab === "recv_outstanding" && (lang === "si"
                ? "ලැබීමට ඇති ඉන්වොයිස් — තවම නොලැබුණු මුදල්"
                : "Unpaid invoices and receipts owed to you — not yet received.")}
              {recvTab === "recv_settled" && (lang === "si"
                ? "ලැබුණු මුදල්"
                : "Documents where the receivable has already been received.")}
              {recvTab === "recv_overdue" && (lang === "si"
                ? "දින 30කට වඩා පරණ ලැබිය යුතු මුදල් — ගෙවීම් මතක් කිරීමක් අවශ්‍ය විය හැක."
                : "Unpaid receivables older than 30 days — these may need a payment reminder.")}
            </p>

            <ReceivableTable rows={recvRows ?? []} lang={lang} router={router}
              emptyMsg={lang === "si" ? "ලේඛන නොමැත" : "Nothing here."} />
          </section>

        </div>
      )}
    </PageShell>
  );
}
