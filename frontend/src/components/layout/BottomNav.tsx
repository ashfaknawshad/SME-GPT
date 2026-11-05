"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { useTabSwipe } from "@/lib/useTabSwipe";
import { logoutUser } from "@/lib/auth";
import { hasUnreadNotifications } from "@/lib/notifications";

type NavItem = { label: string; icon: string; href: string; badge?: boolean };

/**
 * App navigation.
 *
 *  • Below 1024px: the fixed bottom tab bar (unchanged phone/PWA behaviour).
 *  • From 1024px: a fixed left sidebar with the brand, the main tabs, the
 *    report shortcuts and the signed-in user. globals.css pushes the page
 *    right by --side-nav-w whenever this nav is on the page.
 *  • On the Assistant (/query), which has its own thread list, the sidebar
 *    collapses to a slim icon rail so the chat keeps its width.
 */
export default function BottomNav() {
  const pathname = usePathname();
  const navRef = useRef<HTMLElement | null>(null);
  const [lang, setLang] = useState<AppLanguage>("en");
  const [isAdmin, setIsAdmin] = useState(false);
  const [user, setUser] = useState<{ fullName?: string; email?: string; companyName?: string } | null>(null);
  const [hasUnread, setHasUnread] = useState(false);

  // Publish the nav's real rendered height as --bottom-nav-measured so no page
  // has to hardcode "the nav is 64px tall". It genuinely varies: the admin role
  // adds a fifth column, Sinhala labels wrap differently, and the home-indicator
  // inset is added on top. Pages reserve space with var(--bottom-nav-h), which
  // is this measurement — and collapses to 0 while the keyboard is open.
  // On desktop the bar is display:none, so this reads 0 there.
  useEffect(() => {
    const el = navRef.current;
    if (!el) return;
    const root = document.documentElement;
    const publish = () => {
      root.style.setProperty("--bottom-nav-measured", `${Math.round(el.offsetHeight)}px`);
    };
    publish();
    if (typeof ResizeObserver === "undefined") return; // older engines keep the initial read
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.removeProperty("--bottom-nav-measured");
    };
  }, [lang, isAdmin]);

  useEffect(() => {
    setLang(getStoredLanguage());
    fetch("/api/auth/me", { cache: "no-store" })
      .then((res) => res.json())
      .then((data) => {
        setIsAdmin(data?.user?.role === "admin");
        if (data?.user) setUser(data.user);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const update = () => setHasUnread(hasUnreadNotifications());
    update();
    window.addEventListener("notifications-updated", update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener("notifications-updated", update);
      window.removeEventListener("storage", update);
    };
  }, []);

  const t = ui[lang];
  const si = lang === "si";

  const items: NavItem[] = [
    { label: t.overview,                         icon: "dashboard",            href: "/dashboard" },
    { label: t.files,                            icon: "folder",               href: "/repository" },
    { label: t.query,                            icon: "forum",                href: "/query" },
    { label: si ? "සැකසීම්" : "Settings",       icon: "settings",             href: "/settings" },
    ...(isAdmin ? [{ label: "Admin",             icon: "admin_panel_settings", href: "/admin" }] : []),
  ];

  // Desktop-only extras: the shortcuts the dashboard used to hold as chips.
  const workspace: NavItem[] = [
    { label: si ? "ලේඛනය උඩුගත කරන්න" : "Upload Document", icon: "upload_file", href: "/upload" },
    { label: si ? "අතින් ලේඛනය" : "Manual Entry",          icon: "edit_note",   href: "/manual-entry" },
    { label: si ? "දැනුම්දීම්" : "Notifications",           icon: "notifications", href: "/notifications", badge: hasUnread },
  ];
  const reports: NavItem[] = [
    { label: si ? "ලාභ/පාඩු" : "P&L Report",                  icon: "trending_up", href: "/reports/pnl" },
    { label: si ? "ගෙවිය යුතු ශේෂ" : "Outstanding Payments", icon: "payments",    href: "/reports/payables" },
    { label: si ? "සැපයුම්කරුවන්" : "Suppliers",            icon: "contacts",    href: "/suppliers" },
  ];

  // Horizontal swipe moves between these same tabs, in the same order. The nav
  // owns the tab order, so it owns tab navigation — there is no second list to
  // keep in sync when a tab is added or the admin column appears.
  useTabSwipe(items.map((item) => item.href));

  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");
  const rail = pathname === "/query" || pathname.startsWith("/query/");

  const initials = (user?.fullName || user?.email || "U")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");

  const sideLink = (item: NavItem) => {
    const active = isActive(item.href);
    return (
      <Link
        key={item.href}
        href={item.href}
        title={rail ? item.label : undefined}
        aria-label={item.label}
        aria-current={active ? "page" : undefined}
        className={`group relative flex items-center rounded-xl text-[13px] font-semibold transition-colors ${
          rail ? "h-11 w-11 justify-center" : "gap-3 px-3 py-2.5"
        }`}
        style={{
          color: active ? "var(--nav-active-fg)" : "var(--nav-fg-muted)",
          background: active ? "var(--nav-active-bg)" : undefined,
        }}
        onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "var(--nav-hover)"; }}
        onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = ""; }}
      >
        {active && !rail && (
          <span
            className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full"
            style={{ background: "var(--nav-accent)" }}
            aria-hidden
          />
        )}
        <span
          className="material-symbols-outlined text-[20px]"
          style={{
            fontVariationSettings: active
              ? '"FILL" 1, "wght" 500, "GRAD" 0, "opsz" 24'
              : '"FILL" 0, "wght" 400, "GRAD" 0, "opsz" 24',
          }}
        >
          {item.icon}
        </span>
        {!rail && <span className="truncate">{item.label}</span>}
        {item.badge && (
          <span
            className={`h-2 w-2 rounded-full bg-red-500 ${rail ? "absolute right-2 top-2" : "ml-auto"}`}
            aria-hidden
          />
        )}
      </Link>
    );
  };

  const section = (title: string, list: NavItem[]) => (
    <div className={rail ? "flex flex-col items-center gap-1" : "space-y-0.5"}>
      {rail ? (
        <div className="my-2 h-px w-8" style={{ background: "var(--nav-border)" }} aria-hidden />
      ) : (
        <p className="px-3 pb-1.5 pt-5 text-[10px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--nav-section)" }}>
          {title}
        </p>
      )}
      {list.map(sideLink)}
    </div>
  );

  return (
    <>
      {/* ── Desktop sidebar (≥1024px) ───────────────────────────────── */}
      <aside
        className={`app-nav app-chrome fixed inset-y-0 left-0 z-40 hidden flex-col lg:flex ${
          rail ? "app-nav--rail w-[76px] items-center" : "w-[256px]"
        }`}
        style={{
          background: "var(--nav-bg)",
          borderRight: "1px solid var(--nav-border)",
          paddingTop: "var(--safe-top)",
          paddingLeft: "var(--safe-left)",
        }}
        aria-label="Main navigation"
      >
        {/* Brand */}
        <Link
          href="/dashboard"
          className={`flex shrink-0 items-center gap-3 ${rail ? "justify-center px-0 py-5" : "px-5 py-5"}`}
        >
          <Image
            src="/logo.png"
            alt=""
            width={40}
            height={40}
            priority
            className="h-10 w-10 shrink-0 rounded-xl bg-white object-contain p-0.5 shadow-sm"
          />
          {!rail && (
            <div className="min-w-0">
              <p className="text-[17px] font-extrabold leading-tight tracking-tight" style={{ color: "var(--nav-fg)" }}>
                SME-GPT
              </p>
              <p className="truncate text-[11px] font-medium" style={{ color: "var(--nav-fg-muted)" }}>
                {user?.companyName || (si ? "ලේඛන බුද්ධිය" : "Document Intelligence")}
              </p>
            </div>
          )}
        </Link>

        <nav className={`min-h-0 flex-1 overflow-y-auto pb-4 ${rail ? "flex flex-col items-center gap-1 px-2" : "px-3"}`}>
          <div className={rail ? "flex flex-col items-center gap-1" : "space-y-0.5"}>
            {!rail && (
              <p className="px-3 pb-1.5 pt-1 text-[10px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--nav-section)" }}>
                {si ? "ප්‍රධාන" : "Main"}
              </p>
            )}
            {items.map(sideLink)}
          </div>
          {section(si ? "වැඩබිම" : "Workspace", workspace)}
          {section(si ? "වාර්තා" : "Reports", reports)}
        </nav>

        {/* Signed-in user */}
        <div
          className={`shrink-0 ${rail ? "flex flex-col items-center gap-2 py-4" : "m-3 rounded-2xl p-3"}`}
          style={rail ? { borderTop: "1px solid var(--nav-border)" } : { background: "var(--nav-hover)", border: "1px solid var(--nav-border)" }}
        >
          <div className={`flex items-center ${rail ? "flex-col gap-2" : "gap-3"}`}>
            <Link
              href="/profile"
              title={user?.fullName || "Profile"}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[13px] font-bold text-white shadow-sm"
              style={{ background: "#c97b5a" }}
            >
              {initials || <span className="material-symbols-outlined text-[18px]">person</span>}
            </Link>
            {!rail && (
              <Link href="/profile" className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-bold" style={{ color: "var(--nav-fg)" }}>
                  {user?.fullName || "—"}
                </p>
                <p className="truncate text-[11px]" style={{ color: "var(--nav-fg-muted)" }}>
                  {user?.email || ""}
                </p>
              </Link>
            )}
            <button
              onClick={() => { void logoutUser(); }}
              title={si ? "පිටවන්න" : "Logout"}
              aria-label={si ? "පිටවන්න" : "Logout"}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition hover:opacity-80"
              style={{ color: "var(--nav-fg-muted)", background: "var(--nav-hover)" }}
            >
              <span className="material-symbols-outlined text-[18px]">logout</span>
            </button>
          </div>
        </div>
      </aside>

      {/* ── Mobile / tablet bottom bar (<1024px) ───────────────────── */}
      <nav
        ref={navRef}
        className="app-chrome fixed bottom-0 left-0 right-0 z-50 backdrop-blur transition-transform duration-200 lg:hidden"
        style={{
          background: "var(--surface)",
          borderTop: "1px solid var(--border)",
          // The bar itself absorbs the home indicator, so its tappable row always
          // sits above it and its background still reaches the screen edge.
          paddingBottom: "var(--safe-bottom)",
          paddingLeft: "var(--safe-left)",
          paddingRight: "var(--safe-right)",
          // Slide out of the way while the keyboard is up — a native app does the
          // same, and it hands those ~64px back to the conversation.
          transform: "translateY(var(--bottom-nav-shift, 0))",
        }}
      >
        <div className="mx-auto w-full max-w-[1180px]">
          <div className={`grid px-2 py-1.5 ${isAdmin ? "grid-cols-5" : "grid-cols-4"}`} key={`nav-${lang}`}>
            {items.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className="relative flex min-h-[48px] flex-col items-center justify-center gap-0.5 rounded-xl px-2 py-2 text-center text-[10px] font-semibold transition sm:text-[11px]"
                  style={{
                    color: active ? "var(--brand-mid)" : "var(--text-3)",
                  }}
                >
                  <span
                    className="flex h-7 w-12 items-center justify-center rounded-full transition-colors"
                    style={{ background: active ? "var(--brand-tint)" : "transparent" }}
                  >
                    <span
                      className="material-symbols-outlined text-[22px]"
                      style={{
                        fontVariationSettings: active
                          ? '"FILL" 1, "wght" 500, "GRAD" 0, "opsz" 24'
                          : '"FILL" 0, "wght" 400, "GRAD" 0, "opsz" 24',
                      }}
                    >
                      {item.icon}
                    </span>
                  </span>
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      </nav>
    </>
  );
}
