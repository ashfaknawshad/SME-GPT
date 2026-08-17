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

// ── Shared UI primitives (copied from profile) ────────────────────────────────
function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl" style={{ background:"var(--surface)", border:"1px solid var(--border)", boxShadow:"0 1px 4px rgba(0,0,0,0.05)" }}>
      {children}
    </div>
  );
}
function SectionLabel({ icon, title, danger }: { icon:string; title:string; danger?:boolean }) {
  return (
    <div className="mb-2 mt-6 flex items-center gap-2 px-1">
      <span className="material-symbols-outlined text-[15px]" style={{ color: danger ? "#dc2626" : "var(--text-3)" }}>{icon}</span>
      <p className="text-[11px] font-bold uppercase tracking-[0.1em]" style={{ color: danger ? "#dc2626" : "var(--text-3)" }}>{title}</p>
    </div>
  );
}
function Divider() { return <div className="mx-5" style={{ height:1, background:"var(--border)" }} />; }
function FieldRow({ label, value }: { label:string; value:string }) {
  return (
    <div className="flex items-center justify-between px-5 py-4">
      <p className="text-[13px] text-[var(--text-3)]">{label}</p>
      <p className="text-[13px] font-semibold text-[var(--text-1)]">{value || "—"}</p>
    </div>
  );
}
function Toggle({ enabled, onClick }: { enabled:boolean; onClick:()=>void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        position: "relative",
        display: "inline-flex",
        height: 28,
        width: 52,
        flexShrink: 0,
        borderRadius: 999,
        border: "none",
        cursor: "pointer",
        transition: "background-color 0.2s",
        background: enabled ? "#2563eb" : "#9ca3af",
        outline: "none",
        padding: 0,
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 3,
          left: enabled ? 27 : 3,
          height: 22,
          width: 22,
          borderRadius: 999,
          background: "#ffffff",
          boxShadow: "0 1px 4px rgba(0,0,0,0.3)",
          transition: "left 0.2s ease",
        }}
      />
    </button>
  );
}
function ChevronRow({ icon, title, subtitle, onClick, iconBg="#f3f4f6", iconColor="var(--text-3)" }: {
  icon:string; title:string; subtitle:string; onClick:()=>void; iconBg?:string; iconColor?:string;
}) {
  return (
    <button onClick={onClick} className="flex w-full items-center gap-4 px-5 py-4 text-left transition hover:bg-[var(--surface-2)]">
      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl" style={{ background:iconBg, color:iconColor }}>
        <span className="material-symbols-outlined text-[20px]">{icon}</span>
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-[14px] font-semibold text-[var(--text-1)]">{title}</p>
        <p className="text-[12px] text-[var(--text-3)] mt-0.5">{subtitle}</p>
      </div>
      <span className="material-symbols-outlined text-[20px]" style={{ color:"var(--text-3)" }}>chevron_right</span>
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
      const res  = await fetch("/api/auth/2fa", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ enable: next }),
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
  const TABS: { key: Tab; icon: string; en: string; si: string }[] = [
    { key:"account",      icon:"person",       en:"Account",      si:"ගිණුම"          },
    { key:"security",     icon:"security",     en:"Security",     si:"ආරක්ෂාව"        },
    { key:"preferences",  icon:"tune",         en:"Preferences",  si:"කැමැත්ත"       },
    { key:"budget",       icon:"savings",      en:"Budget",       si:"අයවැය"          },
    { key:"export",       icon:"download",     en:"Export",       si:"නිර්යාතය"      },
  ];

  if (!session) return null;

  return (
    <PageShell
      backLabel={t.backToDashboard}
      title={lang === "si" ? "සැකසීම්" : "Settings"}
      width="standard"
    >

          {/* Tab strip */}
          <div className="mt-5 grid grid-cols-5 gap-2 rounded-2xl p-2"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            {TABS.map(tb => (
              <button key={tb.key} onClick={() => setTab(tb.key)}
                className="flex flex-col items-center gap-1.5 rounded-xl px-2 py-3 text-[11px] font-semibold transition"
                style={tab === tb.key
                  ? { background: "var(--brand-mid)", color: "#fff", boxShadow: "0 2px 8px rgba(34,82,181,0.25)" }
                  : { color: "var(--text-3)" }}>
                <span className="material-symbols-outlined text-[20px]">{tb.icon}</span>
                {lang === "si" ? tb.si : tb.en}
              </button>
            ))}
          </div>

          <div className="mt-6 space-y-4">

            {/* ─── ACCOUNT TAB ──────────────────────────────────────────── */}
            {tab === "account" && (
              <>
                {/* Profile card */}
                <Card>
                  <div className="flex flex-wrap items-center justify-between gap-4 p-5">
                    <div className="flex items-center gap-4">
                      <div className="flex h-14 w-14 items-center justify-center rounded-full text-white" style={{ background:"#c97b5a" }}>
                        {form.profileImage
                          ? <img src={form.profileImage} alt="Profile" className="h-full w-full rounded-full object-cover" />
                          : <span className="material-symbols-outlined text-[26px]">person</span>}
                      </div>
                      <div>
                        {isEditing
                          ? <input value={form.fullName} onChange={e => update("fullName",e.target.value)} placeholder={t.fullName} className="field-input h-9 rounded-xl border px-3 text-[15px] font-bold" />
                          : <p className="text-[16px] font-bold text-[var(--text-1)]">{form.fullName || session.fullName}</p>}
                        <p className="text-[13px] text-[var(--text-2)]">{session.email}</p>
                      </div>
                    </div>
                    {!isEditing
                      ? <button onClick={() => setIsEditing(true)} className="rounded-xl px-4 py-2 text-[13px] font-bold text-white" style={{ background:"var(--brand)" }}>{t.editProfileBtn}</button>
                      : <div className="flex gap-2">
                          <button onClick={() => setIsEditing(false)} className="rounded-xl px-4 py-2 text-[13px] font-semibold" style={{ border:"1px solid var(--border)", color:"var(--text-2)" }}>Cancel</button>
                          <button onClick={handleSave} disabled={saving} className="rounded-xl px-4 py-2 text-[13px] font-bold text-white disabled:opacity-60" style={{ background:"var(--brand)" }}>{saving ? "Saving…" : "Save"}</button>
                        </div>}
                  </div>
                </Card>

                <SectionLabel icon="business" title={t.businessStructure} />
                <Card>
                  {isEditing ? (
                    <>
                      {[["companyName",t.organization],["businessUnit",t.businessUnit],["jobTitle",t.jobTitle],["country",t.country],["phone",t.phone]].map(([k,lbl],i) => (
                        <div key={k}>
                          {i > 0 && <Divider />}
                          <div className="flex items-center justify-between gap-3 px-5 py-3">
                            <p className="text-[13px] text-[var(--text-3)]">{lbl}</p>
                            <input value={(form as Record<string,unknown>)[k] as string} onChange={e => update(k as keyof ProfileData, e.target.value)}
                              className="field-input w-44 rounded-xl border px-3 py-1.5 text-[13px] text-right" />
                          </div>
                        </div>
                      ))}
                    </>
                  ) : (
                    <>
                      <FieldRow label={t.organization}  value={form.companyName}  />
                      <Divider />
                      <FieldRow label={t.businessUnit}  value={form.businessUnit} />
                      <Divider />
                      <FieldRow label={t.jobTitle}       value={form.jobTitle}    />
                      <Divider />
                      <FieldRow label={t.country}        value={form.country}     />
                      <Divider />
                      <FieldRow label={t.phone}          value={form.phone}       />
                    </>
                  )}
                </Card>

                {message && (
                  <p className="mt-2 rounded-xl px-4 py-2.5 text-center text-[13px] font-medium"
                    style={message.includes("fail") || message.includes("Failed")
                      ? { background:"rgba(220,38,38,0.08)", color:"#dc2626" }
                      : { background:"rgba(22,163,74,0.08)", color:"#16a34a" }}>
                    {message}
                  </p>
                )}

                <button onClick={() => { void logoutUser(); }}
                  className="mt-6 w-full rounded-2xl py-4 text-[15px] font-bold transition hover:opacity-90"
                  style={{ border:"1px solid rgba(220,38,38,0.25)", background:"rgba(220,38,38,0.05)", color:"#dc2626" }}>
                  {t.signOut}
                </button>
                <p className="mt-5 mb-2 text-center text-[11px] text-[var(--text-3)]">SME-GPT · {t.auditFooter}</p>
              </>
            )}

            {/* ─── SECURITY TAB ─────────────────────────────────────────── */}
            {tab === "security" && (
              <>
                <SectionLabel icon="security" title={t.corporateSecurity} danger />
                <Card>
                  <ChevronRow icon="key" title={t.changePassword} subtitle={t.resetPasswordSubtitle}
                    iconBg="rgba(220,38,38,0.1)" iconColor="#dc2626"
                    onClick={() => router.push("/forgot-password")} />
                  <Divider />
                  <div className="flex w-full items-center justify-between gap-4 px-5 py-4">
                    <div className="flex min-w-0 flex-1 items-center gap-4">
                      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl" style={{ background:"var(--brand-tint)", color:"var(--brand-mid)" }}>
                        <span className="material-symbols-outlined text-[20px]">shield</span>
                      </span>
                      <div className="min-w-0">
                        <p className="text-[14px] font-semibold text-[var(--text-1)]">{t.twoFactor}</p>
                        <p className="text-[12px] text-[var(--text-3)] mt-0.5">{t.twoFactorSubtitle}</p>
                      </div>
                    </div>
                    <Toggle enabled={form.twoFactorEnabled} onClick={handleToggle2FA} />
                  </div>
                  <Divider />
                  <ChevronRow icon="admin_panel_settings" title={t.sessionManagement} subtitle={t.sessionSubtitle}
                    iconBg="rgba(217,119,6,0.1)" iconColor="#d97706"
                    onClick={() => router.push("/session-management")} />
                </Card>

                <SectionLabel icon="delete_forever" title={t.dangerZoneTitle} danger />
                <Card>
                  <ChevronRow icon="table_view" title={t.exportMyData} subtitle={t.exportSubtitle}
                    iconBg="rgba(34,82,181,0.1)" iconColor="#2252b5"
                    onClick={() => { setPwAction("export"); setPwInput(""); setPwError(""); }} />
                  <Divider />
                  <ChevronRow icon="delete_forever" title={t.deleteAccount} subtitle={t.deleteSubtitle}
                    iconBg="rgba(220,38,38,0.1)" iconColor="#dc2626"
                    onClick={() => { setPwAction("delete"); setPwInput(""); setPwError(""); }} />
                </Card>

                {/* Password modal */}
                {pwAction && (
                  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
                    <div className="w-full max-w-[380px] rounded-2xl p-6 shadow-xl" style={{ background:"var(--surface)" }}>
                      <p className="text-[16px] font-extrabold text-[var(--text-1)]">{t.passwordConfirmTitle}</p>
                      <p className="mt-1 text-[13px] text-[var(--text-2)]">{t.passwordConfirmHint}</p>
                      <input type="password" value={pwInput} onChange={e => { setPwInput(e.target.value); setPwError(""); }}
                        onKeyDown={e => { if (e.key === "Enter") handlePasswordConfirm(); }}
                        placeholder="••••••••" autoFocus
                        className="field-input mt-4 w-full rounded-xl border px-4 py-3 text-[15px] transition" />
                      {pwError && <p className="mt-2 text-[12px] text-red-600">{pwError}</p>}
                      <div className="mt-4 flex gap-3">
                        <button onClick={() => setPwAction(null)} className="flex-1 rounded-xl py-2.5 text-[13px] font-semibold" style={{ border:"1px solid var(--border)", color:"var(--text-2)" }}>{t.cancel}</button>
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
                <SectionLabel icon="language" title={t.documentProcessing} />
                <Card>
                  <div className="flex w-full items-center justify-between gap-4 px-5 py-4">
                    <p className="text-[13px] text-[var(--text-1)]">{t.primaryLanguage}</p>
                    <select value={form.primaryLanguage}
                      onChange={e => {
                        const next = e.target.value as AppLanguage;
                        setLang(next);
                        setStoredLanguage(next);
                        setForm(p => ({ ...p, primaryLanguage: next }));
                        handleSaveLanguage(next);
                      }}
                      className="rounded-xl border px-3 py-1.5 text-[13px]" style={{ background:"var(--bg)", borderColor:"var(--border)", color:"var(--text-1)" }}>
                      <option value="en">English</option>
                      <option value="si">සිංහල</option>
                    </select>
                  </div>
                  <Divider />
                  <div className="flex w-full items-center justify-between gap-4 px-5 py-4">
                    <div className="min-w-0 flex-1">
                      <p className="text-[14px] font-semibold text-[var(--text-1)]">{t.autoClassify}</p>
                      <p className="text-[12px] text-[var(--text-3)] mt-0.5">Invoice / PO / DN</p>
                    </div>
                    <Toggle enabled={form.autoClassify} onClick={handleToggleAutoClassify} />
                  </div>
                </Card>

                <SectionLabel icon="palette" title={t.appearanceSection} />
                <Card>
                  <div className="flex w-full items-center justify-between gap-4 px-5 py-4">
                    <div className="flex min-w-0 flex-1 items-center gap-4">
                      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl" style={{ background:"var(--brand-tint)", color:"var(--brand-mid)" }}>
                        <span className="material-symbols-outlined text-[20px]">{theme === "dark" ? "dark_mode" : "light_mode"}</span>
                      </span>
                      <div className="min-w-0">
                        <p className="text-[14px] font-semibold text-[var(--text-1)]">{t.nightMode}</p>
                        <p className="text-[12px] text-[var(--text-3)] mt-0.5">{theme === "dark" ? t.darkThemeActive : t.lightThemeActive}</p>
                      </div>
                    </div>
                    <Toggle enabled={theme === "dark"} onClick={toggleTheme} />
                  </div>
                </Card>

              </>
            )}

            {/* ─── BUDGET TAB ──────────────────────────────────────────────── */}
            {tab === "budget" && (
              <Card>
                <div className="flex w-full items-center justify-between gap-4 px-6 py-5">
                  <div className="min-w-0 flex-1">
                    <p className="text-[15px] font-bold text-[var(--text-1)]">
                      {lang === "si" ? "මාසික අයවැය" : "Monthly Budget Tracker"}
                    </p>
                    <p className="mt-0.5 text-[12px] text-[var(--text-2)]">
                      {lang === "si"
                        ? "ඉලක්ක නියම කරන්න — ඩැශ්බෝඩයේ සංසන්දනය දිස් වේ"
                        : "Set monthly targets — comparison widget appears on your dashboard."}
                    </p>
                  </div>
                  <Toggle enabled={budgetOn} onClick={() => setBudgetOn(b => !b)} />
                </div>
                {budgetOn && (
                  <>
                    <Divider />
                    <div className="px-6 py-5 space-y-3">
                      <div className="grid gap-3">
                        {BUDGET_CATS.map(cat => (
                          <div key={cat} className="flex items-center gap-3 rounded-xl border px-4 py-3"
                            style={{ background:"var(--bg)", borderColor:"var(--border)" }}>
                            <span className="flex-1 text-[13px] font-medium text-[var(--text-1)]">{cat}</span>
                            <span className="text-[11px] text-[var(--text-3)]">LKR</span>
                            <input type="number" min="0" value={budgets[cat] || ""}
                              onChange={e => setBudgets(b => ({ ...b, [cat]:e.target.value }))}
                              placeholder="0"
                              className="w-28 bg-transparent text-right text-[14px] font-bold text-[var(--text-1)] outline-none" />
                          </div>
                        ))}
                      </div>
                      <button onClick={saveBudgets}
                        className="flex items-center gap-2 rounded-xl px-5 py-2.5 text-[13px] font-bold text-white hover:opacity-90 transition"
                        style={{ background:"var(--brand-mid)" }}>
                        <span className="material-symbols-outlined text-[16px]">save</span>
                        {budgetSaved
                          ? (lang === "si" ? "සුරකිනු ලැබිණි ✓" : "Saved ✓")
                          : (lang === "si" ? "සුරකින්න" : "Save Budgets")}
                      </button>
                    </div>
                  </>
                )}
              </Card>
            )}

            {/* ─── EXPORT TAB ──────────────────────────────────────────────── */}
            {tab === "export" && (
              <Card>
                <div className="px-6 py-5">
                  <p className="text-[15px] font-bold text-[var(--text-1)]">
                    {lang === "si" ? "ලේඛන නිර්යාත" : "Document Export"}
                  </p>
                  <p className="mt-0.5 text-[12px] text-[var(--text-2)]">
                    {lang === "si"
                      ? "ඔබ ඇති කාලසීමාව සඳහා සියලු ලේඛන Excel සහ JSON ලෙස ZIP ගොනුවකට."
                      : "Download all your documents for a selected period as a ZIP containing an Excel ledger and JSON summary."}
                  </p>

                  {/* Period selector */}
                  <div className="mt-4 flex flex-wrap gap-2">
                    {PERIOD_OPTIONS.map(p => (
                      <button key={p.key} onClick={() => setPeriod(p.key)}
                        className="rounded-xl px-4 py-2 text-[12px] font-semibold transition"
                        style={period === p.key
                          ? { border:"2px solid var(--brand-mid)", color:"var(--brand-mid)", background:"var(--brand-tint)" }
                          : { border:"1px solid var(--border)", color:"var(--text-2)", background:"var(--surface)" }}>
                        {lang === "si" ? p.si : p.en}
                      </button>
                    ))}
                  </div>

                  {/* Custom date range */}
                  {period === "custom" && (
                    <div className="mt-3 flex flex-wrap items-center gap-3">
                      <div>
                        <p className="mb-1 text-[11px] text-[var(--text-3)]">{lang === "si" ? "සිට" : "From"}</p>
                        <input type="date" value={customFrom} onChange={e => setCustomFrom(e.target.value)}
                          className="rounded-xl border px-3 py-2 text-[13px]"
                          style={{ background:"var(--bg)", borderColor:"var(--border)", color:"var(--text-1)" }} />
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] text-[var(--text-3)]">{lang === "si" ? "දක්වා" : "To"}</p>
                        <input type="date" value={customTo} onChange={e => setCustomTo(e.target.value)}
                          className="rounded-xl border px-3 py-2 text-[13px]"
                          style={{ background:"var(--bg)", borderColor:"var(--border)", color:"var(--text-1)" }} />
                      </div>
                    </div>
                  )}

                  <button onClick={downloadAuditPack} disabled={downloading}
                    className="mt-5 flex items-center gap-2 rounded-xl bg-green-600 px-5 py-2.5 text-[13px] font-bold text-white transition hover:bg-green-700 disabled:opacity-60">
                    {downloading
                      ? <span className="material-symbols-outlined animate-spin text-[16px]">progress_activity</span>
                      : <span className="material-symbols-outlined text-[16px]">download</span>}
                    {downloading
                      ? (lang === "si" ? "සකස් කරමින්..." : "Generating…")
                      : (lang === "si" ? "ZIP බාගන්න" : "Download ZIP")}
                  </button>
                </div>
              </Card>
            )}

          </div>
    </PageShell>
  );
}
