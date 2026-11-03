"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import PageShell from "@/components/layout/PageShell";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { formatMoney, otherPartyName } from "@/lib/format";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type RepoDocument = {
  document_id: string;
  document_type: "invoice" | "po" | "dn" | "receipt" | "unknown";
  company_name: string;
  supplier_name: string;
  date: string;
  raw_total_amount?: string | number;
  final_total_amount?: string | number;
  payable_amount?: string | number;
  currency: string;
  status: string;
  flow_type?: string;
  file_size_kb?: number | null;
  source?: string | null;
  // Iteration 10: workflow status fields
  po_status?: string | null;
  dn_status?: string | null;
  invoice_status?: string | null;
};

type TabType = "all" | "invoice" | "po" | "dn" | "receipt" | "archived";

function getAuthToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

function isUsable(v: unknown) {
  if (v === undefined || v === null) return false;
  const t = String(v).trim();
  return t !== "" && t.toUpperCase() !== "NULL";
}

const typeMap: Record<RepoDocument["document_type"], { bg: string; color: string; icon: string; label: string }> = {
  invoice: { bg: "rgba(34,82,181,0.1)", color: "#2252b5", icon: "description", label: "INVOICE" },
  po:      { bg: "rgba(124,58,237,0.1)", color: "#7c3aed", icon: "shopping_cart", label: "PURCHASE ORDER" },
  dn:      { bg: "rgba(249,115,22,0.1)", color: "#ea6c0a", icon: "local_shipping", label: "DELIVERY NOTE" },
  receipt: { bg: "rgba(22,163,74,0.1)",  color: "#16a34a", icon: "receipt_long", label: "RECEIPT" },
  unknown: { bg: "rgba(100,116,139,0.1)", color: "#64748b", icon: "draft", label: "DOCUMENT" },
};

function statusBadge(status: string) {
  const s = (status || "ready").toLowerCase();
  if (s === "processing")
    return { bg: "rgba(234,108,10,0.1)", color: "#ea6c0a", label: "PROCESSING" };
  if (s === "error" || s === "failed")
    return { bg: "rgba(220,38,38,0.1)", color: "#dc2626", label: "ERROR" };
  return { bg: "rgba(22,163,74,0.1)", color: "#16a34a", label: "READY" };
}

function formatFileSize(kb?: number | null) {
  if (kb == null || isNaN(Number(kb)) || Number(kb) <= 0) return null;
  const n = Number(kb);
  if (n >= 1024) return `${(n / 1024).toFixed(1)} MB`;
  return `${Math.round(n)} KB`;
}

// Pulsing placeholder that mirrors a real document card's layout, shown while
// the repository list is still fetching (instead of a "Loading…" text line).
function SkeletonCard() {
  const bar = (cls: string) => (
    <div className={`animate-pulse rounded-md ${cls}`} style={{ background: "var(--border)" }} aria-hidden />
  );
  return (
    <div className="rounded-2xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="flex items-start gap-4">
        <div className="h-11 w-11 shrink-0 animate-pulse rounded-xl" style={{ background: "var(--border)" }} aria-hidden />
        <div className="min-w-0 flex-1">
          {bar("h-[15px] w-1/2")}
          <div className="mt-2">{bar("h-[11px] w-1/3")}</div>
          <div className="mt-3 flex items-center justify-between">
            {bar("h-[22px] w-20")}
            {bar("h-[13px] w-16")}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function RepositoryPage() {
  const router = useRouter();
  const [lang, setLang] = useState<AppLanguage>("en");
  const [tab, setTab] = useState<TabType>("all");
  const [documents, setDocuments] = useState<RepoDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  // IT-21: server-side upload-date range filter
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [archivingId, setArchivingId] = useState<string | null>(null);
  const [autoClassify, setAutoClassify] = useState(true);
  // Iteration 10: per-tab status filters
  const [statusFilter, setStatusFilter] = useState<string>("all");

  useEffect(() => {
    setLang(getStoredLanguage());
    // Fetch the user's autoClassify setting to control which tabs are shown
    fetch("/api/profile")
      .then(r => r.json())
      .then(d => {
        if (typeof d?.user?.autoClassify === "boolean") {
          setAutoClassify(d.user.autoClassify);
          if (!d.user.autoClassify) setTab("all");
        }
      })
      .catch(() => {});
  }, []);

  const t = ui[lang];

  const loadDocuments = async () => {
    const token = getAuthToken();
    if (!token) { router.push("/login"); return; }
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "200" });
      if (dateFrom) params.set("from", dateFrom);
      if (dateTo) params.set("to", dateTo);
      const res = await fetch(`${BACKEND_URL}/documents?${params.toString()}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (res.status === 401) { localStorage.removeItem("token"); router.push("/login"); return; }
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.message || "Failed to fetch.");
      setDocuments(data.documents || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch documents.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadDocuments(); }, [router, dateFrom, dateTo]);

  // Reset statusFilter when the main tab changes
  const handleTabChange = (v: TabType) => { setTab(v); setStatusFilter("all"); };

  const filtered = useMemo(() => {
    let byTab: RepoDocument[];
    if (tab === "archived") {
      byTab = documents.filter((d) => String(d.status).toLowerCase() === "archived");
    } else if (tab === "all") {
      byTab = documents.filter((d) => String(d.status).toLowerCase() !== "archived");
    } else {
      byTab = documents.filter((d) => d.document_type === tab && String(d.status).toLowerCase() !== "archived");
    }
    // Iteration 10: apply workflow status sub-filter
    if (statusFilter !== "all") {
      byTab = byTab.filter((d) => {
        if (tab === "po")      return (d.po_status      || "pending").toLowerCase() === statusFilter;
        if (tab === "invoice") return (d.invoice_status || "pending").toLowerCase() === statusFilter;
        if (tab === "dn")      return (d.dn_status      || "pending").toLowerCase() === statusFilter;
        return true;
      });
    }
    if (!searchQuery.trim()) return byTab;
    const q = searchQuery.toLowerCase();
    return byTab.filter(
      (d) =>
        d.document_id.toLowerCase().includes(q) ||
        (d.company_name || "").toLowerCase().includes(q) ||
        (d.supplier_name || "").toLowerCase().includes(q)
    );
  }, [tab, documents, searchQuery, statusFilter]);

  const tabLabel = (v: TabType) => {
    const labels: Record<TabType, string> = {
      all: t.documentsAll, invoice: lang === "si" ? "ඉන්වොයිස්" : "Invoice",
      po: "PO", dn: "DN", receipt: lang === "si" ? "රිසිට්" : "Receipt",
      archived: lang === "si" ? "සංරක්ෂිත" : "Archived",
    };
    return labels[v];
  };

  const formatAmount = (item: RepoDocument) => {
    const amt = isUsable(item.payable_amount)
      ? item.payable_amount
      : isUsable(item.final_total_amount)
      ? item.final_total_amount
      : isUsable(item.raw_total_amount)
      ? item.raw_total_amount
      : null;
    return formatMoney(amt, item.currency) || t.noAmount;
  };

  // "IN11 · Invoice" sub-label shown under the party name
  const DOC_TYPE_LABEL: Record<string, { en: string; si: string }> = {
    invoice: { en: "Invoice", si: "ඉන්වොයිස්" },
    receipt: { en: "Receipt", si: "රිසිට්පත" },
    po: { en: "Purchase Order", si: "මිලදී ගැනීමේ ඇණවුම" },
    dn: { en: "Delivery Note", si: "බෙදාහැරීමේ සටහන" },
    unknown: { en: "Document", si: "ලේඛනය" },
  };
  const docSubLabel = (item: RepoDocument) => {
    const label = DOC_TYPE_LABEL[item.document_type] ?? DOC_TYPE_LABEL.unknown;
    return `${item.document_id} · ${lang === "si" ? label.si : label.en}`;
  };
  // Bold title = the other party; fall back to the id when none was extracted.
  const docTitle = (item: RepoDocument) => otherPartyName(item) || item.document_id;

  return (
    <PageShell
      title={t.repositoryTitle}
      subtitle={t.repoSubtitle}
      width="full"
    >

          {/* Search bar */}
          <div className="mb-4 flex items-center gap-2 rounded-xl px-4 py-2.5"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            <span className="material-symbols-outlined text-[18px]" style={{ color: "var(--text-3)" }}>search</span>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t.searchPlaceholder}
              className="flex-1 bg-transparent text-[14px] text-[var(--text-1)] outline-none placeholder:text-[var(--text-3)]"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery("")} className="text-[var(--text-3)] hover:text-[var(--text-1)]">
                <span className="material-symbols-outlined text-[16px]">close</span>
              </button>
            )}
          </div>

          {/* Date range filter — filters by document date (the date on the bill) */}
          <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl px-4 py-2.5"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            <span className="material-symbols-outlined text-[18px]" style={{ color: "var(--text-3)" }}>date_range</span>
            <span className="text-[12px] font-semibold text-[var(--text-2)]">
              {lang === "si" ? "ලේඛනයේ දිනය" : "Document date"}
            </span>
            <input
              type="date"
              value={dateFrom}
              max={dateTo || undefined}
              onChange={(e) => setDateFrom(e.target.value)}
              aria-label={lang === "si" ? "සිට" : "From"}
              className="rounded-lg px-2 py-1 text-[13px] text-[var(--text-1)] outline-none"
              style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
            />
            <span className="text-[12px] text-[var(--text-3)]">{lang === "si" ? "–" : "to"}</span>
            <input
              type="date"
              value={dateTo}
              min={dateFrom || undefined}
              onChange={(e) => setDateTo(e.target.value)}
              aria-label={lang === "si" ? "දක්වා" : "To"}
              className="rounded-lg px-2 py-1 text-[13px] text-[var(--text-1)] outline-none"
              style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
            />
            {(dateFrom || dateTo) && (
              <button
                onClick={() => { setDateFrom(""); setDateTo(""); }}
                className="ml-auto flex items-center gap-1 text-[12px] font-bold transition hover:opacity-75"
                style={{ color: "var(--text-3)" }}
              >
                <span className="material-symbols-outlined text-[16px]">close</span>
                {lang === "si" ? "හිස් කරන්න" : "Clear"}
              </button>
            )}
          </div>

          {/* Tabs */}
          <div className="mb-5 flex flex-wrap gap-2">
            {(autoClassify
              ? (["all", "invoice", "po", "dn", "receipt", "archived"] as TabType[])
              : (["all", "archived"] as TabType[])
            ).map((v) => (
              <button
                key={v}
                onClick={() => handleTabChange(v)}
                className="rounded-full px-4 py-1.5 text-[12px] font-semibold transition"
                style={
                  tab === v
                    ? { background: "var(--brand)", color: "#fff" }
                    : { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }
                }
              >
                {tabLabel(v)}
              </button>
            ))}
          </div>

          {/* Iteration 10: Status sub-filter chips */}
          {tab === "po" && (
            <div className="mb-4 flex flex-wrap gap-2">
              {[
                { v: "all", label: t.filterAll },
                { v: "pending", label: `⏳ ${t.stPending}` },
                { v: "approved", label: `✅ ${t.stApproved}` },
                { v: "rejected", label: `❌ ${t.stRejected}` },
                { v: "fulfilled", label: `📦 ${t.stFulfilled}` },
                { v: "cancelled", label: `🚫 ${t.stCancelled}` },
                { v: "partially_delivered", label: `⚠ ${t.stPartial}` },
              ].map(({ v, label }) => (
                <button key={v} onClick={() => setStatusFilter(v)}
                  className="rounded-full px-3 py-1 text-[11px] font-semibold transition"
                  style={statusFilter === v
                    ? { background: "#7c3aed", color: "#fff" }
                    : { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
                  {label}
                </button>
              ))}
            </div>
          )}
          {tab === "invoice" && (
            <div className="mb-4 flex flex-wrap gap-2">
              {[
                { v: "all", label: t.filterAll },
                { v: "pending", label: `⏳ ${t.stPending}` },
                { v: "overdue", label: `🔴 ${t.stOverdue}` },
                { v: "paid", label: `✅ ${t.stPaid}` },
                { v: "partially_paid", label: `⚠ ${t.stPartial}` },
                { v: "cancelled", label: `🚫 ${t.stCancelled}` },
              ].map(({ v, label }) => (
                <button key={v} onClick={() => setStatusFilter(v)}
                  className="rounded-full px-3 py-1 text-[11px] font-semibold transition"
                  style={statusFilter === v
                    ? { background: "#2252b5", color: "#fff" }
                    : { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
                  {label}
                </button>
              ))}
            </div>
          )}
          {tab === "dn" && (
            <div className="mb-4 flex flex-wrap gap-2">
              {[
                { v: "all", label: t.filterAll },
                { v: "pending", label: `⏳ ${t.stPending}` },
                { v: "delivered", label: `✅ ${t.stDelivered}` },
                { v: "delayed", label: `🔴 ${t.stDelayed}` },
                { v: "partially_delivered", label: `⚠ ${t.stPartial}` },
                { v: "failed", label: `❌ ${t.stFailed}` },
                { v: "returned", label: `↩ ${t.stReturned}` },
              ].map(({ v, label }) => (
                <button key={v} onClick={() => setStatusFilter(v)}
                  className="rounded-full px-3 py-1 text-[11px] font-semibold transition"
                  style={statusFilter === v
                    ? { background: "#ea6c0a", color: "#fff" }
                    : { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
                  {label}
                </button>
              ))}
            </div>
          )}

          {/* Content */}
          {loading ? (
            <div className="space-y-3" aria-busy aria-label={t.loading}>
              {Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : error ? (
            <div className="rounded-2xl px-4 py-6 text-center text-[14px] text-red-600"
              style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.2)" }}>
              {error}
            </div>
          ) : filtered.length === 0 ? (
            <div className="rounded-2xl px-4 py-8 text-center text-[14px] text-[var(--text-2)]"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
              {t.noDocumentsFound}
            </div>
          ) : (
            <>
            <div className="card-list">
              {filtered.map((item) => {
                const m = typeMap[item.document_type] ?? typeMap.unknown;
                const amt = formatAmount(item);
                const badge = statusBadge(item.status);
                const fileSize = formatFileSize(item.file_size_kb);
                return (
                  <div
                    key={item.document_id}
                    className="rounded-2xl p-4"
                    style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
                  >
                    <div className="flex items-start gap-4">
                      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl" style={{ background: m.bg }}>
                        <span className="material-symbols-outlined text-[20px]" style={{ color: m.color }}>{m.icon}</span>
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-[15px] font-bold text-[var(--text-1)]">{docTitle(item)}</p>
                          {item.source === "manual" && (
                            <span className="shrink-0 rounded-md px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider"
                              style={{ background: "rgba(234,108,10,0.1)", color: "#ea6c0a" }}>
                              {lang === "si" ? "අතින්" : "MANUAL"}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--text-3)]">{docSubLabel(item)}</p>

                        <div className="mt-2 grid gap-2 sm:grid-cols-2">
                          <div className="sm:text-right sm:col-start-2">
                            <p className="text-[11px] text-[var(--text-3)]">{t.dateLabel}</p>
                            <p className="text-[13px] text-[var(--text-1)]">
                              {item.date && item.date !== "NULL" ? item.date : "—"}
                            </p>
                          </div>
                        </div>

                        {fileSize && (
                          <p className="mt-1 text-[11px] text-[var(--text-3)]">{fileSize}</p>
                        )}

                        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                          <span
                            className="rounded-lg px-2.5 py-1 text-[10px] font-bold uppercase"
                            style={{ background: badge.bg, color: badge.color }}
                          >
                            {badge.label}
                          </span>
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => setArchivingId(archivingId === item.document_id ? null : item.document_id)}
                              className="text-[12px] font-bold transition hover:opacity-75"
                              style={{ color: String(item.status).toLowerCase() === "archived" ? "var(--brand-mid)" : "var(--text-3)" }}
                            >
                              {String(item.status).toLowerCase() === "archived" ? t.unarchive : t.archive}
                            </button>
                            <button
                              onClick={() => router.push(`/analysis/${item.document_id}`)}
                              className="text-[12px] font-bold transition hover:opacity-75"
                              style={{ color: "var(--brand-mid)" }}
                            >
                              {t.openDoc}
                            </button>
                          </div>
                        </div>

                        {archivingId === item.document_id && (
                          <div className="mt-3 rounded-xl px-3 py-2.5 text-[12px]"
                            style={{ background: "rgba(26,53,96,0.06)", border: "1px solid rgba(26,53,96,0.12)", color: "var(--text-2)" }}>
                            <span className="font-semibold">
                              {String(item.status).toLowerCase() === "archived" ? t.restoreConfirm : t.archiveConfirm}
                            </span>{" "}
                            {String(item.status).toLowerCase() === "archived" ? t.restoreDesc : t.archiveDesc}
                            <div className="mt-2 flex gap-2">
                              <button
                                className="rounded-lg px-3 py-1 text-[11px] font-bold text-white transition hover:opacity-80"
                                style={{ background: "var(--brand)" }}
                                onClick={async () => {
                                  const token = getAuthToken();
                                  const newStatus = String(item.status).toLowerCase() === "archived" ? "ready" : "archived";
                                  try {
                                    await fetch(`${BACKEND_URL}/documents/${item.document_id}`, {
                                      method: "PUT",
                                      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
                                      body: JSON.stringify({ status: newStatus }),
                                    });
                                    setArchivingId(null);
                                    loadDocuments();
                                  } catch { setArchivingId(null); }
                                }}
                              >
                                {String(item.status).toLowerCase() === "archived" ? t.restore : t.archive}
                              </button>
                              <button className="text-[11px] font-bold" style={{ color: "var(--text-3)" }}
                                onClick={() => setArchivingId(null)}>{t.cancel}</button>
                            </div>
                          </div>
                        )}
                      </div>

                      <div className="text-right">
                        <p
                          className="text-[13px] font-bold"
                          style={{ color: amt === t.noAmount ? "var(--text-3)" : "var(--text-1)" }}
                        >
                          {amt}
                        </p>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* REFRESH LIST */}
            <div className="mt-6 flex justify-center">
              <button
                onClick={loadDocuments}
                disabled={loading}
                className="flex items-center gap-2 rounded-xl px-6 py-2.5 text-[13px] font-bold transition hover:opacity-80 disabled:opacity-50"
                style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--brand-mid)" }}
              >
                <span className="material-symbols-outlined text-[16px]">refresh</span>
                {t.refreshList}
              </button>
            </div>
            </>
          )}
    </PageShell>
  );
}
