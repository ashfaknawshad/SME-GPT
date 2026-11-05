"use client";
/**
 * TopBar — the one header every signed-in page uses.
 *
 *   [← Back]  (desktop extras)                      [actions] theme · language · notifications
 *
 *  • Back always reads "Back" and returns to the previous page *in this app*;
 *    with no in-app history (fresh tab, PWA cold start) it goes to
 *    `fallbackHref` instead of leaving the app.
 *  • The dashboard (home) passes showBack={false}: phones then show the logo,
 *    plus profile and logout (desktop has those in the sidebar).
 *  • `inShell` is for the full-height chat shell, where the bar itself must
 *    clear the status-bar inset (normal pages get it from .app-page).
 */

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ThemeToggle from "./ThemeToggle";
import LanguageSwitcher from "./LanguageSwitcher";
import { logoutUser } from "@/lib/auth";
import { hasUnreadNotifications } from "@/lib/notifications";
import { getStoredLanguage } from "@/lib/i18n";
import { canGoBack } from "@/lib/navHistory";

export default function TopBar({
  title,
  showBack = true,
  fallbackHref = "/dashboard",
  onBack,
  start,
  leading,
  actions,
  companyName,
  inShell = false,
}: {
  /** Page name — not printed (the page heading shows it); used as the bar's accessible label. */
  title?: string;
  showBack?: boolean;
  /** Where Back goes when there is no earlier in-app page. */
  fallbackHref?: string;
  /** Fully override Back (rare). */
  onBack?: () => void;
  /** Always-visible content before Back (e.g. the chat's thread-list button). */
  start?: React.ReactNode;
  /** Desktop-only content on the left (e.g. the dashboard greeting). */
  leading?: React.ReactNode;
  /** Page actions, placed before the standard controls. */
  actions?: React.ReactNode;
  companyName?: string;
  inShell?: boolean;
}) {
  const router = useRouter();
  const [hasUnread, setHasUnread] = useState(false);
  const [si, setSi] = useState(false);

  useEffect(() => {
    setSi(getStoredLanguage() === "si");
    const update = () => setHasUnread(hasUnreadNotifications());
    update();
    window.addEventListener("notifications-updated", update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener("notifications-updated", update);
      window.removeEventListener("storage", update);
    };
  }, []);

  const goBack = () => {
    if (onBack) return onBack();
    if (canGoBack()) router.back();
    else router.push(fallbackHref);
  };

  const iconBtn =
    "relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition hover:bg-[var(--surface-2)]";

  return (
    <header
      aria-label={title}
      className={`app-chrome z-30 shrink-0 ${inShell ? "" : "sticky top-0"}`}
      style={{
        background: "color-mix(in srgb, var(--surface) 88%, transparent)",
        backdropFilter: "saturate(1.4) blur(12px)",
        WebkitBackdropFilter: "saturate(1.4) blur(12px)",
        borderBottom: "1px solid var(--border)",
        paddingTop: inShell ? "var(--safe-top)" : undefined,
      }}
    >
      <div className={`flex w-full items-center gap-2 py-3 sm:gap-3 ${inShell ? "px-3 sm:px-5" : "mx-auto max-w-[1600px] px-4 sm:px-6 lg:px-8"}`}>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 sm:gap-2">
          {start}

          {showBack ? (
            <button
                onClick={goBack}
                aria-label={si ? "ආපසු" : "Back"}
                className="flex h-9 shrink-0 items-center gap-1.5 rounded-xl px-2 text-[13px] font-semibold transition hover:bg-[var(--brand-tint)] sm:px-2.5"
                style={{ color: "var(--brand-mid)" }}
              >
                <span className="material-symbols-outlined text-[20px]">arrow_back</span>
                <span className="hidden sm:inline">{si ? "ආපසු" : "Back"}</span>
            </button>
          ) : (
            /* Home: brand on phones (the sidebar carries it on desktop). */
            <div className="flex min-w-0 items-center gap-2.5 lg:hidden">
              <Image
                src="/logo.png"
                alt=""
                width={36}
                height={36}
                priority
                className="h-9 w-9 shrink-0 rounded-xl object-contain shadow-sm"
              />
              <div className="hidden min-w-0 min-[400px]:block">
                <p className="text-[16px] font-extrabold leading-tight tracking-tight text-[var(--text-1)]">SME-GPT</p>
                {companyName && <p className="truncate text-[11px] text-[var(--text-2)]">{companyName}</p>}
              </div>
            </div>
          )}
          {leading && <div className="hidden min-w-0 lg:block">{leading}</div>}
        </div>

        <div className="flex shrink-0 items-center gap-1 sm:gap-1.5">
          {actions}
          <ThemeToggle />
          <LanguageSwitcher />
          <button
            onClick={() => router.push("/notifications")}
            aria-label={si ? "දැනුම්දීම්" : "Notifications"}
            className={iconBtn}
          >
            <span className="material-symbols-outlined text-[20px] text-[var(--text-2)]">notifications</span>
            {hasUnread && (
              <span
                className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-red-500 ring-2"
                style={{ ["--tw-ring-color" as string]: "var(--surface)" }}
              />
            )}
          </button>
          {/* Home only, phones only: desktop has these in the sidebar, and
              other pages reach them through the Settings tab. */}
          {!showBack && (
            <>
              <button
                onClick={() => router.push("/profile")}
                aria-label="Profile"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white transition hover:opacity-90 lg:hidden"
                style={{ background: "#c97b5a" }}
              >
                <span className="material-symbols-outlined text-[20px]">person</span>
              </button>
              <button
                onClick={() => { void logoutUser(); }}
                aria-label={si ? "පිටවන්න" : "Logout"}
                className={`${iconBtn} lg:hidden`}
              >
                <span className="material-symbols-outlined text-[19px] text-[var(--text-2)]">logout</span>
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
