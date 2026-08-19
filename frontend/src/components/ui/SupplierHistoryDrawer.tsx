"use client";

// Slide-in panel showing a supplier/customer's full transaction history and
// financial summary. Extracted from the suppliers page so the chat can open the
// same drawer when a supplier pill is tapped.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type Transaction = {
  document_id: string; document_type: string; date: string;
  amount: number; currency: string; flow_type: string;
  paid_status: string; invoice_status: string; po_status: string; notes: string;
};

const TYPE_COLOR: Record<string, string> = {
  invoice: "#2252b5", receipt: "#16a34a", po: "#7c3aed", dn: "#ea6c0a",
};

function getToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

function StatusBadge({ val }: { val: string }) {
  if (!val || val === "NULL") return null;
  const map: Record<string, string> = {
    paid: "#16a34a", not_paid: "#ea6c0a", overdue: "#dc2626",
    received: "#16a34a", not_received: "#ea6c0a",
    approved: "#16a34a", pending: "#ea6c0a", rejected: "#dc2626",
  };
  const c = map[val.toLowerCase()] || "#64748b";
  return (
    <span className="ml-1 rounded-md px-1.5 py-0.5 text-[9px] font-bold uppercase"
      style={{ background: `${c}15`, color: c }}>
      {val.replace(/_/g, " ")}
    </span>
  );
}

export default function SupplierHistoryDrawer({ name, lang, onClose }: { name: string; lang: string; onClose: () => void }) {
  const router = useRouter();
  const [data, setData] = useState<{ transactions: Transaction[]; summary: Record<string, number> } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${BACKEND_URL}/suppliers/${encodeURIComponent(name)}/history`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [name]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center"
      style={{ background: "rgba(0,0,0,0.35)", height: "var(--app-height)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-[900px] rounded-t-3xl px-5 py-6 shadow-2xl"
        style={{
          background: "var(--surface)",
          // 80vh was measured against the *large* viewport, so on mobile the
          // sheet ran under the browser toolbar. --app-height is what's usable.
          maxHeight: "calc(var(--app-height) * 0.85)",
          paddingBottom: "calc(1.5rem + var(--safe-bottom))",
          overflowY: "auto",
          overscrollBehavior: "contain",
        }}>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="text-[18px] font-extrabold text-[var(--text-1)]">{name}</p>
            <p className="text-[12px] text-[var(--text-3)]">
              {lang === "si" ? "ගනුදෙනු ඉතිහාසය" : "Full Transaction History"}
            </p>
          </div>
          <button onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-xl transition hover:opacity-70"
            style={{ background: "var(--bg)" }}>
            <span className="material-symbols-outlined text-[20px]">close</span>
          </button>
        </div>

        {loading ? (
          <p className="py-8 text-center text-[13px] text-[var(--text-3)]">{lang === "si" ? "පූරණය..." : "Loading..."}</p>
        ) : !data ? (
          <p className="py-8 text-center text-[13px] text-red-500">Failed to load</p>
        ) : (
          <>
            <div className="mb-2 grid grid-cols-2 gap-2">
              {[
                { lbl: lang === "si" ? "ලැබිය යුතු" : "Receivable", val: data.summary.total_received, color: "#16a34a", bg: "rgba(22,163,74,0.06)", border: "rgba(22,163,74,0.2)" },
                { lbl: lang === "si" ? "ගෙවිය යුතු" : "Payable",    val: data.summary.total_paid,     color: "#dc2626", bg: "rgba(220,38,38,0.06)", border: "rgba(220,38,38,0.2)" },
              ].map(({ lbl, val, color, bg, border }) => (
                <div key={lbl} className="rounded-xl p-3 text-center" style={{ background: bg, border: `1px solid ${border}` }}>
                  <p className="text-[9px] font-bold uppercase tracking-wider" style={{ color }}>{lbl}</p>
                  <p className="mt-1 text-[14px] font-extrabold" style={{ color }}>{val === 0 ? "—" : `LKR ${val.toLocaleString()}`}</p>
                </div>
              ))}
            </div>
            <div className="mb-2 grid grid-cols-2 gap-2">
              {[
                { lbl: lang === "si" ? "ලැබුණු මුදල" : "Total Received", val: data.summary.total_received, color: "#0891b2", bg: "rgba(8,145,178,0.06)", border: "rgba(8,145,178,0.2)" },
                { lbl: lang === "si" ? "ගෙව්වා"       : "Total Paid",     val: data.summary.total_paid,     color: "#7c3aed", bg: "rgba(124,58,237,0.06)", border: "rgba(124,58,237,0.2)" },
              ].map(({ lbl, val, color, bg, border }) => (
                <div key={lbl} className="rounded-xl p-3 text-center" style={{ background: bg, border: `1px solid ${border}` }}>
                  <p className="text-[9px] font-bold uppercase tracking-wider" style={{ color }}>{lbl}</p>
                  <p className="mt-1 text-[14px] font-extrabold" style={{ color }}>{val === 0 ? "—" : `LKR ${val.toLocaleString()}`}</p>
                </div>
              ))}
            </div>
            <div className="mb-4 flex items-center justify-between rounded-xl px-4 py-2.5"
              style={{
                background: data.summary.net >= 0 ? "rgba(34,82,181,0.06)" : "rgba(234,108,10,0.06)",
                border: `1px solid ${data.summary.net >= 0 ? "rgba(34,82,181,0.15)" : "rgba(234,108,10,0.2)"}`,
              }}>
              <p className="text-[10px] font-bold uppercase tracking-wider"
                style={{ color: data.summary.net >= 0 ? "#2252b5" : "#ea6c0a" }}>
                {lang === "si" ? "ශේෂය" : "Net Position"}
              </p>
              <p className="text-[15px] font-extrabold"
                style={{ color: data.summary.net >= 0 ? "#2252b5" : "#ea6c0a" }}>
                {data.summary.net === 0 ? "—" : `${data.summary.net >= 0 ? "+" : ""}LKR ${data.summary.net.toLocaleString()}`}
              </p>
            </div>

            <div className="space-y-2">
              {data.transactions.length === 0 && (
                <p className="py-4 text-center text-[13px] text-[var(--text-3)]">
                  {lang === "si" ? "ගනුදෙනු නොමැත" : "No transactions"}
                </p>
              )}
              {data.transactions.map((t, i) => (
                <button key={i} onClick={() => router.push(`/analysis/${t.document_id}`)}
                  className="flex w-full items-center justify-between rounded-xl px-4 py-3 text-left transition hover:opacity-80"
                  style={{ background: "var(--bg)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white text-[10px] font-bold"
                      style={{ background: TYPE_COLOR[t.document_type] || "#64748b" }}>
                      {(t.document_type || "?").slice(0, 2).toUpperCase()}
                    </span>
                    <div>
                      <p className="text-[13px] font-semibold text-[var(--text-1)]">{t.document_id}</p>
                      <p className="text-[11px] text-[var(--text-3)]">
                        {t.date || "—"}
                        <StatusBadge val={t.paid_status || t.invoice_status || t.po_status} />
                      </p>
                    </div>
                  </div>
                  <span className="text-[13px] font-bold"
                    style={{ color: t.flow_type?.includes("receiv") || t.flow_type?.includes("inflow") ? "#16a34a" : "#dc2626" }}>
                    {t.flow_type?.includes("receiv") || t.flow_type?.includes("inflow") ? "+" : "−"}
                    LKR {t.amount.toLocaleString()}
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
