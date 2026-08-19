"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import ThemeToggle from "@/components/layout/ThemeToggle";
import { getSession, loginUser } from "@/lib/auth";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";

const features = [
  {
    icon: "document_scanner",
    title: "OCR Extraction",
    desc: "Extract structured data from invoices, POs and delivery notes.",
  },
  {
    icon: "translate",
    title: "Bilingual Support",
    desc: "English and Sinhala UI with seamless switching.",
  },
  {
    icon: "psychology",
    title: "Explainable AI",
    desc: "Every answer includes evidence and reasoning.",
  },
  {
    icon: "verified_user",
    title: "Secure Workspace",
    desc: "Per-user isolation, 2FA, and trusted-device management.",
  },
];

// Where to land after signing in. proxy.ts appends ?next=<path> when it bounces
// an unauthenticated request, so the user resumes where they were headed
// instead of always landing on the dashboard. Read straight from
// window.location rather than useSearchParams(), which would require wrapping
// this page in a Suspense boundary.
function nextDestination(): string {
  if (typeof window === "undefined") return "/dashboard";
  const next = new URLSearchParams(window.location.search).get("next");
  // Relative same-site paths only — an absolute URL here would turn the login
  // page into an open redirect. "//host" is absolute despite the leading
  // slash, hence the second check.
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return "/dashboard";
}

export default function LoginPage() {
  const router = useRouter();
  const [lang, setLang] = useState<AppLanguage>("en");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  // Gate the form on the session check. The httpOnly cookie is the source of
  // truth for whether you're already logged in, but confirming it needs an
  // async round-trip to /api/auth/me. Rendering the form first and redirecting
  // after made an already-logged-in user watch the login page flash by before
  // landing on the dashboard. Start in "checking" and show a neutral splash
  // until we know: redirect if there's a session, show the form if not.
  const [checkingSession, setCheckingSession] = useState(true);

  useEffect(() => {
    setLang(getStoredLanguage());
    let active = true;
    getSession()
      .then((s) => {
        if (!active) return;
        if (s) router.replace(nextDestination());
        else setCheckingSession(false);
      })
      .catch(() => { if (active) setCheckingSession(false); });
    return () => { active = false; };
  }, [router]);

  const t = ui[lang];

  if (checkingSession) {
    return (
      <div
        className="flex min-h-svh flex-col items-center justify-center gap-4"
        style={{ background: "var(--bg)" }}
      >
        <div
          className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px] font-black text-white"
          style={{ background: "var(--brand)" }}
        >
          S
        </div>
        <div
          className="h-6 w-6 animate-spin rounded-full border-2 border-t-transparent"
          style={{ borderColor: "var(--border)", borderTopColor: "transparent" }}
          role="status"
          aria-label="Loading"
        />
      </div>
    );
  }

  const handleLogin = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    const result = await loginUser(email, password);
    setLoading(false);

    if (!result.ok) {
      setError(result.data?.error || t.invalidLogin);
      return;
    }

    if (result.data?.requiresTwoFactor) {
      router.push(`/login/verify?token=${result.data.verificationToken}`);
      return;
    }

    if (result.data?.success) {
      if (result.data.token) {
        localStorage.setItem("token", result.data.token);
        router.replace(nextDestination());
      } else {
        setError("Login token not received. Please try again.");
      }
      return;
    }

    setError("Login failed.");
  };

  return (
    <div className="flex min-h-svh">
      {/* ── Left brand panel (desktop only) ──────────────────── */}
      <div
        className="relative hidden flex-col justify-between overflow-hidden px-12 py-10 lg:flex lg:w-5/12 xl:w-[42%]"
        style={{ background: "#1a2e4a" }}
      >
        {/* Subtle dot grid */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              "radial-gradient(circle, rgba(255,255,255,0.07) 1px, transparent 1px)",
            backgroundSize: "28px 28px",
          }}
        />

        <div className="relative z-10">
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 ring-1 ring-white/20">
              <span className="material-symbols-outlined text-[20px] text-white">
                account_tree
              </span>
            </div>
            <span className="text-xl font-extrabold tracking-tight text-white">
              SME-GPT
            </span>
          </div>

          <h2 className="mt-16 text-[34px] font-extrabold leading-tight tracking-tight text-white">
            Enterprise Document Intelligence
          </h2>
          <p className="mt-4 max-w-sm text-[14px] leading-7 text-white/60">
            OCR, bilingual extraction, discrepancy detection, and explainable AI — in one workspace.
          </p>

          <div className="mt-12 space-y-5">
            {features.map((f) => (
              <div key={f.title} className="flex items-start gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white/10">
                  <span className="material-symbols-outlined text-[16px] text-white/80">
                    {f.icon}
                  </span>
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
      <div
        className="flex w-full flex-col lg:w-7/12 xl:w-[58%]"
        style={{ background: "var(--bg)" }}
      >
        {/* Top bar */}
        <div className="flex items-center justify-end gap-2 px-6 py-4">
          <ThemeToggle />
          <LanguageSwitcher />
        </div>

        {/* Centered form */}
        <div className="flex flex-1 items-center justify-center px-6 pb-12 pt-2">
          <div className="w-full max-w-[400px]">
            {/* Mobile-only logo */}
            <div className="mb-8 flex items-center gap-3 lg:hidden">
              <div
                className="flex h-9 w-9 items-center justify-center rounded-xl"
                style={{ background: "var(--brand)" }}
              >
                <span className="material-symbols-outlined text-[18px] text-white">
                  account_tree
                </span>
              </div>
              <span className="text-lg font-extrabold text-[var(--text-1)]">SME-GPT</span>
            </div>

            <h1 className="text-[28px] font-extrabold tracking-tight text-[var(--text-1)]">
              {t.signIn}
            </h1>
            <p className="mt-1.5 text-[14px] text-[var(--text-2)]">
              Welcome back — sign in to your workspace.
            </p>

            <form onSubmit={handleLogin} className="mt-8 space-y-5">
              {/* Email */}
              <div>
                <label
                  className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.1em]"
                  style={{ color: "var(--text-2)" }}
                >
                  {t.businessEmail}
                </label>
                <div className="relative">
                  <span
                    className="material-symbols-outlined absolute left-3.5 top-1/2 -translate-y-1/2 text-[18px]"
                    style={{ color: "var(--text-3)" }}
                  >
                    business_center
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
              </div>

              {/* Password */}
              <div>
                <label
                  className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.1em]"
                  style={{ color: "var(--text-2)" }}
                >
                  {t.password}
                </label>
                <div className="relative">
                  <span
                    className="material-symbols-outlined absolute left-3.5 top-1/2 -translate-y-1/2 text-[18px]"
                    style={{ color: "var(--text-3)" }}
                  >
                    shield_lock
                  </span>
                  <input
                    type={showPassword ? "text" : "password"}
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    className="field-input h-12 w-full rounded-xl border pl-11 pr-11 text-[15px] transition"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(p => !p)}
                    className="absolute right-3.5 top-1/2 -translate-y-1/2 transition hover:opacity-70"
                    style={{ color: "var(--text-3)" }}
                    tabIndex={-1}
                  >
                    <span className="material-symbols-outlined text-[18px]">
                      {showPassword ? "visibility_off" : "visibility"}
                    </span>
                  </button>
                </div>
                <div className="mt-2 flex justify-end">
                  <button
                    type="button"
                    onClick={() => router.push("/forgot-password")}
                    className="text-[12px] font-semibold transition hover:opacity-75"
                    style={{ color: "var(--brand-mid)" }}
                  >
                    {t.forgotAccess}
                  </button>
                </div>
              </div>

              {error && (
                <p className="rounded-lg px-3 py-2 text-[13px] font-medium"
                   style={{ background: "var(--danger-tint)", border: "1px solid var(--danger-border)", color: "var(--danger)" }}>
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={loading}
                className="flex h-12 w-full items-center justify-center gap-2 rounded-xl text-[15px] font-bold text-white transition hover:opacity-90 disabled:opacity-60"
                style={{ background: "var(--brand)" }}
              >
                {loading ? (
                  <span className="material-symbols-outlined animate-spin text-[20px]">
                    progress_activity
                  </span>
                ) : (
                  <>
                    <span>{t.signIn}</span>
                    <span className="material-symbols-outlined text-[18px]">login</span>
                  </>
                )}
              </button>
            </form>

            <p className="mt-8 text-center text-[13px]" style={{ color: "var(--text-2)" }}>
              {t.noAccount}{" "}
              <a
                href="/signup"
                className="font-bold transition hover:opacity-75"
                style={{ color: "var(--brand-mid)" }}
              >
                {t.signUp}
              </a>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
