"use client";
/**
 * PageShell — unified page wrapper for every non-auth page.
 *
 * Provides:
 *  • Consistent background (var(--bg)) across light/dark themes
 *  • The shared TopBar (same header as the dashboard)
 *  • One full-width container (max 1600px) so every page lines up with the
 *    dashboard; on desktop the app sidebar sits to its left
 *  • Page title + subtitle, with optional page actions on the right
 *  • BottomNav (bottom bar on phones, sidebar on desktop)
 *
 * Width variants control only how wide the *content* may grow, never where it
 * starts, so titles line up identically on every page:
 *  "narrow"   → content up to 880px   — focused forms
 *  "standard" → full width            — most pages (default)
 *  "wide"     → full width            — kept for compatibility
 */

import MobileShell from "./MobileShell";
import BottomNav from "./BottomNav";
import TopBar from "./TopBar";
import PageHeading from "./PageHeading";

type Width = "narrow" | "standard" | "wide";

const CONTENT_W: Record<Width, string> = {
  narrow:   "max-w-[880px]",
  standard: "",
  wide:     "",
};

interface PageShellProps {
  /** Page title */
  title?: string;
  /** Smaller subtitle below the title */
  subtitle?: string;
  /** Kept for compatibility — every PageShell page now shows "Back". */
  backLabel?: string;
  /** Where Back goes when there is no earlier in-app page (default /dashboard). */
  backHref?: string;
  /** Short page name for the top bar (defaults to `title`). */
  barTitle?: string;
  /** Layout width variant */
  width?: Width;
  /** Extra className on the <main> container */
  mainClassName?: string;
  /** Page-level actions, shown beside the title (e.g. filters, badges) */
  topBarRight?: React.ReactNode;
  /** Extra content on the left of the top bar */
  topBarLeft?: React.ReactNode;
  /** Desktop only: the page fills exactly one screen and never scrolls as a
   *  whole — the page's own panels scroll internally instead. */
  fixedDesktop?: boolean;
  children: React.ReactNode;
}

export default function PageShell({
  title,
  subtitle,
  backHref,
  barTitle,
  width = "standard",
  mainClassName = "",
  topBarRight,
  topBarLeft,
  fixedDesktop = false,
  children,
}: PageShellProps) {
  return (
    <MobileShell>
      <div
        className={`pad-nav ${fixedDesktop ? "lg:flex lg:h-[var(--app-height)] lg:flex-col lg:overflow-hidden" : ""}`}
        style={{ background: "var(--bg)" }}
      >
        <TopBar title={barTitle ?? title} fallbackHref={backHref} leading={topBarLeft} />

        <main
          className={`mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8 ${
            fixedDesktop ? "lg:flex lg:min-h-0 lg:flex-1 lg:flex-col lg:pb-0" : ""
          } ${mainClassName}`}
        >
          {/* ── Page heading ─────────────────────────────────────────── */}
          <PageHeading title={title} subtitle={subtitle} actions={topBarRight} />

          {/* ── Page content ─────────────────────────────────────────── */}
          <div className={`${CONTENT_W[width]} ${fixedDesktop ? "lg:min-h-0 lg:flex-1" : ""}`}>{children}</div>
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}
