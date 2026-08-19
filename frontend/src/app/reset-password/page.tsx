"use client";

import { FormEvent, Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import ThemeToggle from "@/components/layout/ThemeToggle";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";

function ResetPasswordContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";

  const [lang, setLang] = useState<AppLanguage>("en");
  const [message, setMessage] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { setLang(getStoredLanguage()); }, []);
  const t = ui[lang];

  useEffect(() => {
    if (!token) {
      setMessage(t.rpInvalidLink);
    } else {
      setMessage(t.rpEnterNew);
    }
  }, [token, t]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSuccess("");

    if (!token) {
      setError(t.rpInvalidLink);
      return;
    }

    if (!password || !confirmPassword) {
      setError(t.rpFillAll);
      return;
    }

    if (password !== confirmPassword) {
      setError(t.rpNoMatch);
      return;
    }

    setLoading(true);

    try {
      const res = await fetch("/api/auth/reset-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          token,
          password,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || t.rpFailed);
      }

      setSuccess(t.rpSuccess);
      setTimeout(() => {
        router.push("/login");
      }, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.genericError);
    } finally {
      setLoading(false);
    }
  };

  return (
    <MobileShell>
      <div className="px-4 py-8" style={{ background: "var(--bg)" }}>
        <div className="mx-auto mb-4 flex max-w-[520px] items-center justify-end gap-2">
          <LanguageSwitcher />
          <ThemeToggle />
        </div>
        <div
          className="mx-auto max-w-[520px] rounded-[30px] p-8 shadow-sm"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
        >
          <h1 className="text-center text-[28px] font-extrabold text-[var(--text-1)]">
            {t.rpTitle}
          </h1>

          <p className="mt-4 text-center text-[14px] text-[var(--text-2)]">
            {message}
          </p>

          {token && (
            <form onSubmit={handleSubmit} className="mt-8 space-y-5">
              <div>
                <label className="mb-2 block text-[12px] font-semibold text-[var(--text-2)]">
                  {t.rpNewPassword}
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={t.rpNewPlaceholder}
                  className="field-input h-12 w-full rounded-2xl border px-4 text-[15px] outline-none transition"
                  required
                />
              </div>

              <div>
                <label className="mb-2 block text-[12px] font-semibold text-[var(--text-2)]">
                  {t.rpConfirmPassword}
                </label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder={t.rpConfirmPlaceholder}
                  className="field-input h-12 w-full rounded-2xl border px-4 text-[15px] outline-none transition"
                  required
                />
              </div>

              {error && (
                <p className="text-[13px] font-medium text-red-600">{error}</p>
              )}

              {success && (
                <p className="text-[13px] font-medium text-green-600">{success}</p>
              )}

              <button
                type="submit"
                disabled={loading}
                className="flex h-12 w-full items-center justify-center rounded-2xl text-[16px] font-bold text-white transition hover:translate-y-[1px] hover:opacity-95 disabled:opacity-60"
                style={{ background: "var(--brand)" }}
              >
                {loading ? t.rpResetting : t.rpTitle}
              </button>
            </form>
          )}
        </div>
      </div>
    </MobileShell>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<div className="p-6 text-center">Loading...</div>}>
      <ResetPasswordContent />
    </Suspense>
  );
}
