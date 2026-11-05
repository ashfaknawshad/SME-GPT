"use client";
/**
 * Unified Settings page.
 * Tabs: Account | Security | Preferences | Budget | Export
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import PageShell from "@/components/layout/PageShell";
import { getSession, logoutUser, SessionUser, getStoredToken } from "@/lib/auth";
import { AppLanguage, getStoredLanguage, ui, setStoredLanguage } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { TEXT_SIZES, TextSize, getStoredTextSize, setStoredTextSize } from "@/lib/textSize";
import { noticeDialog } from "@/lib/confirm";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type ProfileData = {
  fullName: string; profileImage: string; companyName: string; businessUnit: string;
  primaryLanguage: string; autoClassify: boolean; twoFactorEnabled: boolean;
  phone: string; jobTitle: string; country: string;
};

const BUDGET_CATS = ["Budgeted Cost","Revenue","Cash Inflow","Cash Outflow"];
type Tab = "account" | "security" | "preferences" | "budget" | "export";

const PERIOD_OPTIONS = [
  { key:"this_week",  en:"This Week",   si:"මේ සතිය"     },
  { key:"this_month", en:"This Month",  si:"මේ මාසය"     },
  { key:"last_month", en:"Last Month",  si:"ගිය මාසය"    },
  { key:"this_year",  en:"This Year",   si:"මේ අවුරුද්ද" },
  { key:"custom",     en:"Custom Range",si:"අභිමත"        },
];

function getToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

// ── Shared UI primitives ──────────────────────────────────────────────────────
function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`ui-card overflow-hidden ${className}`}>{children}</div>;
}
function SectionLabel({ icon, title, danger }: { icon:string; title:string; danger?:boolean }) {
  return (
    <div className="mb-2.5 mt-7 flex items-center gap-2 px-1 first:mt-0">
      <span className="material-symbols-outlined text-[16px]" style={{ color: danger ? "var(--danger)" : "var(--text-3)" }}>{icon}</span>
      <p className="text-[11px] font-bold uppercase tracking-[0.1em]" style={{ color: danger ? "var(--danger)" : "var(--text-3)" }}>{title}</p>
    </div>
  );
}
function Divider() { return <div className="mx-5" style={{ height:1, background:"var(--border)" }} />; }
function Toggle({ enabled, onClick, label }: { enabled:boolean; onClick:()=>void; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={label}
      onClick={onClick}
      className="relative inline-flex h-7 w-[50px] shrink-0 cursor-pointer items-center rounded-full p-0 transition-colors focus-visible:outline-none focus-visible:ring-4"
      style={{
        background: enabled ? "var(--brand-mid)" : "color-mix(in srgb, var(--text-3) 45%, var(--border))",
        ["--tw-ring-color" as string]: "var(--brand-ring)",
      }}
    >
      <span
        className="absolute top-[3px] h-[22px] w-[22px] rounded-full bg-white shadow transition-[left] duration-200"
        style={{ left: enabled ? 25 : 3 }}
      />
    </button>
  );
}
function IconTile({ icon, bg, color }: { icon: string; bg: string; color: string }) {
  return (
    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl" style={{ background: bg, color }}>
      <span className="material-symbols-outlined text-[20px]">{icon}</span>
    </span>
  );
}
function SettingRow({ icon, iconBg = "var(--brand-tint)", iconColor = "var(--brand-mid)", title, subtitle, children }: {
  icon: string; iconBg?: string; iconColor?: string; title: string; subtitle?: string; children: React.ReactNode;
}) {
  return (
    <div className="flex w-full items-center justify-between gap-4 px-5 py-4">
      <div className="flex min-w-0 flex-1 items-center gap-4">
        <IconTile icon={icon} bg={iconBg} color={iconColor} />
        <div className="min-w-0">
          <p className="text-[14px] font-semibold text-[var(--text-1)]">{title}</p>
          {subtitle && <p className="mt-0.5 text-[12px] text-[var(--text-3)]">{subtitle}</p>}
        </div>
      </div>
      {children}
    </div>
  );
}
function ChevronRow({ icon, title, subtitle, onClick, iconBg="var(--surface-2)", iconColor="var(--text-3)" }: {
  icon:string; title:string; subtitle:string; onClick:()=>void; iconBg?:string; iconColor?:string;
}) {
  return (
    <button onClick={onClick} className="group flex w-full items-center gap-4 px-5 py-4 text-left transition hover:bg-[var(--surface-2)]">
      <IconTile icon={icon} bg={iconBg} color={iconColor} />
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-semibold text-[var(--text-1)]">{title}</p>
        <p className="mt-0.5 text-[12px] text-[var(--text-3)]">{subtitle}</p>
      </div>
      <span className="material-symbols-outlined text-[20px] transition-transform group-hover:translate-x-0.5" style={{ color:"var(--text-3)" }}>chevron_right</span>
    </button>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function SettingsPage() {
  const router     = useRouter();
  const { theme, toggle: toggleTheme } = useTheme();
  const [lang, setLang]     = useState<AppLanguage>("en");
  const [session, setSession] = useState<SessionUser | null>(null);
  const [tab, setTab]         = useState<Tab>("account");
  const [textSize, setTextSize] = useState<TextSize>("default");
  // Profile state
  const [form, setForm]           = useState<ProfileData>({ fullName:"", profileImage:"", companyName:"", businessUnit:"", primaryLanguage:"en", autoClassify:true, twoFactorEnabled:false, phone:"", jobTitle:"", country:"" });
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving]       = useState(false);
  const [message, setMessage]     = useState("");

  // Password modal
  const [pwAction, setPwAction]   = useState<"export"|"delete"|null>(null);
  const [pwInput, setPwInput]     = useState("");
  const [pwError, setPwError]     = useState("");
  const [pwLoading, setPwLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [deleting, setDeleting]   = useState(false);

  // Budget state
  const [budgetOn, setBudgetOn]       = useState(false);
  const [budgets, setBudgets]         = useState<Record<string,string>>({});
  const [budgetSaved, setBudgetSaved] = useState(false);

  // Export state
  const [period, setPeriod]           = useState("this_month");
  const [customFrom, setCustomFrom]   = useState("");
  const [customTo, setCustomTo]       = useState("");
  const [downloading, setDownloading] = useState(false);


  useEffect(() => {
    setLang(getStoredLanguage());
    setTextSize(getStoredTextSize());
    const load = async () => {
      const s = await getSession();
      if (!s) { router.push("/login"); return; }
      setSession(s);
      try {
        const res  = await fetch("/api/profile", { cache:"no-store" });
        const data = await res.json();
        if (res.ok && data.user) {
          setForm({
            fullName: data.user.fullName || s.fullName || "",
            profileImage: data.user.profileImage || "",
            companyName: data.user.companyName || "",
            businessUnit: data.user.businessUnit || "",
            primaryLanguage: getStoredLanguage(),
            autoClassify: data.user.autoClassify ?? true,
            twoFactorEnabled: data.user.twoFactorEnabled ?? false,
            phone: data.user.phone || "",
            jobTitle: data.user.jobTitle || "",
            country: data.user.country || "",
          });
        }
      } catch {}

      const token = getToken();
      // Load budgets
      fetch(`${BACKEND_URL}/user/budget-settings`, { headers:{ Authorization:`Bearer ${token}` } })
        .then(r => r.json()).then(d => {
          if (d.budgets && Object.keys(d.budgets).length > 0) { setBudgetOn(true); setBudgets(d.budgets); }
        }).catch(() => {});
    };
    load();
  }, []);

  const update = (key: keyof ProfileData, val: string | boolean) => setForm(p => ({ ...p, [key]: val }));

  const handleSaveLanguage = async (next: AppLanguage) => {
    const token = getStoredToken();
    try {
      await fetch("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ ...form, primaryLanguage: next }),
      });
    } catch {}
  };

  const handleSave = async () => {
    setSaving(true); setMessage("");
    const token = getStoredToken();
    try {
      const res  = await fetch("/api/profile", { method:"PUT", headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`}, body:JSON.stringify(form) });
      const data = await res.json();
      if (!res.ok) { setMessage(data.error || "Save failed"); return; }
      setMessage("Profile updated");
      setIsEditing(false);
      setStoredLanguage(form.primaryLanguage as AppLanguage);
      setLang(form.primaryLanguage as AppLanguage);
    } catch { setMessage("Save failed"); }
    finally { setSaving(false); }
  };

  const handleToggleAutoClassify = async () => {
    const next = !form.autoClassify;
    update("autoClassify", next);  // optimistic UI update
    const token = getStoredToken();
    try {
      // Send the full form with the new value — don't rely on state having updated
      const res = await fetch("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ ...form, autoClassify: next }),
      });
      if (!res.ok) {
        update("autoClassify", !next);  // revert on failure
        setMessage("Failed to save preference");
      }
    } catch {
      update("autoClassify", !next);  // revert on network error
    }
  };

  const handleToggle2FA = async () => {
    // Optimistic update — flip immediately so the toggle moves right away
    const next = !form.twoFactorEnabled;
    update("twoFactorEnabled", next);
    try {
      const token = getToken();
      const res  = await fetch("/api/profile/two-factor", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ enabled: next }),
      });
      const data = await res.json();
      if (!res.ok) {
        // Revert on failure
        update("twoFactorEnabled", !next);
        setMessage(data.error || "Failed to update 2FA");
      } else {
        setMessage(next ? "Two-factor authentication enabled" : "Two-factor authentication disabled");
      }
    } catch {
      update("twoFactorEnabled", !next);
      setMessage("Network error — please try again");
    }
  };

  const handleExportData = async () => {
    setExporting(true);
    try {
      const token  = getToken();
      const res    = await fetch(`${BACKEND_URL}/user/export`, { headers:{ Authorization:`Bearer ${token}` } });
      const data   = await res.json();
      const rows   = (data.documents || []).map((d: Record<string,unknown>) => ({
        "Document ID": d.document_id ?? "", "Type": d.document_type ?? "",
        "Date": d.date ?? "", "Company": d.company_name ?? "",
        "Counterparty": d.supplier_name ?? "", "Amount": d.payable_amount ?? d.final_total_amount ?? "",
        "Currency": d.currency ?? "LKR",
      }));
      const { utils, writeFileXLSX } = await import("xlsx");
      const wb = utils.book_new();
      utils.book_append_sheet(wb, utils.json_to_sheet(rows), "Documents");
      writeFileXLSX(wb, "SME-GPT-Export.xlsx");
    } finally { setExporting(false); }
  };

  const handleDeleteAccount = async () => {
    setDeleting(true);
    const token = getToken();
    try {
      await fetch(`${BACKEND_URL}/user/account`, { method:"DELETE", headers:{ Authorization:`Bearer ${token}` } });
      await fetch("/api/user/delete", { method:"DELETE" });
      localStorage.removeItem("token"); sessionStorage.clear(); router.push("/login");
    } finally { setDeleting(false); }
  };

  const handlePasswordConfirm = async () => {
    if (!pwInput.trim()) return;
    setPwLoading(true); setPwError("");
    try {
      const res  = await fetch("/api/auth/verify-password", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ password:pwInput }) });
      const data = await res.json();
      if (!data.valid) { setPwError("Incorrect password"); return; }
      setPwAction(null); setPwInput("");
      if (pwAction === "export") await handleExportData();
      if (pwAction === "delete") await handleDeleteAccount();
    } finally { setPwLoading(false); }
  };

  const saveBudgets = async () => {
    const token = getToken();
    await fetch(`${BACKEND_URL}/user/budget-settings`, { method:"PUT", headers:{"Content-Type":"application/json",Authorization:`Bearer ${token}`}, body:JSON.stringify({ budgets: budgetOn ? budgets : {} }) });
    setBudgetSaved(true); setTimeout(() => setBudgetSaved(false), 2500);
  };

  // The on/off state is derived from whether any targets are saved, so turning
  // the tracker OFF has to persist an empty set — otherwise the next dashboard/
  // settings load reads the old targets back and flips it on again.
  const toggleBudgetTracker = async () => {
    const next = !budgetOn;
    setBudgetOn(next);
    if (!next) {
      setBudgets({});
      const token = getToken();
      try {
        await fetch(`${BACKEND_URL}/user/budget-settings`, {
          method: "PUT",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ budgets: {} }),
        });
      } catch {
        setBudgetOn(!next);  // revert the toggle if the clear didn't persist
      }
    }
  };

  const downloadAuditPack = async () => {
    setDownloading(true);
    const token = getToken();
    const body: Record<string,string> = { period };
    if (period === "custom") {
      if (!customFrom || !customTo) {
        await noticeDialog({
          title: lang === "si" ? "දින දෙකම තෝරන්න" : "Select both dates",
          message: lang === "si" ? "කරුණාකර 'සිට' සහ 'දක්වා' දින දෙකම තෝරන්න." : "Please select both From and To dates.",
        });
        setDownloading(false);
        return;
      }
      body.date_from = customFrom;
      body.date_to   = customTo;
    }
    try {
      const res = await fetch(`${BACKEND_URL}/reports/audit-pack`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        await noticeDialog({
          title: lang === "si" ? "නිර්යාතය අසාර්ථකයි" : "Export failed",
          message: lang === "si" ? "කරුණාකර නැවත උත්සාහ කරන්න." : "Please try again.",
        });
        return;
      }
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href = url;
      a.download = `sme_gpt_export_${period}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      await noticeDialog({
        title: lang === "si" ? "නිර්යාතය අසාර්ථකයි" : "Export failed",
        message: lang === "si" ? "බැක්එන්ඩ් ක්‍රියාත්මක වන බව සහතික කරගන්න." : "Make sure the backend is running.",
      });
    }
    finally { setDownloading(false); }
  };

  const t  = ui[lang];
  const si = lang === "si";
  const TABS: { key: Tab; icon: string; en: string; si: string; den: string; dsi: string }[] = [
    { key:"account",     icon:"person",   en:"Account",     si:"ගිණුම",     den:"Profile and business details", dsi:"පැතිකඩ සහ ව්‍යාපාර විස්තර" },
    { key:"security",    icon:"security", en:"Security",    si:"ආරක්ෂාව",   den:"Password, 2FA and sessions",   dsi:"මුරපදය, 2FA සහ සැසි" },
    { key:"preferences", icon:"tune",     en:"Preferences", si:"කැමැත්ත",   den:"Language, theme, processing",  dsi:"භාෂාව, තේමාව, සැකසීම" },
    { key:"budget",      icon:"savings",  en:"Budget",      si:"අයවැය",     den:"Monthly spending targets",     dsi:"මාසික ඉලක්ක" },
    { key:"export",      icon:"download", en:"Export",      si:"නිර්යාතය",  den:"Download your documents",      dsi:"ඔබගේ ලේඛන බාගන්න" },
  ];
  const activeTab = TABS.find((x) => x.key === tab) ?? TABS[0];

  const BUSINESS_FIELDS: { key: keyof ProfileData; label: string; icon: string }[] = [
    { key: "companyName",  label: t.organization, icon: "apartment" },
    { key: "businessUnit", label: t.businessUnit, icon: "account_tree" },
    { key: "jobTitle",     label: t.jobTitle,     icon: "badge" },
    { key: "country",      label: t.country,      icon: "public" },
    { key: "phone",        label: t.phone,        icon: "call" },
  ];

  const BUDGET_META: Record<string, { icon: string; color: string; si: string }> = {
    "Budgeted Cost": { icon: "account_balance_wallet", color: "var(--brand-mid)", si: "අයවැයගත පිරිවැය" },
    "Revenue":       { icon: "trending_up",            color: "#16a34a",          si: "ආදායම" },
    "Cash Inflow":   { icon: "south_west",             color: "#16a34a",          si: "මුදල් ලැබීම" },
    "Cash Outflow":  { icon: "north_east",             color: "#dc2626",          si: "මුදල් ගෙවීම" },
  };

  const changeLanguage = (next: AppLanguage) => {
    setLang(next);
    setStoredLanguage(next);
    setForm(p => ({ ...p, primaryLanguage: next }));
    handleSaveLanguage(next);
  };

  const displayName = form.fullName || session?.fullName || "";
  const initials = displayName.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]?.toUpperCase()).join("") || "U";

  const avatar = (size: number) => (
    <div
      className="flex shrink-0 items-center justify-center overflow-hidden rounded-full font-bold text-white shadow-md ring-4"
      style={{ width: size, height: size, background: "#c97b5a", fontSize: size * 0.36, ["--tw-ring-color" as string]: "var(--surface)" }}
    >
      {form.profileImage
        // eslint-disable-next-line @next/next/no-img-element
        ? <img src={form.profileImage} alt="Profile" className="h-full w-full object-cover" />
        : initials}
    </div>
  );

  const signOutButton = (extra: string) => (
    <button onClick={() => { void logoutUser(); }}
      className={`w-full items-center justify-center gap-2 rounded-2xl px-4 py-3 text-[13px] font-bold transition hover:opacity-85 ${extra}`}
      style={{ border:"1px solid var(--danger-border)", background:"var(--danger-tint)", color:"var(--danger)" }}>
      <span className="material-symbols-outlined text-[18px]">logout</span>
      {t.signOut}
    </button>
  );

  if (!session) return null;

  return (
    <PageShell
      backLabel={t.backToDashboard}
      title={si ? "සැකසීම්" : "Settings"}
      subtitle={si ? "ඔබගේ ගිණුම, ආරක්ෂාව සහ කැමැත්ත කළමනාකරණය කරන්න." : "Manage your account, security and preferences."}
      width="standard"
      fixedDesktop
    >
      <div className="grid gap-6 lg:h-full lg:min-h-0 lg:grid-cols-[280px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)] xl:gap-8">
        {/* ── Left: profile summary + section menu ──────────────────── */}
        <aside className="settings-panel min-w-0 space-y-4 lg:flex lg:min-h-0 lg:flex-col lg:overflow-y-auto">
          {/* Compact profile summary (desktop). Hidden on very short screens so
              the section menu always fits. */}
          <div className="ui-card hidden shrink-0 items-center gap-3 p-3.5 lg:flex lg:[@media(max-height:640px)]:hidden"
            style={{ background: "linear-gradient(125deg, color-mix(in srgb, var(--hero-to) 10%, var(--surface)) 0%, var(--surface) 70%)" }}>
            {avatar(44)}
            <div className="min-w-0 flex-1">
              <p className="truncate text-[14px] font-extrabold text-[var(--text-1)]">{displayName}</p>
              <p className="truncate text-[11px] text-[var(--text-3)]">{session.email}</p>
              {(form.jobTitle || form.companyName) && (
                <p className="mt-0.5 truncate text-[11px] font-semibold" style={{ color: "var(--brand-mid)" }}>
                  {[form.jobTitle, form.companyName].filter(Boolean).join(" · ")}
                </p>
              )}
            </div>
          </div>

          <nav
            className="ui-card -mx-1 flex shrink-0 gap-1 overflow-x-auto p-1.5 lg:mx-0 lg:flex-col lg:overflow-visible lg:p-2"
            aria-label={si ? "සැකසීම් කොටස්" : "Settings sections"}
            data-no-swipe
          >
            {TABS.map(tb => {
              const active = tab === tb.key;
              return (
                <button
                  key={tb.key}
                  onClick={() => setTab(tb.key)}
                  aria-current={active ? "page" : undefined}
                  className="relative flex shrink-0 items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-left transition lg:gap-3 lg:px-3 lg:py-3"
                  style={{ background: active ? "var(--brand-tint)" : "transparent" }}
                >
                  {active && (
                    <span className="absolute left-0 top-1/2 hidden h-6 w-[3px] -translate-y-1/2 rounded-r-full lg:block"
                      style={{ background: "var(--brand-mid)" }} aria-hidden />
                  )}
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg lg:h-9 lg:w-9"
                    style={active
                      ? { background: "var(--brand-mid)", color: "#fff" }
                      : { background: "var(--surface-2)", color: "var(--text-3)" }}
                  >
                    <span className="material-symbols-outlined text-[18px]">{tb.icon}</span>
                  </span>
                  <span className="min-w-0">
                    <span className="block whitespace-nowrap text-[13px] font-bold"
                      style={{ color: active ? "var(--brand-mid)" : "var(--text-1)" }}>
                      {si ? tb.si : tb.en}
                    </span>
                    <span className="hidden truncate text-[11px] text-[var(--text-3)] lg:block">{si ? tb.dsi : tb.den}</span>
                  </span>
                </button>
              );
            })}
          </nav>

          {/* Desktop: pinned to the bottom of this column, visible from every section. */}
          {signOutButton("hidden lg:mt-auto lg:flex")}
        </aside>

        {/* ── Right: active section ─────────────────────────────────── */}
        <section key={tab} className="settings-panel rise-in min-w-0 lg:min-h-0 lg:overflow-y-auto">
          <div className="mb-5 flex items-center gap-3 lg:hidden">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl" style={{ background: "var(--brand-tint)", color: "var(--brand-mid)" }}>
              <span className="material-symbols-outlined text-[21px]">{activeTab.icon}</span>
            </span>
            <div>
              <h2 className="text-[19px] font-extrabold tracking-tight text-[var(--text-1)]">{si ? activeTab.si : activeTab.en}</h2>
              <p className="text-[12px] text-[var(--text-3)]">{si ? activeTab.dsi : activeTab.den}</p>
            </div>
          </div>

          {message && (
            <p className="mb-4 flex items-center gap-2 rounded-xl px-4 py-2.5 text-[13px] font-medium"
              style={message.toLowerCase().includes("fail") || message.toLowerCase().includes("error")
                ? { background:"var(--danger-tint)", color:"var(--danger)", border: "1px solid var(--danger-border)" }
                : { background:"var(--success-tint)", color:"var(--success)", border: "1px solid var(--success-border)" }}>
              <span className="material-symbols-outlined text-[18px]">
                {message.toLowerCase().includes("fail") || message.toLowerCase().includes("error") ? "error" : "check_circle"}
              </span>
              {message}
            </p>
          )}

          {/* ─── ACCOUNT TAB ──────────────────────────────────────────── */}
          {tab === "account" && (
            <>
              {/* Profile card */}
              <Card>
                <div className="relative h-24 sm:h-28 lg:hidden" style={{ background: "linear-gradient(125deg, var(--hero-from) 0%, var(--hero-to) 100%)" }}>
                  <div className="pointer-events-none absolute -right-10 -top-16 h-48 w-48 rounded-full bg-white/10 blur-3xl" aria-hidden />
                  <div
                    className="pointer-events-none absolute inset-0 opacity-[0.08]"
                    style={{
                      backgroundImage: "linear-gradient(rgba(255,255,255,.8) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.8) 1px, transparent 1px)",
                      backgroundSize: "28px 28px",
                    }}
                    aria-hidden
                  />
                </div>
                <div className="flex flex-wrap items-end justify-between gap-4 px-5 pb-5 sm:px-6 lg:items-center lg:pt-5">
                  <div className="relative -mt-8 flex min-w-0 items-end gap-4 lg:mt-0 lg:items-center">
                    {avatar(84)}
                    <div className="min-w-0 pb-1">
                      {isEditing
                        ? <input value={form.fullName} onChange={e => update("fullName",e.target.value)} placeholder={t.fullName} className="field-input h-10 w-full max-w-[260px] rounded-xl border px-3 text-[15px] font-bold" />
                        : <p className="truncate text-[18px] font-extrabold text-[var(--text-1)]">{displayName}</p>}
                      <p className="truncate text-[13px] text-[var(--text-2)]">{session.email}</p>
                    </div>
                  </div>
                  {!isEditing
                    ? <button onClick={() => setIsEditing(true)} className="flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-[13px] font-bold text-white shadow-sm transition hover:opacity-90" style={{ background:"var(--brand)" }}>
                        <span className="material-symbols-outlined text-[17px]">edit</span>{t.editProfileBtn}
                      </button>
                    : <div className="flex gap-2">
                        <button onClick={() => setIsEditing(false)} className="rounded-xl px-4 py-2.5 text-[13px] font-semibold transition hover:bg-[var(--surface-2)]" style={{ border:"1px solid var(--border)", color:"var(--text-2)" }}>{t.cancel}</button>
                        <button onClick={handleSave} disabled={saving} className="flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-[13px] font-bold text-white disabled:opacity-60" style={{ background:"var(--brand)" }}>
                          <span className="material-symbols-outlined text-[17px]">{saving ? "progress_activity" : "check"}</span>
                          {saving ? "Saving…" : "Save"}
                        </button>
                      </div>}
                </div>
              </Card>

              <SectionLabel icon="business" title={t.businessStructure} />
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {BUSINESS_FIELDS.map(f => (
                  <div key={f.key} className="ui-card flex items-center gap-3.5 p-4"
                    style={isEditing ? { borderColor: "color-mix(in srgb, var(--brand-mid) 35%, var(--border))" } : undefined}>
                    <IconTile icon={f.icon} bg="var(--surface-2)" color="var(--text-2)" />
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-[var(--text-3)]">{f.label}</p>
                      {isEditing
                        ? <input value={String(form[f.key] ?? "")} onChange={e => update(f.key, e.target.value)}
                            className="field-input mt-1 w-full rounded-lg border px-2.5 py-1.5 text-[14px]" />
                        : <p className="mt-0.5 truncate text-[14px] font-semibold text-[var(--text-1)]">{String(form[f.key] || "—")}</p>}
                    </div>
                  </div>
                ))}
              </div>

              {signOutButton("mt-7 flex lg:hidden")}
            </>
          )}

          {/* ─── SECURITY TAB ─────────────────────────────────────────── */}
          {tab === "security" && (
            <>
              {/* Security status */}
              <div className="mb-5 flex items-start gap-3 rounded-[1.25rem] p-4"
                style={form.twoFactorEnabled
                  ? { background: "var(--success-tint)", border: "1px solid var(--success-border)", color: "var(--success)" }
                  : { background: "var(--warn-tint)", border: "1px solid var(--warn-border)", color: "var(--warn)" }}>
                <span className="material-symbols-outlined text-[22px]">{form.twoFactorEnabled ? "verified_user" : "gpp_maybe"}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-[14px] font-bold">
                    {form.twoFactorEnabled
                      ? (si ? "ඔබගේ ගිණුම හොඳින් ආරක්ෂිතයි" : "Your account is well protected")
                      : (si ? "ආරක්ෂාව වැඩි කරන්න" : "Strengthen your account")}
                  </p>
                  <p className="mt-0.5 text-[12px] opacity-90">
                    {form.twoFactorEnabled
                      ? (si ? "ද්වි-සාධක සත්‍යාපනය සක්‍රියයි." : "Two-factor authentication is on.")
                      : (si ? "අමතර ආරක්ෂාවක් සඳහා ද්වි-සාධක සත්‍යාපනය සක්‍රිය කරන්න." : "Turn on two-factor authentication for an extra layer of protection.")}
                  </p>
                </div>
              </div>

              <div className="grid gap-y-7 xl:grid-cols-2 xl:gap-x-5">
              <div className="min-w-0">
              <SectionLabel icon="security" title={t.corporateSecurity} />
              <Card>
                <ChevronRow icon="key" title={t.changePassword} subtitle={t.resetPasswordSubtitle}
                  iconBg="var(--danger-tint)" iconColor="var(--danger)"
                  onClick={() => router.push("/forgot-password")} />
                <Divider />
                <SettingRow icon="shield" title={t.twoFactor} subtitle={t.twoFactorSubtitle}>
                  <Toggle enabled={form.twoFactorEnabled} onClick={handleToggle2FA} label={t.twoFactor} />
                </SettingRow>
                <Divider />
                <ChevronRow icon="devices" title={t.sessionManagement} subtitle={t.sessionSubtitle}
                  iconBg="var(--warn-tint)" iconColor="var(--warn)"
                  onClick={() => router.push("/session-management")} />
              </Card>

              </div>
              <div className="min-w-0">
              <SectionLabel icon="delete_forever" title={t.dangerZoneTitle} danger />
              <Card className="!border-[var(--danger-border)]">
                <ChevronRow icon="table_view" title={t.exportMyData} subtitle={t.exportSubtitle}
                  iconBg="var(--info-tint)" iconColor="var(--info)"
                  onClick={() => { setPwAction("export"); setPwInput(""); setPwError(""); }} />
                <Divider />
                <ChevronRow icon="delete_forever" title={t.deleteAccount} subtitle={t.deleteSubtitle}
                  iconBg="var(--danger-tint)" iconColor="var(--danger)"
                  onClick={() => { setPwAction("delete"); setPwInput(""); setPwError(""); }} />
              </Card>
              </div>
              </div>

              {/* Password modal */}
              {pwAction && (
                <div data-no-swipe className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 px-4 backdrop-blur-sm"
                  onClick={e => { if (e.target === e.currentTarget) setPwAction(null); }}>
                  <div className="rise-in w-full max-w-[400px] rounded-3xl p-6 shadow-2xl" style={{ background:"var(--surface)", border: "1px solid var(--border)" }}>
                    <span className="flex h-12 w-12 items-center justify-center rounded-2xl"
                      style={pwAction === "delete"
                        ? { background: "var(--danger-tint)", color: "var(--danger)" }
                        : { background: "var(--brand-tint)", color: "var(--brand-mid)" }}>
                      <span className="material-symbols-outlined text-[24px]">{pwAction === "delete" ? "delete_forever" : "lock"}</span>
                    </span>
                    <p className="mt-4 text-[17px] font-extrabold text-[var(--text-1)]">{t.passwordConfirmTitle}</p>
                    <p className="mt-1 text-[13px] text-[var(--text-2)]">{t.passwordConfirmHint}</p>
                    <input type="password" value={pwInput} onChange={e => { setPwInput(e.target.value); setPwError(""); }}
                      onKeyDown={e => { if (e.key === "Enter") handlePasswordConfirm(); }}
                      placeholder="••••••••" autoFocus
                      className="field-input mt-4 w-full rounded-xl border px-4 py-3 text-[15px] transition" />
                    {pwError && <p className="mt-2 text-[12px] text-red-600">{pwError}</p>}
                    <div className="mt-5 flex gap-3">
                      <button onClick={() => setPwAction(null)} className="flex-1 rounded-xl py-2.5 text-[13px] font-semibold transition hover:bg-[var(--surface-2)]" style={{ border:"1px solid var(--border)", color:"var(--text-2)" }}>{t.cancel}</button>
                      <button onClick={handlePasswordConfirm} disabled={pwLoading || !pwInput.trim()}
                        className="flex-1 rounded-xl py-2.5 text-[13px] font-bold text-white disabled:opacity-50"
                        style={{ background: pwAction === "delete" ? "#dc2626" : "var(--brand)" }}>
                        {pwLoading ? "Verifying…" : pwAction === "export" ? t.downloadExcel : t.deleteAccount}
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {/* ─── PREFERENCES TAB ──────────────────────────────────────── */}
          {tab === "preferences" && (
            <>
              <div className="grid gap-y-7 xl:grid-cols-2 xl:gap-x-5">
              <div className="min-w-0">
              <SectionLabel icon="language" title={t.documentProcessing} />
              <Card>
                <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
                  <div className="flex min-w-0 items-center gap-4">
                    <IconTile icon="translate" bg="var(--brand-tint)" color="var(--brand-mid)" />
                    <div className="min-w-0">
                      <p className="text-[14px] font-semibold text-[var(--text-1)]">{t.primaryLanguage}</p>
                      <p className="mt-0.5 text-[12px] text-[var(--text-3)]">{si ? "යෙදුම සහ පිළිතුරු සඳහා" : "Used across the app and in answers"}</p>
                    </div>
                  </div>
                  <div className="inline-flex rounded-xl p-1" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }} role="radiogroup">
                    {([["en", "English"], ["si", "සිංහල"]] as [AppLanguage, string][]).map(([code, label]) => {
                      const on = form.primaryLanguage === code;
                      return (
                        <button key={code} role="radio" aria-checked={on} onClick={() => { if (!on) changeLanguage(code); }}
                          className="rounded-lg px-4 py-1.5 text-[13px] font-bold transition"
                          style={on
                            ? { background: "var(--surface)", color: "var(--brand-mid)", boxShadow: "0 1px 4px rgba(0,0,0,0.1)" }
                            : { color: "var(--text-3)" }}>
                          {label}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <Divider />
                <SettingRow icon="category" title={t.autoClassify} subtitle="Invoice / PO / DN / Receipt">
                  <Toggle enabled={form.autoClassify} onClick={handleToggleAutoClassify} label={t.autoClassify} />
                </SettingRow>
              </Card>

              </div>
              <div className="min-w-0">
              <SectionLabel icon="palette" title={t.appearanceSection} />
              <div className="grid grid-cols-2 gap-3">
                {(["light", "dark"] as const).map(mode => {
                  const on = theme === mode;
                  const dark = mode === "dark";
                  return (
                    <button key={mode} onClick={() => { if (!on) toggleTheme(); }} aria-pressed={on}
                      className="ui-card ui-card-hover overflow-hidden p-3 text-left"
                      style={on ? { borderColor: "var(--brand-mid)", boxShadow: "0 0 0 3px var(--brand-ring)" } : undefined}>
                      {/* Mini preview */}
                      <div className="flex h-24 gap-1.5 rounded-xl p-2" style={{ background: dark ? "#15181d" : "#f0f4f8", border: `1px solid ${dark ? "#2d333d" : "#dce3ef"}` }}>
                        <div className="w-1/4 rounded-md" style={{ background: dark ? "#1c2027" : "#1a3560" }} />
                        <div className="flex flex-1 flex-col gap-1.5">
                          <div className="h-5 rounded-md" style={{ background: dark ? "linear-gradient(125deg,#1d3766,#2f5bb0)" : "linear-gradient(125deg,#1a3560,#2252b5)" }} />
                          <div className="flex flex-1 gap-1.5">
                            <div className="flex-1 rounded-md" style={{ background: dark ? "#1c2027" : "#ffffff" }} />
                            <div className="flex-1 rounded-md" style={{ background: dark ? "#1c2027" : "#ffffff" }} />
                          </div>
                        </div>
                      </div>
                      <div className="mt-3 flex items-center justify-between gap-2 px-1">
                        <span className="flex items-center gap-2 text-[13px] font-bold text-[var(--text-1)]">
                          <span className="material-symbols-outlined text-[18px]">{dark ? "dark_mode" : "light_mode"}</span>
                          {dark ? (si ? "අඳුරු" : "Dark") : (si ? "ආලෝක" : "Light")}
                        </span>
                        <span className="flex h-5 w-5 items-center justify-center rounded-full"
                          style={on ? { background: "var(--brand-mid)", color: "#fff" } : { border: "2px solid var(--border)" }}>
                          {on && <span className="material-symbols-outlined text-[14px]">check</span>}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
              <p className="mt-2.5 px-1 text-[12px] text-[var(--text-3)]">{theme === "dark" ? t.darkThemeActive : t.lightThemeActive}</p>

              <SectionLabel icon="format_size" title={si ? "අකුරු ප්‍රමාණය" : "Text size"} />
              <div className="ui-card p-1.5">
                <div className="grid grid-cols-4 gap-1" role="radiogroup" aria-label={si ? "අකුරු ප්‍රමාණය" : "Text size"}>
                  {TEXT_SIZES.map((ts, i) => {
                    const on = textSize === ts.key;
                    return (
                      <button key={ts.key} role="radio" aria-checked={on}
                        onClick={() => { setTextSize(ts.key); setStoredTextSize(ts.key); }}
                        className="flex flex-col items-center justify-end gap-1.5 rounded-xl px-1 pb-2.5 pt-3 transition hover:bg-[var(--surface-2)]"
                        style={on
                          ? { background: "var(--brand-tint)", color: "var(--brand-mid)", boxShadow: "inset 0 0 0 1.5px var(--brand-mid)" }
                          : { color: "var(--text-2)" }}>
                        <span className="font-extrabold leading-none" style={{ fontSize: [14, 17, 20, 24][i] }} aria-hidden>Aa</span>
                        <span className="text-center text-[11px] font-semibold leading-tight">{si ? ts.si : ts.en}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
              <p className="mt-2.5 px-1 text-[12px] text-[var(--text-3)]">
                {si ? "මුළු යෙදුමම විශාලනය කරයි. මෙම උපාංගයේ සුරැකේ." : "Resizes the whole app. Saved on this device."}
              </p>
              </div>
              </div>
            </>
          )}

          {/* ─── BUDGET TAB ──────────────────────────────────────────────── */}
          {tab === "budget" && (
            <Card>
              <SettingRow icon="savings" iconBg="var(--success-tint)" iconColor="var(--success)"
                title={si ? "මාසික අයවැය" : "Monthly Budget Tracker"}
                subtitle={si ? "ඉලක්ක නියම කරන්න — ඩැශ්බෝඩයේ සංසන්දනය දිස් වේ" : "Set monthly targets — a comparison widget appears on your dashboard."}>
                <Toggle enabled={budgetOn} onClick={toggleBudgetTracker} label={si ? "මාසික අයවැය" : "Monthly Budget Tracker"} />
              </SettingRow>
              {budgetOn ? (
                <>
                  <Divider />
                  <div className="space-y-4 px-5 py-5">
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                      {BUDGET_CATS.map(cat => {
                        const meta = BUDGET_META[cat] ?? { icon: "payments", color: "var(--brand-mid)", si: cat };
                        return (
                          <label key={cat} className="flex cursor-text items-center gap-3 rounded-2xl border px-4 py-3.5 transition focus-within:border-[var(--brand-mid)] focus-within:shadow-[0_0_0_3px_var(--brand-ring)]"
                            style={{ background:"var(--surface-2)", borderColor:"var(--border)" }}>
                            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
                              style={{ background: `color-mix(in srgb, ${meta.color} 13%, transparent)`, color: meta.color }}>
                              <span className="material-symbols-outlined text-[18px]">{meta.icon}</span>
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[12px] font-semibold text-[var(--text-2)]">{si ? meta.si : cat}</span>
                              <span className="mt-0.5 flex items-baseline gap-1.5">
                                <span className="text-[11px] font-bold text-[var(--text-3)]">LKR</span>
                                <input type="number" min="0" value={budgets[cat] || ""}
                                  onChange={e => setBudgets(b => ({ ...b, [cat]:e.target.value }))}
                                  placeholder="0"
                                  className="w-full min-w-0 bg-transparent text-[17px] font-extrabold text-[var(--text-1)] outline-none" />
                              </span>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                    <button onClick={saveBudgets}
                      className="flex items-center gap-2 rounded-xl px-5 py-2.5 text-[13px] font-bold text-white shadow-sm transition hover:opacity-90"
                      style={{ background: budgetSaved ? "#16a34a" : "var(--brand-mid)" }}>
                      <span className="material-symbols-outlined text-[17px]">{budgetSaved ? "check_circle" : "save"}</span>
                      {budgetSaved
                        ? (si ? "සුරකිනු ලැබිණි" : "Saved")
                        : (si ? "සුරකින්න" : "Save Budgets")}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <Divider />
                  <div className="flex flex-col items-center px-6 py-10 text-center">
                    <span className="material-symbols-outlined text-[36px] text-[var(--text-3)]">query_stats</span>
                    <p className="mt-2 max-w-sm text-[13px] text-[var(--text-3)]">
                      {si ? "ඉලක්ක සැකසීමට ඉහත ස්විචය සක්‍රිය කරන්න." : "Switch the tracker on to set targets for costs, revenue and cash flow."}
                    </p>
                  </div>
                </>
              )}
            </Card>
          )}

          {/* ─── EXPORT TAB ──────────────────────────────────────────────── */}
          {tab === "export" && (
            <Card>
              <div className="px-5 py-5 sm:px-6">
                <div className="flex items-start gap-4">
                  <IconTile icon="folder_zip" bg="var(--success-tint)" color="var(--success)" />
                  <div className="min-w-0">
                    <p className="text-[15px] font-bold text-[var(--text-1)]">{si ? "ලේඛන නිර්යාත" : "Document Export"}</p>
                    <p className="mt-0.5 text-[12px] leading-5 text-[var(--text-2)]">
                      {si
                        ? "ඔබ ඇති කාලසීමාව සඳහා සියලු ලේඛන Excel සහ JSON ලෙස ZIP ගොනුවකට."
                        : "Download all your documents for a selected period as a ZIP containing an Excel ledger and JSON summary."}
                    </p>
                  </div>
                </div>

                <p className="mb-2.5 mt-6 text-[11px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)]">
                  {si ? "කාලසීමාව" : "Period"}
                </p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5" role="radiogroup">
                  {PERIOD_OPTIONS.map(p => {
                    const on = period === p.key;
                    return (
                      <button key={p.key} role="radio" aria-checked={on} onClick={() => setPeriod(p.key)}
                        className="flex items-center justify-between gap-2 rounded-xl px-3.5 py-3 text-left text-[13px] font-semibold transition"
                        style={on
                          ? { border:"1.5px solid var(--brand-mid)", color:"var(--brand-mid)", background:"var(--brand-tint)" }
                          : { border:"1px solid var(--border)", color:"var(--text-2)", background:"var(--surface)" }}>
                        {si ? p.si : p.en}
                        <span className="material-symbols-outlined text-[17px]">{on ? "radio_button_checked" : "radio_button_unchecked"}</span>
                      </button>
                    );
                  })}
                </div>

                {/* Custom date range */}
                {period === "custom" && (
                  <div className="mt-4 grid max-w-md grid-cols-2 gap-3">
                    <label className="block">
                      <span className="mb-1 block text-[11px] font-semibold text-[var(--text-3)]">{si ? "සිට" : "From"}</span>
                      <input type="date" value={customFrom} onChange={e => setCustomFrom(e.target.value)}
                        className="field-input w-full rounded-xl border px-3 py-2 text-[13px]" />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[11px] font-semibold text-[var(--text-3)]">{si ? "දක්වා" : "To"}</span>
                      <input type="date" value={customTo} onChange={e => setCustomTo(e.target.value)}
                        className="field-input w-full rounded-xl border px-3 py-2 text-[13px]" />
                    </label>
                  </div>
                )}

                <div className="mt-6 grid gap-2 sm:grid-cols-2">
                  {[
                    { icon: "table_view", en: "Excel ledger of every document", si: "සියලු ලේඛනවල Excel ලෙජරය" },
                    { icon: "data_object", en: "JSON summary for your records", si: "වාර්තා සඳහා JSON සාරාංශය" },
                  ].map(x => (
                    <div key={x.icon} className="flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[12px] text-[var(--text-2)]" style={{ background: "var(--surface-2)" }}>
                      <span className="material-symbols-outlined text-[18px]" style={{ color: "var(--success)" }}>{x.icon}</span>
                      {si ? x.si : x.en}
                    </div>
                  ))}
                </div>

                <button onClick={downloadAuditPack} disabled={downloading}
                  className="mt-6 flex items-center gap-2 rounded-xl bg-green-600 px-5 py-3 text-[13px] font-bold text-white shadow-sm transition hover:bg-green-700 disabled:opacity-60">
                  {downloading
                    ? <span className="material-symbols-outlined animate-spin text-[17px]">progress_activity</span>
                    : <span className="material-symbols-outlined text-[17px]">download</span>}
                  {downloading
                    ? (si ? "සකස් කරමින්..." : "Generating…")
                    : (si ? "ZIP බාගන්න" : "Download ZIP")}
                </button>
              </div>
            </Card>
          )}
        </section>
      </div>
    </PageShell>
  );
}
