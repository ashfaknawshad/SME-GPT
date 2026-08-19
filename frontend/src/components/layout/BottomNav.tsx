"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";

export default function BottomNav() {
  const pathname = usePathname();
  const navRef = useRef<HTMLElement | null>(null);
  const [lang, setLang] = useState<AppLanguage>("en");
  const [isAdmin, setIsAdmin] = useState(false);

  // Publish the nav's real rendered height as --bottom-nav-measured so no page
  // has to hardcode "the nav is 64px tall". It genuinely varies: the admin role
  // adds a fifth column, Sinhala labels wrap differently, and the home-indicator
  // inset is added on top. Pages reserve space with var(--bottom-nav-h), which
  // is this measurement — and collapses to 0 while the keyboard is open.
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
      .then((data) => setIsAdmin(data?.user?.role === "admin"))
      .catch(() => {});
  }, []);

  const t = ui[lang];

  const items = [
    { label: t.overview,                                icon: "dashboard",          href: "/dashboard" },
    { label: t.files,                                   icon: "folder",             href: "/repository" },
    { label: t.query,                                   icon: "forum",              href: "/query" },
    { label: lang === "si" ? "සැකසීම්" : "Settings",  icon: "settings",           href: "/settings" },
    ...(isAdmin ? [{ label: "Admin",                    icon: "admin_panel_settings", href: "/admin" }] : []),
  ];

  return (
    <nav
      ref={navRef}
      className="app-chrome fixed bottom-0 left-0 right-0 z-50 backdrop-blur transition-transform duration-200"
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
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                className="flex min-h-[48px] flex-col items-center justify-center gap-0.5 rounded-xl px-2 py-2 text-center text-[10px] font-semibold transition sm:text-[11px]"
                style={{
                  color: active ? "var(--brand-mid)" : "var(--text-3)",
                }}
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
                {item.label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
