"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import TopBar from "@/components/layout/TopBar";
import { getSession, SessionUser, getStoredToken } from "@/lib/auth";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { syncOverdueAlerts } from "@/lib/overdueAlerts";
import { syncCashFlowAlerts } from "@/lib/cashFlowAlerts";
import { formatMoney, otherPartyName } from "@/lib/format";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
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

type OrderIssues = {
  orders: {
    order_id: string; anchor_document_id: string; order_status: string;
    issues: { code: string; severity: "info" | "warning" | "error"; message_en: string; message_si: string }[];
  }[];
  counts: Record<string, number>;
  suggestions: number;
  linked_orders: number;
};

const ISSUE_CHIPS: { code: string; en: (n: number) => string; si: (n: number) => string; tone: "warn" | "danger" | "info" }[] = [
  { code: "over_billed",            en: (n) => `${n} over-billed`,            si: (n) => `වැඩිපුර බිල් ${n}`,          tone: "danger" },
  { code: "price_mismatch",         en: (n) => `${n} price difference${n === 1 ? "" : "s"}`, si: (n) => `මිල වෙනස් ${n}`, tone: "warn" },
  { code: "partial_delivery",       en: (n) => `${n} partly delivered`,       si: (n) => `අර්ධව ලැබුණු ${n}`,          tone: "warn" },
  { code: "over_delivery",          en: (n) => `${n} over-delivered`,         si: (n) => `වැඩිපුර ලැබුණු ${n}`,        tone: "warn" },
  { code: "item_not_on_po",         en: (n) => `${n} item${n === 1 ? "" : "s"} not on PO`, si: (n) => `PO හි නැති අයිතම ${n}`, tone: "warn" },
  { code: "unpaid_balance",         en: (n) => `${n} unpaid balance${n === 1 ? "" : "s"}`, si: (n) => `ගෙවීමට ඇති ශේෂ ${n}`, tone: "info" },
  { code: "overpaid",               en: (n) => `${n} overpaid`,               si: (n) => `වැඩිපුර ගෙවූ ${n}`,          tone: "warn" },
  { code: "delivered_not_invoiced", en: (n) => `${n} not invoiced yet`,       si: (n) => `තවම ඉන්වොයිස් නොකළ ${n}`,    tone: "info" },
];

// One colour per document type, used by icons, stat cards and the donut alike.
const DOC_COLORS: Record<DocIconType, { color: string; bg: string; icon: string }> = {
  invoice: { color: "var(--brand-mid)", bg: "rgba(34,82,181,0.12)", icon: "description" },
  po:      { color: "#7c3aed",          bg: "rgba(124,58,237,0.12)", icon: "shopping_cart" },
  dn:      { color: "#ea6c0a",          bg: "rgba(249,115,22,0.12)", icon: "local_shipping" },
  receipt: { color: "#16a34a",          bg: "rgba(22,163,74,0.12)",  icon: "receipt_long" },
  unknown: { color: "#64748b",          bg: "rgba(100,116,139,0.12)", icon: "draft" },
};

function DocIcon({ type }: { type: DocIconType }) {
  const item = DOC_COLORS[type] ?? DOC_COLORS.unknown;
  return (
    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl" style={{ background: item.bg }}>
      <span className="material-symbols-outlined text-[20px]" style={{ color: item.color }}>
        {item.icon}
      </span>
    </div>
  );
}

function StatCard({
  label, value, icon, color, bg, hint, share, loading, href, links,
}: {
  label: string; value: string; icon: string; color: string; bg: string;
  hint: string; share?: number; loading?: boolean;
  /** Where the card opens (the Files page, pre-filtered). */
  href: string;
  /** Optional finer links shown in place of the hint (e.g. "3 PO · 4 DN"). */
  links?: { label: string; href: string }[];
}) {
  return (
    <div className="ui-card ui-card-hover group relative overflow-hidden p-4 sm:p-5">
      {/* Stretched link: the whole card is the click target, while the optional
          sub-links below sit above it (z-10) and stay separately clickable. */}
      <Link
        href={href}
        aria-label={label}
        className="absolute inset-0 rounded-[1.25rem] focus-visible:outline-none focus-visible:ring-4"
        style={{ ["--tw-ring-color" as string]: "var(--brand-ring)" }}
      />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)] sm:text-[11px]">{label}</p>
          {loading ? (
            <div className="mt-2.5 h-[30px] w-14 animate-pulse rounded-md" style={{ background: "var(--border)" }} aria-hidden />
          ) : (
            <p className="mt-2 text-[26px] font-extrabold leading-none tracking-tight sm:text-[32px]" style={{ color: "var(--text-1)" }}>
              {value}
            </p>
          )}
        </div>
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl sm:h-11 sm:w-11" style={{ background: bg, color }}>
          <span className="material-symbols-outlined text-[21px]">{icon}</span>
        </span>
      </div>
      <div className="mt-4">
        <div className="h-1.5 w-full overflow-hidden rounded-full" style={{ background: "var(--surface-2)" }}>
          <div
            className="h-full rounded-full transition-[width] duration-700"
            style={{ width: `${loading ? 0 : Math.round((share ?? 1) * 100)}%`, background: color }}
          />
        </div>
        <div className="mt-2 flex items-center justify-between gap-2">
          {links && !loading ? (
            <span className="relative z-10 flex min-w-0 items-center gap-1.5">
              {links.map((l) => (
                <Link
                  key={l.href}
                  href={l.href}
                  className="rounded-md px-1.5 py-0.5 text-[11px] font-semibold transition hover:opacity-80"
                  style={{ background: bg, color }}
                >
                  {l.label}
                </Link>
              ))}
            </span>
          ) : (
            <p className="truncate text-[11px] font-medium text-[var(--text-3)]">{loading ? " " : hint}</p>
          )}
          <span
            className="material-symbols-outlined shrink-0 text-[16px] opacity-0 transition-all group-hover:translate-x-0.5 group-hover:opacity-100"
            style={{ color }}
            aria-hidden
          >
            arrow_forward
          </span>
        </div>
      </div>
    </div>
  );
}

function ActionCard({
  icon, title, desc, color, onClick,
}: { icon: string; title: string; desc: string; color: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="ui-card ui-card-hover group flex items-center gap-3 p-4 text-left">
      <span
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl"
        style={{ background: `color-mix(in srgb, ${color} 13%, transparent)`, color }}
      >
        <span className="material-symbols-outlined text-[22px]">{icon}</span>
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[14px] font-bold text-[var(--text-1)]">{title}</span>
        <span className="mt-0.5 hidden truncate text-[12px] text-[var(--text-3)] sm:block">{desc}</span>
      </span>
      <span className="material-symbols-outlined text-[18px] text-[var(--text-3)] transition-transform group-hover:translate-x-0.5">
        arrow_forward
      </span>
    </button>
  );
}

function CardHeader({ title, icon, action }: { title: string; icon: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-5 pt-5">
      <div className="flex items-center gap-2.5">
        <span className="material-symbols-outlined text-[20px]" style={{ color: "var(--brand-mid)" }}>{icon}</span>
        <h3 className="text-[16px] font-extrabold tracking-tight text-[var(--text-1)]">{title}</h3>
      </div>
      {action}
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
    <div className="flex items-center gap-4 px-5 py-4">
      <div className="h-11 w-11 shrink-0 animate-pulse rounded-xl" style={{ background: "var(--border)" }} aria-hidden />
      <div className="min-w-0 flex-1 space-y-2">
        {bar("h-[14px] w-1/2")}
        {bar("h-[11px] w-1/3")}
      </div>
      {bar("hidden h-[14px] w-24 md:block")}
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [session, setSession] = useState<SessionUser | null>(null);
  const [lang, setLang] = useState<AppLanguage>("en");
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [error, setError] = useState("");
  const [cashFlow, setCashFlow] = useState<Array<{month:string;inflow:number;outflow:number;net:number}>>([]);
  const [budgetCats, setBudgetCats] = useState<BudgetCategory[]>([]);
  // PO -> DN -> Invoice -> Receipt orders needing attention (GET /order-issues).
  const [orderIssues, setOrderIssues] = useState<OrderIssues | null>(null);

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

        fetch(`${BACKEND_URL}/order-issues`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        }).then(r => r.json()).then(d => { if (d.success) setOrderIssues(d); }).catch(() => {});

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

  if (!session) return null;

  const t = ui[lang];
  const si = lang === "si";
  const recentDocs = summary?.recent_documents || [];
  // Still fetching the summary — drives skeleton placeholders instead of "0" / "no documents".
  const loading = !summary && !error;

  const total   = summary?.total ?? 0;
  const invoice = summary?.invoice ?? 0;
  const receipt = summary?.receipt ?? 0;
  const po      = summary?.po ?? 0;
  const dn      = summary?.dn ?? 0;
  const share   = (n: number) => (total > 0 ? n / total : 0);
  const pct     = (n: number) => `${Math.round(share(n) * 100)}%`;
  const ofAll   = si ? "සියලු ලේඛනවලින්" : "of all documents";
  const alerts  = summary?.mismatch_alerts || [];

  const hour = new Date().getHours();
  const greeting = si
    ? (hour < 12 ? "සුබ උදෑසනක්" : hour < 17 ? "සුබ දහවලක්" : "සුබ සන්ධ්‍යාවක්")
    : (hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening");
  const firstName = (session.fullName || "").trim().split(/\s+/)[0];
  const today = new Date().toLocaleDateString(si ? "si-LK" : "en-GB", {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });

  // "R11 · Receipt" style sub-label under the party name
  const DOC_TYPE_LABEL: Record<string, { en: string; si: string }> = {
    invoice: { en: "Invoice", si: "ඉන්වොයිස්" },
    receipt: { en: "Receipt", si: "රිසිට්පත" },
    po: { en: "Purchase Order", si: "මිලදී ගැනීමේ ඇණවුම" },
    dn: { en: "Delivery Note", si: "බෙදාහැරීමේ සටහන" },
    unknown: { en: "Document", si: "ලේඛනය" },
  };
  const typeLabel = (doc: RecentDocument) => {
    const label = DOC_TYPE_LABEL[doc.document_type] ?? DOC_TYPE_LABEL.unknown;
    return si ? label.si : label.en;
  };
  // Bold title = the other party; fall back to the id when none was extracted.
  const docTitle = (doc: RecentDocument) => otherPartyName(doc) || doc.document_id;
  const docDate  = (doc: RecentDocument) => (doc.date && doc.date !== "NULL" ? doc.date : "—");
  const docAmt   = (doc: RecentDocument) => formatMoney(doc.final_total_amount, doc.currency) || "—";

  const flowLabel = (k: string) =>
    k === "inflow" ? (si ? "ලැබීම" : "Inflow") : k === "outflow" ? (si ? "ගෙවීම" : "Outflow") : (si ? "ශේෂය" : "Net");
  const FLOW_COLORS: Record<string, string> = { inflow: "#16a34a", outflow: "#dc2626", net: "var(--brand-mid)" };

  const other = Math.max(0, total - invoice - receipt - po - dn);
  const mix = [
    { key: "invoice", name: si ? "ඉන්වොයිස්" : "Invoices",       value: invoice, color: DOC_COLORS.invoice.color },
    { key: "receipt", name: si ? "රිසිට්" : "Receipts",           value: receipt, color: DOC_COLORS.receipt.color },
    { key: "po",      name: si ? "මිලදී ඇණවුම්" : "Purchase Orders", value: po, color: DOC_COLORS.po.color },
    { key: "dn",      name: si ? "බෙදාහැරීම් සටහන්" : "Delivery Notes", value: dn, color: DOC_COLORS.dn.color },
    { key: "other",   name: si ? "වෙනත්" : "Other",               value: other,   color: DOC_COLORS.unknown.color },
  ].filter((m) => m.value > 0);

  const tooltipStyle = {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: 12,
    boxShadow: "var(--card-shadow-hover)",
    fontSize: 12,
    color: "var(--text-1)",
  };

  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <TopBar
          showBack={false}
          companyName={session.companyName}
          leading={
            <div className="min-w-0">
              <p className="truncate text-[15px] font-bold text-[var(--text-1)]">
                {greeting}{firstName ? `, ${firstName}` : ""} <span aria-hidden>👋</span>
              </p>
              <p className="truncate text-[12px] text-[var(--text-3)]">{today}</p>
            </div>
          }
        />

        <main className="mx-auto w-full max-w-[1600px] space-y-6 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {error && (
            <div
              className="rounded-2xl px-4 py-3 text-[13px] text-red-600"
              style={{ background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.2)" }}
            >
              {error}
            </div>
          )}

          {/* ── Hero ───────────────────────────────────────────────── */}
          <section
            className="rise-in relative overflow-hidden rounded-3xl p-6 text-white shadow-lg sm:p-8 lg:p-10"
            style={{ background: "linear-gradient(125deg, var(--hero-from) 0%, var(--hero-to) 100%)" }}
          >
            {/* Decorative glow + grid */}
            <div className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-white/10 blur-3xl" aria-hidden />
            <div className="pointer-events-none absolute -bottom-32 left-1/3 h-72 w-72 rounded-full blur-3xl" style={{ background: "rgba(127,176,255,0.25)" }} aria-hidden />
            <div
              className="pointer-events-none absolute inset-0 opacity-[0.07]"
              style={{
                backgroundImage:
                  "linear-gradient(rgba(255,255,255,.8) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.8) 1px, transparent 1px)",
                backgroundSize: "32px 32px",
                maskImage: "linear-gradient(90deg, transparent, black 70%)",
                WebkitMaskImage: "linear-gradient(90deg, transparent, black 70%)",
              }}
              aria-hidden
            />

            <div className="relative grid items-center gap-8 lg:grid-cols-[minmax(0,1fr)_auto]">
              <div className="min-w-0">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-white/12 px-3 py-1 text-[11px] font-semibold ring-1 ring-white/20">
                  <span className="material-symbols-outlined text-[14px]">auto_awesome</span>
                  {si ? "AI බලයෙන් • English සහ සිංහල" : "AI-powered • English & සිංහල"}
                </span>
                <h2 className="mt-4 text-[26px] font-extrabold leading-tight tracking-tight sm:text-[34px] lg:text-[40px]">
                  {t.welcomeTitle}
                </h2>
                <p className="mt-2 max-w-xl text-[13px] leading-6 text-white/75 sm:text-[15px]">
                  {t.welcomeSubtitle}
                </p>
                <div className="mt-6 flex flex-wrap gap-3">
                  <button
                    onClick={() => router.push("/upload")}
                    className="flex items-center gap-2 rounded-xl bg-white px-5 py-3 text-[14px] font-bold shadow-md transition hover:-translate-y-px hover:shadow-lg active:scale-[0.98]"
                    style={{ color: "#1a3560" }}
                  >
                    <span className="material-symbols-outlined text-[20px]">upload_file</span>
                    {t.uploadCTA}
                  </button>
                  <button
                    onClick={() => router.push("/query")}
                    className="flex items-center gap-2 rounded-xl bg-white/12 px-5 py-3 text-[14px] font-bold ring-1 ring-white/25 transition hover:bg-white/20 active:scale-[0.98]"
                  >
                    <span className="material-symbols-outlined text-[20px]">forum</span>
                    {si ? "සහායකගෙන් අසන්න" : "Ask the Assistant"}
                  </button>
                </div>
                <p className="mt-3 text-[11px] text-white/55">PDF, PNG, JPG supported</p>
              </div>

              {/* Glass mini-stats */}
              <div className="grid grid-cols-3 gap-2 sm:gap-3 lg:w-[380px] lg:grid-cols-1 xl:w-[420px]">
                {[
                  { icon: "inventory_2",  label: si ? "සකසන ලද ලේඛන" : "Documents processed", value: total },
                  { icon: "neurology",    label: si ? "AI විමසුම් සඳහා සූදානම්" : "Ready for AI questions", value: summary?.ready_for_query_count ?? total },
                  { icon: "rule",         label: si ? "සමාලෝචනය අවශ්‍යයි" : "Need a second look", value: alerts.length },
                ].map((s) => (
                  <div key={s.label} className="flex flex-col gap-2 rounded-2xl bg-white/10 p-3 ring-1 ring-white/15 backdrop-blur-sm sm:p-4 lg:flex-row lg:items-center lg:gap-4">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/15">
                      <span className="material-symbols-outlined text-[19px]">{s.icon}</span>
                    </span>
                    <div className="min-w-0 lg:flex-1">
                      <p className="text-[10px] font-medium leading-tight text-white/70 sm:text-[12px]">{s.label}</p>
                    </div>
                    <p className="text-[22px] font-extrabold leading-none sm:text-[26px]">
                      {loading ? "…" : s.value}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* ── Stats ──────────────────────────────────────────────── */}
          <section className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            <StatCard label={si ? "මුළු ලේඛන" : "Total Documents"} value={String(total)} icon="folder_copy"
              color="var(--brand)" bg="var(--brand-tint)" hint={si ? "සියලු වර්ග" : "All document types"} share={total > 0 ? 1 : 0} loading={loading} href="/repository" />
            <StatCard label={si ? "ඉන්වොයිස්" : "Invoices"} value={String(invoice)} icon={DOC_COLORS.invoice.icon}
              color={DOC_COLORS.invoice.color} bg={DOC_COLORS.invoice.bg} hint={`${pct(invoice)} ${ofAll}`} share={share(invoice)} loading={loading} href="/repository?type=invoice" />
            <StatCard label={si ? "රිසිට්" : "Receipts"} value={String(receipt)} icon={DOC_COLORS.receipt.icon}
              color={DOC_COLORS.receipt.color} bg={DOC_COLORS.receipt.bg} hint={`${pct(receipt)} ${ofAll}`} share={share(receipt)} loading={loading} href="/repository?type=receipt" />
            <StatCard label="PO / DN" value={String(po + dn)} icon={DOC_COLORS.po.icon}
              color={DOC_COLORS.po.color} bg={DOC_COLORS.po.bg} hint={`${po} PO · ${dn} DN`} share={share(po + dn)} loading={loading} href="/repository?type=po"
              links={[{ label: `${po} PO`, href: "/repository?type=po" }, { label: `${dn} DN`, href: "/repository?type=dn" }]} />
          </section>

          {/* ── Quick actions ──────────────────────────────────────── */}
          <section className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            <ActionCard icon="trending_up" color="#2252b5" onClick={() => router.push("/reports/pnl")}
              title={si ? "ලාභ/පාඩු" : "P&L Report"} desc={si ? "මාසික ආදායම සහ වියදම්" : "Monthly revenue vs expenses"} />
            <ActionCard icon="payments" color="#dc2626" onClick={() => router.push("/reports/payables")}
              title={si ? "ගෙවිය යුතු ශේෂ" : "Outstanding Payments"} desc={si ? "ලැබිය යුතු සහ ගෙවිය යුතු" : "Who owes whom, and how much"} />
            <ActionCard icon="contacts" color="#16a34a" onClick={() => router.push("/suppliers")}
              title={si ? "සැපයුම්කරුවන්" : "Suppliers"} desc={si ? "සියලු ගනුදෙනු පාර්ශ්ව" : "Every counterparty, full history"} />
            <ActionCard icon="edit_note" color="#64748b" onClick={() => router.push("/manual-entry")}
              title={si ? "අතින් ලේඛනය" : "Manual Entry"} desc={si ? "බිල්පතක් නැති විට" : "No bill? Create one yourself"} />
          </section>

          {/* ── Main grid ──────────────────────────────────────────── */}
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px] 2xl:grid-cols-[minmax(0,1fr)_440px]">
            <div className="min-w-0 space-y-6">
              {/* Recent documents */}
              <section className="ui-card overflow-hidden">
                <CardHeader
                  title={t.recentDocuments}
                  icon="history"
                  action={
                    <button
                      onClick={() => router.push("/repository")}
                      className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[13px] font-bold transition hover:bg-[var(--brand-tint)]"
                      style={{ color: "var(--brand-mid)" }}
                    >
                      {t.viewAll}
                      <span className="material-symbols-outlined text-[16px]">arrow_forward</span>
                    </button>
                  }
                />

                {/* Column headings (desktop) */}
                <div className="mt-4 hidden grid-cols-[minmax(0,1fr)_110px_150px_84px] gap-4 border-y px-5 py-2 text-[10px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)] md:grid"
                  style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}>
                  <span className="pl-[60px]">{si ? "පාර්ශ්වය" : "Party"}</span>
                  <span>{si ? "දිනය" : "Date"}</span>
                  <span className="text-right">{si ? "මුදල" : "Amount"}</span>
                  <span className="text-right">{si ? "තත්ත්වය" : "Status"}</span>
                </div>

                <div className="mt-2 divide-y divide-[var(--border)] md:mt-0" style={{ borderColor: "var(--border)" }}>
                  {loading ? (
                    Array.from({ length: 4 }).map((_, i) => <RecentDocSkeleton key={i} />)
                  ) : recentDocs.length === 0 ? (
                    <div className="px-5 py-12 text-center">
                      <span className="material-symbols-outlined text-[40px] text-[var(--text-3)]">upload_file</span>
                      <p className="mt-2 text-[14px] text-[var(--text-2)]">{t.noSavedDocs}</p>
                    </div>
                  ) : (
                    recentDocs.map((doc) => {
                      const c = DOC_COLORS[doc.document_type] ?? DOC_COLORS.unknown;
                      return (
                        <button
                          key={doc.document_id}
                          onClick={() => router.push(`/analysis/${doc.document_id}`)}
                          className="flex w-full items-center gap-4 px-5 py-3.5 text-left transition-colors hover:bg-[var(--surface-2)] md:grid md:grid-cols-[minmax(0,1fr)_110px_150px_84px]"
                          style={{ borderColor: "var(--border)" }}
                        >
                          <div className="flex min-w-0 flex-1 items-center gap-4">
                            <DocIcon type={doc.document_type} />
                            <div className="min-w-0">
                              <p className="truncate text-[14px] font-bold text-[var(--text-1)]">{docTitle(doc)}</p>
                              <p className="mt-0.5 truncate text-[11px] font-semibold text-[var(--text-3)]">
                                {doc.document_id} · <span style={{ color: c.color }}>{typeLabel(doc)}</span>
                              </p>
                              <p className="mt-0.5 text-[12px] text-[var(--text-2)] md:hidden">
                                {docDate(doc)} • {docAmt(doc)}
                              </p>
                            </div>
                          </div>
                          <span className="hidden text-[13px] text-[var(--text-2)] md:block">{docDate(doc)}</span>
                          <span className="hidden text-right text-[13px] font-bold text-[var(--text-1)] md:block">{docAmt(doc)}</span>
                          <span className="shrink-0 text-right">
                            <span
                              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-bold uppercase"
                              style={{ background: "rgba(22,163,74,0.1)", color: "#16a34a" }}
                            >
                              <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                              {si ? "සුරැකිණි" : "Saved"}
                            </span>
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              </section>

              {/* IT-20 — 6-month Cash Flow Chart */}
              <section className="ui-card">
                <CardHeader
                  title={si ? "මාසික මුදල් ප්‍රවාහය" : "Monthly Cash Flow"}
                  icon="monitoring"
                  action={
                    <div className="hidden items-center gap-3 sm:flex">
                      {["inflow", "outflow", "net"].map((k) => (
                        <span key={k} className="flex items-center gap-1.5 text-[12px] font-semibold text-[var(--text-2)]">
                          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: FLOW_COLORS[k] }} aria-hidden />
                          {flowLabel(k)}
                        </span>
                      ))}
                    </div>
                  }
                />
                <div className="px-3 pb-4 pt-4 sm:px-5">
                  {cashFlow.length > 0 ? (
                    <ResponsiveContainer width="100%" height={280}>
                      <BarChart data={cashFlow} margin={{ top: 4, right: 8, left: 0, bottom: 4 }} barGap={3}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                        <XAxis dataKey="month" tick={{ fontSize: 11, fill: "var(--text-3)" }} stroke="var(--border)" tickLine={false} />
                        <YAxis tick={{ fontSize: 10, fill: "var(--text-3)" }} stroke="var(--border)" tickLine={false} axisLine={false}
                          tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} width={44} />
                        <Tooltip
                          cursor={{ fill: "var(--surface-2)" }}
                          contentStyle={tooltipStyle}
                          labelStyle={{ color: "var(--text-1)", fontWeight: 700 }}
                          formatter={(value, name) => [`LKR ${Number(value ?? 0).toLocaleString()}`, flowLabel(String(name))]}
                        />
                        <Bar dataKey="inflow"  fill={FLOW_COLORS.inflow}  radius={[6, 6, 0, 0]} maxBarSize={28} />
                        <Bar dataKey="outflow" fill={FLOW_COLORS.outflow} radius={[6, 6, 0, 0]} maxBarSize={28} />
                        <Bar dataKey="net"     fill={FLOW_COLORS.net}     radius={[6, 6, 0, 0]} maxBarSize={28} />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="flex h-[200px] flex-col items-center justify-center text-center">
                      <span className="material-symbols-outlined text-[36px] text-[var(--text-3)]">bar_chart</span>
                      <p className="mt-2 text-[13px] text-[var(--text-3)]">
                        {loading ? "…" : si ? "තවම මුදල් ප්‍රවාහ දත්ත නැත" : "No cash-flow data yet"}
                      </p>
                    </div>
                  )}
                </div>
              </section>

              {/* ── Feature 2: Widget 4 — Who Owes Who ─────────────── */}
              {(summary?.top_receivables?.length || summary?.top_payables?.length) ? (
                <section>
                  <WhoOwesWho
                    receivables={summary.top_receivables || []}
                    payables={summary.top_payables || []}
                    lang={lang}
                  />
                </section>
              ) : null}
            </div>

            {/* ── Right column ─────────────────────────────────────── */}
            <aside className="min-w-0 space-y-6">
              {/* ── Feature 2: Widget 1 — Business Health Score ─────── */}
              {summary?.health_score && (
                <HealthScoreCard health={summary.health_score} lang={lang} currency="LKR" />
              )}

              {/* PO -> DN -> Invoice -> Receipt orders needing attention */}
              {orderIssues && (orderIssues.linked_orders > 0 || orderIssues.suggestions > 0) && (
                <section className="ui-card">
                  <CardHeader
                    title={si ? "ඇණවුම් ගැටලු" : "Order issues"}
                    icon="account_tree"
                    action={orderIssues.orders.length > 0 ? (
                      <span className="rounded-full px-2 py-0.5 text-[11px] font-bold"
                        style={{ background: "var(--warn-tint)", color: "var(--warn)", border: "1px solid var(--warn-border)" }}>
                        {orderIssues.orders.length}
                      </span>
                    ) : undefined}
                  />
                  <div className="p-5 pt-4">
                    {(() => {
                      const chips = ISSUE_CHIPS.filter((c) => (orderIssues.counts[c.code] || 0) > 0);
                      const tone = (t: "warn" | "danger" | "info") => ({
                        background: `var(--${t}-tint)`, color: `var(--${t})`, border: `1px solid var(--${t}-border)`,
                      });
                      return (chips.length > 0 || orderIssues.suggestions > 0) && (
                        <div className="mb-4 flex flex-wrap gap-1.5">
                          {chips.map((c) => (
                            <span key={c.code} className="rounded-lg px-2 py-1 text-[11px] font-bold" style={tone(c.tone)}>
                              {si ? c.si(orderIssues.counts[c.code]) : c.en(orderIssues.counts[c.code])}
                            </span>
                          ))}
                          {orderIssues.suggestions > 0 && (
                            <span className="rounded-lg border border-dashed px-2 py-1 text-[11px] font-bold"
                              style={{ borderColor: "var(--brand-mid)", color: "var(--brand-mid)" }}>
                              {si ? `යෝජිත සම්බන්ධ ${orderIssues.suggestions}` : `${orderIssues.suggestions} suggested link${orderIssues.suggestions === 1 ? "" : "s"}`}
                            </span>
                          )}
                        </div>
                      );
                    })()}
                    {orderIssues.orders.length === 0 ? (
                      <p className="flex items-center gap-2 text-[13px] text-[var(--text-2)]">
                        <span className="material-symbols-outlined" style={{ fontSize: 18, color: "var(--success)" }}>verified</span>
                        {si ? "සම්බන්ධිත සියලු ඇණවුම් ගැළපේ." : "All linked orders match."}
                      </p>
                    ) : (
                      <div className="space-y-2">
                        {orderIssues.orders.slice(0, 4).map((o) => {
                          const top = o.issues[0];
                          const t = top.severity === "error" ? "danger" : top.severity === "warning" ? "warn" : "info";
                          return (
                            <button key={o.order_id + o.anchor_document_id}
                              onClick={() => router.push(`/analysis/${o.anchor_document_id}`)}
                              className="group flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition hover:bg-[var(--surface-2)]"
                              style={{ border: "1px solid var(--border)" }}>
                              <span className="material-symbols-outlined mt-px shrink-0" style={{ fontSize: 18, color: `var(--${t})` }}>
                                {top.severity === "error" ? "error" : top.severity === "warning" ? "warning" : "info"}
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="flex items-center gap-2">
                                  <span className="truncate text-[13px] font-bold text-[var(--text-1)]">{o.order_id}</span>
                                  {o.issues.length > 1 && (
                                    <span className="text-[11px] text-[var(--text-3)]">
                                      +{o.issues.length - 1} {si ? "තවත්" : "more"}
                                    </span>
                                  )}
                                </span>
                                <span className="mt-0.5 block text-[12px] leading-5 text-[var(--text-2)]">{si ? top.message_si : top.message_en}</span>
                              </span>
                              <span className="material-symbols-outlined shrink-0 text-[var(--text-3)] transition group-hover:translate-x-0.5" style={{ fontSize: 18 }}>
                                arrow_forward
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* Document mix */}
              <section className="ui-card">
                <CardHeader title={si ? "ලේඛන වර්ග" : "Document Mix"} icon="donut_large" />
                <div className="flex items-center gap-4 p-5">
                  <div className="relative h-[150px] w-[150px] shrink-0">
                    {mix.length > 0 ? (
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie data={mix} dataKey="value" nameKey="name" innerRadius={50} outerRadius={72}
                            paddingAngle={mix.length > 1 ? 3 : 0} stroke="none" startAngle={90} endAngle={-270}>
                            {mix.map((m) => <Cell key={m.key} fill={m.color} />)}
                          </Pie>
                          <Tooltip contentStyle={tooltipStyle} />
                        </PieChart>
                      </ResponsiveContainer>
                    ) : (
                      <div className="h-full w-full rounded-full border-[14px]" style={{ borderColor: "var(--surface-2)" }} />
                    )}
                    <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                      <span className="text-[24px] font-extrabold leading-none text-[var(--text-1)]">{loading ? "…" : total}</span>
                      <span className="mt-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-3)]">{si ? "මුළු" : "Total"}</span>
                    </div>
                  </div>
                  <ul className="min-w-0 flex-1 space-y-2.5">
                    {mix.map((m) => (
                      <li key={m.key} className="flex items-center gap-2 text-[12px]">
                        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: m.color }} aria-hidden />
                        <span className="min-w-0 flex-1 truncate text-[var(--text-2)]">{m.name}</span>
                        <span className="font-bold text-[var(--text-1)]">{m.value}</span>
                      </li>
                    ))}
                    {!loading && mix.length === 0 && (
                      <li className="text-[12px] text-[var(--text-3)]">{t.noSavedDocs}</li>
                    )}
                  </ul>
                </div>
              </section>

              {/* UI-D2: Invoice Insights Ready notification card */}
              {alerts.length > 0 && (
                <section
                  className="rounded-[1.25rem] p-5"
                  style={{ background: "var(--warn-tint)", border: "1px solid var(--warn-border)" }}
                >
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-[20px]" style={{ color: "var(--warn)" }}>insights</span>
                    <p className="text-[14px] font-extrabold" style={{ color: "var(--warn)" }}>{t.insightsReady}</p>
                    <span className="ml-auto rounded-full px-2 py-0.5 text-[11px] font-bold" style={{ background: "var(--warn-border)", color: "var(--warn)" }}>
                      {alerts.length}
                    </span>
                  </div>
                  <div className="mt-3 space-y-3">
                    {alerts.map((alert) => (
                      <div key={alert.document_id} className="rounded-xl p-3" style={{ background: "var(--surface)", border: "1px solid var(--warn-border)" }}>
                        <p className="text-[12px] leading-5 text-[var(--text-2)]">
                          {si
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
                          className="mt-1.5 flex items-center gap-1 text-[12px] font-bold transition hover:opacity-75"
                          style={{ color: "var(--brand-mid)" }}
                        >
                          {t.viewComparison}
                          <span className="material-symbols-outlined text-[15px]">arrow_forward</span>
                        </button>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Budget vs Actual (Settings → Budget) — only if targets are set */}
              {budgetCats.length > 0 && (
                <BudgetVsActual categories={budgetCats} currency="LKR" lang={lang} />
              )}
            </aside>
          </div>
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}
