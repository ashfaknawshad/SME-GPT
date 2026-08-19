"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ThemeToggle from "@/components/layout/ThemeToggle";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [lang, setLang] = useState<AppLanguage>("en");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [isError, setIsError] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => { setLang(getStoredLanguage()); }, []);
  const t = ui[lang];

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage("");

    try {
      const res = await fetch("/api/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });

      const data = await res.json();

      if (!res.ok) {
        setIsError(true);
        setMessage(data.error || t.genericError);
        return;
      }

      setIsError(false);
      setMessage(t.fpSent);
    } catch {
      setIsError(true);
      setMessage(t.requestFailed);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="flex min-h-svh flex-col items-center justify-center px-4 py-10"
      style={{ background: "var(--bg)" }}
    >
      {/* Top-right controls */}
      <div className="fixed right-4 top-4 flex items-center gap-2">
        <LanguageSwitcher />
        <ThemeToggle />
      </div>

      <div
        className="w-full max-w-[420px] rounded-2xl p-8 shadow-sm"
        style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
      >
        {/* Logo */}
        <div
          className="mx-auto mb-6 flex h-12 w-12 items-center justify-center rounded-xl"
          style={{ background: "var(--brand)" }}
        >
          <span className="material-symbols-outlined text-[22px] text-white">lock_reset</span>
        </div>

        <h1 className="text-center text-[24px] font-extrabold tracking-tight text-[var(--text-1)]">
          {t.fpTitle}
        </h1>
        <p className="mt-2 text-center text-[13px] text-[var(--text-2)]">
          {t.fpSubtitle}
        </p>

        <form onSubmit={handleSubmit} className="mt-8 space-y-4">
          <div className="relative">
            <span
              className="material-symbols-outlined absolute left-3.5 top-1/2 -translate-y-1/2 text-[18px]"
              style={{ color: "var(--text-3)" }}
            >
              mail
            </span>
            <input
              type="email"
              placeholder="name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="field-input h-12 w-full rounded-xl border pl-11 pr-4 text-[15px] transition"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="flex h-12 w-full items-center justify-center gap-2 rounded-xl text-[15px] font-bold text-white transition hover:opacity-90 disabled:opacity-60"
            style={{ background: "var(--brand)" }}
          >
            {loading ? (
              <span className="material-symbols-outlined animate-spin text-[20px]">progress_activity</span>
            ) : (
              t.fpSendLink
            )}
          </button>
        </form>

        {message && (
          <p
            className="mt-4 rounded-lg px-3 py-2 text-center text-[13px] font-medium"
            style={
              isError
                ? { color: "#dc2626", background: "rgba(220,38,38,0.08)" }
                : { color: "#16a34a", background: "rgba(22,163,74,0.08)" }
            }
          >
            {message}
          </p>
        )}

        <button
          onClick={() => router.push("/login")}
          className="mt-6 flex w-full items-center justify-center gap-1.5 text-[13px] font-semibold transition hover:opacity-75"
          style={{ color: "var(--text-2)" }}
        >
          <span className="material-symbols-outlined text-[16px]">arrow_back</span>
          {t.backToSignIn}
        </button>
      </div>
    </div>
  );
}
