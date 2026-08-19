"use client";
/**
 * PageShell — unified page wrapper for every non-auth page.
 *
 * Provides:
 *  • Consistent background (var(--bg)) across light/dark themes
 *  • Standardised max-width + padding
 *  • Top bar: optional back button on the left, ThemeToggle + LanguageSwitcher on the right
 *  • Page title + optional subtitle below the top bar
 *  • BottomNav at the bottom
 *
 * Width variants:
 *  "narrow"   → max-w-[760px]   — Settings, Query
 *  "standard" → max-w-[960px]   — most pages (default)
 *  "wide"     → max-w-[1180px]  — Dashboard, Analysis, Manual Entry
 */

import { useRouter } from "next/navigation";
import MobileShell from "./MobileShell";
import BottomNav from "./BottomNav";
import ThemeToggle from "./ThemeToggle";
import LanguageSwitcher from "./LanguageSwitcher";

type Width = "narrow" | "standard" | "wide";

const MAX_W: Record<Width, string> = {
  narrow:   "max-w-[760px]",
  standard: "max-w-[960px]",
  wide:     "max-w-[1180px]",
};

interface PageShellProps {
  /** Page title shown below the top bar */
  title?: string;
  /** Smaller subtitle below the title */
  subtitle?: string;
  /** If provided, shows a ← back button on the top-left */
  backLabel?: string;
  /** Override back destination (defaults to router.back()) */
  backHref?: string;
  /** Layout width variant */
  width?: Width;
  /** Extra className on the <main> container */
  mainClassName?: string;
  /** Render custom right-side content in the top bar (replaces default ThemeToggle+Lang) */
  topBarRight?: React.ReactNode;
  /** Render custom left content in the top bar alongside/instead of back button */
  topBarLeft?: React.ReactNode;
  children: React.ReactNode;
}

export default function PageShell({
  title,
  subtitle,
  backLabel,
  backHref,
  width = "standard",
  mainClassName = "",
  topBarRight,
  topBarLeft,
  children,
}: PageShellProps) {
  const router = useRouter();

  const handleBack = () => {
    if (backHref) router.push(backHref);
    else router.back();
  };

  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <main
          className={`mx-auto w-full ${MAX_W[width]} px-4 py-6 sm:px-6 ${mainClassName}`}
        >
          {/* ── Top bar ──────────────────────────────────────────────── */}
          <div className="mb-5 flex items-center justify-between gap-3">
            {/* Left side */}
            <div className="flex items-center gap-3">
              {backLabel && (
                <button
                  onClick={handleBack}
                  className="flex items-center gap-1 text-[13px] font-semibold transition hover:opacity-75"
                  style={{ color: "var(--brand-mid)" }}
                >
                  <span className="material-symbols-outlined text-[16px]">arrow_back</span>
                  Back
                </button>
              )}
              {topBarLeft}
            </div>

            {/* Right side */}
            <div className="flex items-center gap-2">
              {topBarRight ?? (
                <>
                  <ThemeToggle />
                  <LanguageSwitcher />
                </>
              )}
            </div>
          </div>

          {/* ── Page heading ─────────────────────────────────────────── */}
          {title && (
            <h1 className="text-[24px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[28px]">
              {title}
            </h1>
          )}
          {subtitle && (
            <p className="mt-1 text-[13px] text-[var(--text-2)]">{subtitle}</p>
          )}
          {(title || subtitle) && <div className="mt-5" />}

          {/* ── Page content ─────────────────────────────────────────── */}
          {children}
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}
