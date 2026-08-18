"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import BottomNav from "@/components/layout/BottomNav";
import ThemeToggle from "@/components/layout/ThemeToggle";
import { getSession, logoutUser, SessionUser, getStoredToken } from "@/lib/auth";
import Image from "next/image";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { hasUnreadNotifications } from "@/lib/notifications";
import { syncOverdueAlerts } from "@/lib/overdueAlerts";
import { syncCashFlowAlerts } from "@/lib/cashFlowAlerts";
import { formatMoney, otherPartyName } from "@/lib/format";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import HealthScoreCard from "@/components/ui/HealthScoreCard";
import WhoOwesWho     from "@/components/ui/WhoOwesWho";
import BudgetVsActual, { BudgetCategory } from "@/components/ui/BudgetVsActual";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type MismatchAlert = {
  document_id: string;
  company_name: string;
  date: string;
  document_type: string;
};

type HealthScore    = { net: number; trend_pct: number; color: "green"|"red"; this_month: string };
type BalanceEntry   = { document_id: string; document_type: string; supplier_name: string; amount: number; currency: string; date: string };

type SummaryData = {
  total: number;
  invoice: number;
  receipt: number;
  po: number;
  dn: number;
  recent_documents: RecentDocument[];
  pending_processing_count?: number;
  ready_for_query_count?: number;
  mismatch_alerts?: MismatchAlert[];
  // Feature 2 analytics
  health_score?:      HealthScore;
  top_receivables?:   BalanceEntry[];
  top_payables?:      BalanceEntry[];
};

type RecentDocument = {
  document_id: string;
  document_type: "invoice" | "po" | "dn" | "receipt" | "unknown";
  company_name: string;
  supplier_name: string;
  date: string;
  final_total_amount: string;
  currency: string;
};

type DocIconType = "po" | "invoice" | "dn" | "receipt" | "unknown";

function DocIcon({ type }: { type: DocIconType }) {
  const map: Record<DocIconType, { bg: string; color: string; icon: string }> = {
    invoice: { bg: "rgba(34,82,181,0.12)", color: "#2252b5", icon: "description" },
    po:      { bg: "rgba(124,58,237,0.10)", color: "#7c3aed", icon: "shopping_cart" },
    dn:      { bg: "rgba(249,115,22,0.10)", color: "#ea6c0a", icon: "local_shipping" },
    receipt: { bg: "rgba(22,163,74,0.10)",  color: "#16a34a", icon: "receipt" },
    unknown: { bg: "rgba(100,116,139,0.1)", color: "#64748b", icon: "draft" },
  };

  const item = map[type] ?? map.unknown;

  return (
    <div
      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl"
      style={{ background: item.bg }}
    >
      <span className="material-symbols-outlined text-[20px]" style={{ color: item.color }}>
        {item.icon}
      </span>
    </div>
  );
}

function StatCard({ label, value, color, loading }: { label: string; value: string; color?: string; loading?: boolean }) {
  return (
    <div
      className="rounded-2xl px-4 py-4 shadow-sm"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <p className="text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--text-3)]">
        {label}
      </p>
      {loading ? (
        <div
          className="mt-2 h-[22px] w-12 animate-pulse rounded-md"
          style={{ background: "var(--border)" }}
          aria-hidden
        />
      ) : (
        <p
          className="mt-2 text-[22px] font-extrabold leading-none"
          style={{ color: color || "var(--text-1)" }}
        >
          {value}
        </p>
      )}
    </div>
  );
}

// Pulsing placeholder mirroring a recent-document row, shown while the
// dashboard summary is still fetching (instead of "no documents yet").
function RecentDocSkeleton() {
  const bar = (cls: string) => (
    <div className={`animate-pulse rounded-md ${cls}`} style={{ background: "var(--border)" }} aria-hidden />
  );
  return (
    <div className="w-full rounded-2xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-4">
        <div className="h-11 w-11 shrink-0 animate-pulse rounded-xl" style={{ background: "var(--border)" }} aria-hidden />
        <div className="min-w-0 flex-1 space-y-2">
          {bar("h-[15px] w-1/2")}
          {bar("h-[11px] w-1/3")}
          {bar("h-[12px] w-2/5")}
        </div>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [session, setSession] = useState<SessionUser | null>(null);
  const [lang, setLang] = useState<AppLanguage>("en");
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [error, setError] = useState("");
  const [hasUnread, setHasUnread] = useState(false);
  const [cashFlow, setCashFlow] = useState<Array<{month:string;inflow:number;outflow:number;net:number}>>([]);
  const [budgetCats, setBudgetCats] = useState<BudgetCategory[]>([]);

  useEffect(() => {
    const load = async () => {
      setLang(getStoredLanguage());
      const s = await getSession();
      if (!s) { router.push("/login"); return; }
      setSession(s);

      try {
        const token = getStoredToken();
        if (!token) { setError("Missing login token. Please log in again."); return; }

        let res: Response;
        try {
          res = await fetch(`${BACKEND_URL}/dashboard-summary`, {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
          });
        } catch {
          // Network error — backend not reachable
          setError(
            "Cannot reach the backend server. Make sure it is running: " +
            "cd backend && uvicorn app:app --reload --port 8000"
          );
          return;
        }

        if (res.status === 401) {
          localStorage.removeItem("token");
          router.push("/login");
          return;
        }

        const data = await res.json();
        if (!res.ok || !data.success) {
          throw new Error(data.message || `Server error ${res.status}`);
        }
        setSummary(data);

        // IT-23: surface overdue payables/receivables as notifications.
        void syncOverdueAlerts(token);

        // Surface cash-flow-health alerts (negative/declining net cash flow).
        void syncCashFlowAlerts(token);

        // IT-20: fetch 6-month cash flow for the bar chart
        fetch(`${BACKEND_URL}/cash-flow?months=6`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        }).then(r => r.json()).then(d => { if (d.success) setCashFlow(d.data || []); }).catch(() => {});

        // Budget vs actual (this month) — only renders when the user has set
        // targets in Settings → Budget.
        fetch(`${BACKEND_URL}/user/budget-vs-actual`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        }).then(r => r.json()).then(d => { if (d.success) setBudgetCats(d.categories || []); }).catch(() => {});
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to fetch dashboard summary.");
      }
    };

    load();
  }, [router]);

  useEffect(() => {
    const update = () => setHasUnread(hasUnreadNotifications());
    update();
    window.addEventListener("notifications-updated", update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener("notifications-updated", update);
      window.removeEventListener("storage", update);
    };
  }, []);

  if (!session) return null;

  const t = ui[lang];
  const recentDocs = summary?.recent_documents || [];
  // Still fetching the summary — drives skeleton placeholders instead of "0" / "no documents".
  const loading = !summary && !error;

  const getRecentMeta = (doc: RecentDocument) => {
    const amt = formatMoney(doc.final_total_amount, doc.currency) || "—";
    const dt = doc.date && doc.date !== "NULL" ? doc.date : "—";
    return `${dt} • ${amt}`;
  };

  // "R11 · Receipt" style sub-label under the party name
  const DOC_TYPE_LABEL: Record<string, { en: string; si: string }> = {
    invoice: { en: "Invoice", si: "ඉන්වොයිස්" },
    receipt: { en: "Receipt", si: "රිසිට්පත" },
    po: { en: "Purchase Order", si: "මිලදී ගැනීමේ ඇණවුම" },
    dn: { en: "Delivery Note", si: "බෙදාහැරීමේ සටහන" },
    unknown: { en: "Document", si: "ලේඛනය" },
  };
  const docSubLabel = (doc: RecentDocument) => {
    const label = DOC_TYPE_LABEL[doc.document_type] ?? DOC_TYPE_LABEL.unknown;
    return `${doc.document_id} · ${lang === "si" ? label.si : label.en}`;
  };
  // Bold title = the other party; fall back to the id when none was extracted.
  const docTitle = (doc: RecentDocument) => otherPartyName(doc) || doc.document_id;

  return (
    <MobileShell>
      <div className="min-h-screen pb-24" style={{ background: "var(--bg)" }}>
        {/* Header */}
        <header style={{ background: "var(--surface)", borderBottom: "1px solid var(--border)" }}>
          {/* One row from 640px up. Below that the controls wrap to their own
              row — five of them plus the wordmark won't fit across a phone —
              but stay right-aligned so they read as a deliberate group rather
              than bunching against the left edge. This used to stack all the
              way up to lg (1024px), which left half the header empty on any
              tablet or narrowed desktop window. */}
          <div className="mx-auto flex w-full max-w-[960px] flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            {/* min-w-0 lets this side shrink so a long company name truncates
                instead of shoving the controls off the right edge. */}
            <div className="flex min-w-0 items-center gap-2.5 sm:gap-3">
              {/* Decorative: the "SME-GPT" heading beside it already names the
                  app, so an alt text here would just be read out twice. */}
              <Image
                src="/logo.png"
                alt=""
                width={40}
                height={40}
                priority
                className="h-10 w-10 shrink-0 rounded-xl object-contain shadow-sm"
              />
              <div className="min-w-0">
                <h1 className="text-[17px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[19px]">
                  SME-GPT
                </h1>
                <p className="truncate text-[12px] text-[var(--text-2)]">{session.companyName}</p>
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-1.5 self-end sm:gap-2 sm:self-auto">
              <ThemeToggle />
              <LanguageSwitcher />

              <button
                onClick={() => router.push("/notifications")}
                aria-label="Notifications"
                className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition hover:bg-[var(--surface-2)]"
              >
                <span className="material-symbols-outlined text-[20px] text-[var(--text-2)]">
                  notifications
                </span>
                {hasUnread && (
                  <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-red-500" />
                )}
              </button>

              <button
                onClick={() => router.push("/profile")}
                aria-label="Profile"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white transition hover:opacity-90"
                style={{ background: "#c97b5a" }}
              >
                <span className="material-symbols-outlined text-[20px]">person</span>
              </button>

              {/* Icon-only on phones, where these five share a cramped row of
                  their own; the label returns from sm up. */}
              <button
                onClick={() => { void logoutUser(); }}
                aria-label="Logout"
                className="flex h-9 w-9 items-center justify-center rounded-xl transition hover:bg-[var(--surface-2)] sm:w-auto sm:px-3"
                style={{ border: "1px solid var(--border)", color: "var(--text-2)" }}
              >
                <span className="material-symbols-outlined text-[18px] sm:hidden">logout</span>
                <span className="hidden text-[12px] font-semibold sm:inline">Logout</span>
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[960px] px-4 py-6 sm:px-6">
          <section>
            <h2 className="text-[24px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[28px]">
              {t.welcomeTitle}
            </h2>
            <p className="mt-1.5 text-[13px] leading-6 text-[var(--text-2)]">
              {t.welcomeSubtitle}
            </p>
          </section>

          {error && (
            <div
              className="mt-4 rounded-2xl px-4 py-3 text-[13px] text-red-600"
              style={{ background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.2)" }}
            >
              {error}
            </div>
          )}

          {/* Upload CTA */}
          <section className="mt-6">
            <button
              onClick={() => router.push("/upload")}
              className="flex w-full items-center gap-4 rounded-2xl px-5 py-5 text-left text-white shadow-md transition hover:opacity-90 active:scale-[0.99]"
              style={{ background: "var(--brand)" }}
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/15">
                <span className="material-symbols-outlined text-[22px]">upload_file</span>
              </div>
              <div>
                <p className="text-[15px] font-bold">{t.uploadCTA}</p>
                <p className="text-[12px] text-white/65">PDF, PNG, JPG supported</p>
              </div>
              <span className="material-symbols-outlined ml-auto text-white/50">chevron_right</span>
            </button>
          </section>

          {/* Quick links to reports */}
          <section className="mt-5 flex flex-wrap gap-2">
            {[
              { label: lang === "si" ? "ලාභ/පාඩු" : "P&L Report",       icon: "trending_up",  path: "/reports/pnl",      color: "#2252b5" },
              { label: lang === "si" ? "ගෙවිය යුතු ශේෂ" : "Outstanding Payments", icon: "payments", path: "/reports/payables", color: "#dc2626" },
              { label: lang === "si" ? "සැපයුම්කරුවන්" : "Suppliers", icon: "contacts", path: "/suppliers", color: "#16a34a" },
              { label: lang === "si" ? "අතින් ලේඛනය" : "Manual Entry", icon: "edit_note",  path: "/manual-entry",   color: "#64748b" },
            ].map(({ label, icon, path, color }) => (
              <button key={path} onClick={() => router.push(path)}
                className="flex items-center gap-2 rounded-xl px-4 py-2 text-[12px] font-bold transition hover:opacity-80"
                style={{ background: `${color}12`, color, border: `1px solid ${color}25` }}>
                <span className="material-symbols-outlined text-[15px]">{icon}</span>
                {label}
              </button>
            ))}
          </section>

          {/* Stats */}
          <section className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {(() => {
              // Show pulsing skeletons while the summary is still fetching, so counts
              // never flash a misleading "0" on first load.
              return (
                <>
                  <StatCard label={lang === "si" ? "මුළු ලේඛන"  : "Total Documents"} value={String(summary?.total   ?? 0)} loading={loading} />
                  <StatCard label={lang === "si" ? "ඉන්වොයිස්"  : "Invoices"}        value={String(summary?.invoice ?? 0)} color="var(--brand-mid)" loading={loading} />
                  <StatCard label={lang === "si" ? "රිසිට්"      : "Receipts"}        value={String(summary?.receipt ?? 0)} color="#16a34a" loading={loading} />
                  <StatCard label="PO / DN"                                            value={String((summary?.po ?? 0) + (summary?.dn ?? 0))} color="#7c3aed" loading={loading} />
                </>
              );
            })()}
          </section>

          {/* Budget vs Actual (Settings → Budget) — only if targets are set */}
          {budgetCats.length > 0 && (
            <section className="mt-6">
              <BudgetVsActual categories={budgetCats} currency="LKR" lang={lang} />
            </section>
          )}

          {/* Recent docs */}
          <section className="mt-8">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-[17px] font-extrabold tracking-tight text-[var(--text-1)]">
                {t.recentDocuments}
              </h3>
              <button
                onClick={() => router.push("/repository")}
                className="text-[13px] font-bold transition hover:opacity-75"
                style={{ color: "var(--brand-mid)" }}
              >
                {t.viewAll}
              </button>
            </div>

            <div className="space-y-3">
              {loading ? (
                Array.from({ length: 3 }).map((_, i) => <RecentDocSkeleton key={i} />)
              ) : recentDocs.length === 0 ? (
                <div
                  className="rounded-2xl px-4 py-8 text-center text-[14px] text-[var(--text-2)]"
                  style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
                >
                  {t.noSavedDocs}
                </div>
              ) : (
                recentDocs.map((doc) => (
                  <button
                    key={doc.document_id}
                    onClick={() => router.push(`/analysis/${doc.document_id}`)}
                    className="w-full rounded-2xl p-4 text-left transition hover:-translate-y-px hover:shadow-md"
                    style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
                  >
                    <div className="flex items-center gap-4">
                      <DocIcon type={doc.document_type} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[15px] font-bold text-[var(--text-1)]">
                          {docTitle(doc)}
                        </p>
                        <p className="mt-0.5 text-[11px] font-semibold text-[var(--text-3)]">
                          {docSubLabel(doc)}
                        </p>
                        <p className="mt-0.5 text-[12px] text-[var(--text-2)]">
                          {getRecentMeta(doc)}
                        </p>
                      </div>
                      <span
                        className="shrink-0 rounded-lg px-2.5 py-1 text-[10px] font-bold uppercase"
                        style={{ background: "rgba(22,163,74,0.1)", color: "#16a34a" }}
                      >
                        {lang === "si" ? "සුරැකිණි" : "Saved"}
                      </span>
                    </div>
                  </button>
                ))
              )}
            </div>
          </section>

          {/* UI-D2: Invoice Insights Ready notification card */}
          {summary?.mismatch_alerts && summary.mismatch_alerts.length > 0 && (
            <section className="mt-5">
              <div
                className="rounded-2xl p-4"
                style={{ background: "rgba(234,108,10,0.06)", border: "1px solid rgba(234,108,10,0.25)" }}
              >
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-[18px]" style={{ color: "#ea6c0a" }}>
                    insights
                  </span>
                  <p className="text-[13px] font-bold" style={{ color: "#ea6c0a" }}>
                    {t.insightsReady}
                  </p>
                </div>
                {summary.mismatch_alerts.map((alert) => (
                  <div key={alert.document_id} className="mt-2">
                    <p className="text-[12px] text-[var(--text-2)]">
                      {lang === "si"
                        ? <>
                            <span className="font-semibold text-[var(--text-1)]">{alert.document_id}</span>
                            {alert.company_name && alert.company_name !== "NULL" ? ` (${alert.company_name})` : ""}
                            {" "}ලේඛනයේ අංකගණිත නොගැලපීමක් හඳුනාගෙන ඇත. ලබාගත් මුළු එකතු සත්‍යාපනය කරන්න.
                          </>
                        : <>
                            System detected an arithmetic mismatch in{" "}
                            <span className="font-semibold text-[var(--text-1)]">{alert.document_id}</span>
                            {alert.company_name && alert.company_name !== "NULL" ? ` (${alert.company_name})` : ""}
                            . Verify the extracted totals.
                          </>
                      }
                    </p>
                    <button
                      onClick={() => router.push(`/analysis/${alert.document_id}`)}
                      className="mt-1 text-[12px] font-bold transition hover:opacity-75"
                      style={{ color: "var(--brand-mid)" }}
                    >
                      {t.viewComparison}
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* IT-20 — 6-month Cash Flow Chart */}
          {cashFlow.length > 0 && (
            <section className="mt-8">
              <h3 className="mb-4 text-[17px] font-extrabold tracking-tight text-[var(--text-1)]">
                {lang === "si" ? "මාසික මුදල් ප්‍රවාහය" : "Monthly Cash Flow"}
              </h3>
              <div
                className="rounded-2xl p-4"
                style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
              >
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={cashFlow} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="month" tick={{ fontSize: 11 }} stroke="var(--text-3)" />
                    <YAxis tick={{ fontSize: 10 }} stroke="var(--text-3)" tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                    <Tooltip
                      formatter={(value, name) => [
                        `LKR ${Number(value ?? 0).toLocaleString()}`,
                        name === "inflow" ? (lang === "si" ? "ලැබීම" : "Inflow")
                          : name === "outflow" ? (lang === "si" ? "ගෙවීම" : "Outflow")
                          : (lang === "si" ? "ශේෂය" : "Net"),
                      ]}
                    />
                    <Legend formatter={(v) => v === "inflow" ? (lang === "si" ? "ලැබීම" : "Inflow") : v === "outflow" ? (lang === "si" ? "ගෙවීම" : "Outflow") : (lang === "si" ? "ශේෂය" : "Net")} />
                    <Bar dataKey="inflow"  fill="#16a34a" radius={[3,3,0,0]} />
                    <Bar dataKey="outflow" fill="#dc2626" radius={[3,3,0,0]} />
                    <Bar dataKey="net"     fill="#2252b5" radius={[3,3,0,0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}

          {/* ── Feature 2: Widget 1 — Business Health Score ─────────────── */}
          {summary?.health_score && (
            <section className="mt-6">
              <HealthScoreCard
                health={summary.health_score}
                lang={lang}
                currency="LKR"
              />
            </section>
          )}

          {/* ── Feature 2: Widget 4 — Who Owes Who ─────────────────────── */}
          {(summary?.top_receivables?.length || summary?.top_payables?.length) ? (
            <section className="mt-6">
              <WhoOwesWho
                receivables={summary.top_receivables || []}
                payables={summary.top_payables || []}
                lang={lang}
              />
            </section>
          ) : null}

        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}
