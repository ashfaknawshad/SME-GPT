"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import ThemeToggle from "@/components/layout/ThemeToggle";
import { getSession, signupUser } from "@/lib/auth";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";

export default function SignupPage() {
  const router = useRouter();
  const [lang, setLang] = useState<AppLanguage>("en");
  const [fullName, setFullName] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLang(getStoredLanguage());
    getSession().then((s) => { if (s) router.push("/dashboard"); });
  }, [router]);

  const t = ui[lang];

  const handleSignup = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    await signupUser({ fullName, companyName, email, password });
    setLoading(false);
    setMessage(t.signupSuccess);
    setTimeout(() => router.push("/login"), 1200);
  };

  return (
    <div className="flex min-h-svh">
      {/* ── Left brand panel (desktop only) ──────────────────── */}
      <div
        className="relative hidden flex-col justify-between overflow-hidden px-12 py-10 lg:flex lg:w-5/12 xl:w-[42%]"
        style={{ background: "#1a2e4a" }}
      >
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              "radial-gradient(circle, rgba(255,255,255,0.07) 1px, transparent 1px)",
            backgroundSize: "28px 28px",
          }}
        />
        <div className="relative z-10">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 ring-1 ring-white/20">
              <span className="material-symbols-outlined text-[20px] text-white">account_tree</span>
            </div>
            <span className="text-xl font-extrabold tracking-tight text-white">SME-GPT</span>
          </div>

          <h2 className="mt-16 text-[34px] font-extrabold leading-tight tracking-tight text-white">
            Set Up Your Secure Workspace
          </h2>
          <p className="mt-4 max-w-sm text-[14px] leading-7 text-white/60">
            Create an account to start uploading, analysing, and querying your financial documents.
          </p>

          <div className="mt-12 space-y-5">
            {[
              { icon: "storage", title: "Structured Storage", desc: "Documents, fields, and validation flows in one place." },
              { icon: "manage_accounts", title: "Per-User Isolation", desc: "Your data is private and separated from other accounts." },
              { icon: "auto_fix_high", title: "Auto-Classification", desc: "Invoices, POs, DNs classified automatically on upload." },
            ].map((f) => (
              <div key={f.title} className="flex items-start gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white/10">
                  <span className="material-symbols-outlined text-[16px] text-white/80">{f.icon}</span>
                </div>
                <div>
                  <p className="text-[13px] font-semibold text-white">{f.title}</p>
                  <p className="text-[12px] leading-5 text-white/50">{f.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
        <p className="relative z-10 text-[10px] font-bold uppercase tracking-[0.18em] text-white/25">
          Secure AI Workspace · v3.1
        </p>
      </div>

      {/* ── Right form panel ─────────────────────────────────── */}
      <div className="flex w-full flex-col lg:w-7/12 xl:w-[58%]" style={{ background: "var(--bg)" }}>
        <div className="flex items-center justify-end gap-2 px-6 py-4">
          <ThemeToggle />
          <LanguageSwitcher />
        </div>

        <div className="flex flex-1 items-center justify-center px-6 pb-12 pt-2">
          <div className="w-full max-w-[400px]">
            <div className="mb-8 flex items-center gap-3 lg:hidden">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl" style={{ background: "var(--brand)" }}>
                <span className="material-symbols-outlined text-[18px] text-white">account_tree</span>
              </div>
              <span className="text-lg font-extrabold text-[var(--text-1)]">SME-GPT</span>
            </div>

            <h1 className="text-[28px] font-extrabold tracking-tight text-[var(--text-1)]">
              {t.signUp}
            </h1>
            <p className="mt-1.5 text-[14px] text-[var(--text-2)]">
              {t.enterpriseSubtitle}
            </p>

            <form onSubmit={handleSignup} className="mt-8 space-y-4">
              {[
                { label: t.fullName, value: fullName, set: setFullName, type: "text", icon: "person", placeholder: t.fullName },
                { label: t.companyName, value: companyName, set: setCompanyName, type: "text", icon: "domain", placeholder: t.companyName },
                { label: t.businessEmail, value: email, set: setEmail, type: "email", icon: "business_center", placeholder: "name@company.com" },
                { label: t.password, value: password, set: setPassword, type: "password", icon: "shield_lock", placeholder: "••••••••" },
              ].map((field) => (
                <div key={field.label}>
                  <label
                    className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.1em]"
                    style={{ color: "var(--text-2)" }}
                  >
                    {field.label}
                  </label>
                  <div className="relative">
                    <span
                      className="material-symbols-outlined absolute left-3.5 top-1/2 -translate-y-1/2 text-[18px]"
                      style={{ color: "var(--text-3)" }}
                    >
                      {field.icon}
                    </span>
                    <input
                      type={field.type}
                      placeholder={field.placeholder}
                      value={field.value}
                      onChange={(e) => field.set(e.target.value)}
                      required
                      className="field-input h-12 w-full rounded-xl border pl-11 pr-4 text-[15px] transition"
                    />
                  </div>
                </div>
              ))}

              {message && (
                <p className="rounded-lg px-3 py-2 text-[13px] font-medium text-emerald-600"
                   style={{ background: "rgba(22,163,74,0.08)" }}>
                  {message}
                </p>
              )}

              <button
                type="submit"
                disabled={loading}
                className="mt-1 flex h-12 w-full items-center justify-center gap-2 rounded-xl text-[15px] font-bold text-white transition hover:opacity-90 disabled:opacity-60"
                style={{ background: "var(--brand)" }}
              >
                {loading ? (
                  <span className="material-symbols-outlined animate-spin text-[20px]">progress_activity</span>
                ) : (
                  <>
                    <span>{t.signUp}</span>
                    <span className="material-symbols-outlined text-[18px]">person_add</span>
                  </>
                )}
              </button>
            </form>

            <p className="mt-8 text-center text-[13px]" style={{ color: "var(--text-2)" }}>
              {t.haveAccount}{" "}
              <a
                href="/login"
                className="font-bold transition hover:opacity-75"
                style={{ color: "var(--brand-mid)" }}
              >
                {t.signIn}
              </a>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
