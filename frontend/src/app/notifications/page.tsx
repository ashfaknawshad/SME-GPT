"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import { getStoredLanguage, ui, AppLanguage } from "@/lib/i18n";
import {
  getNotifications,
  clearNotifications,
  markAllNotificationsRead,
  AppNotification,
} from "@/lib/notifications";

function formatTime(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

/** Maps a notification type onto the semantic tokens in globals.css, so the
 *  cards follow the light/dark theme instead of being pinned to Tailwind's
 *  light-only palette. */
function getTypeStyles(type: AppNotification["type"]) {
  switch (type) {
    case "success":
      return { token: "success", icon: "check_circle" };
    case "warning":
      return { token: "warn", icon: "warning" };
    case "error":
      return { token: "danger", icon: "error" };
    default:
      return { token: "info", icon: "notifications" };
  }
}

export default function NotificationsPage() {
  const router = useRouter();
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [lang, setLang] = useState<AppLanguage>("en");
  const t = ui[lang];

  useEffect(() => { setLang(getStoredLanguage()); }, []);

  useEffect(() => {
    const items = getNotifications();
    setNotifications(items);

    if (items.some((item) => item.read === false)) {
      markAllNotificationsRead();
    }
  }, []);

  const handleClearAll = () => {
    clearNotifications();
    setNotifications([]);
  };

  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <main className="mx-auto w-full max-w-[980px] px-4 py-6 sm:px-6 lg:px-8">
          <div className="mb-5 flex items-center justify-between">
            <button
              onClick={() => router.back()}
              className="text-[14px] font-medium text-[var(--brand-mid)]"
            >
              ← {t.back}
            </button>

            <div className="flex items-center gap-2">
              <LanguageSwitcher />

              <button
                onClick={handleClearAll}
                className="rounded-xl px-4 py-2 text-[12px] font-semibold text-[var(--text-2)]"
                style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
              >
                {t.clearAll}
              </button>
            </div>
          </div>

          <h1 className="text-[24px] font-extrabold text-[var(--text-1)]">
            {lang === "si" ? "දැනුම්දීම්" : "Notifications"}
          </h1>

          <p className="mt-2 text-[14px] text-[var(--text-2)]">
            {lang === "si" ? "මෑත පද්ධති යාවත්කාලීන සහ ක්‍රියාකාරකම් ඇඟවීම්." : "Recent system updates and activity alerts."}
          </p>

          <p
            className="mt-2 rounded-xl px-3 py-2 text-[12px]"
            style={{ background: "var(--warn-tint)", border: "1px solid var(--warn-border)", color: "var(--warn)" }}
          >
            {lang === "si"
              ? "දැනුම්දීම් මෙම උපාංගයේ පමණක් ගබඩා වේ. බ්‍රවුසර් දත්ත ඉවත් කළහොත් ඒවා ද ඉවත් වේ."
              : "Notifications are stored on this device only and will be cleared if you clear browser data."}
          </p>

          <div className="mt-6 space-y-4">
            {notifications.length === 0 ? (
              <div
                className="rounded-[18px] p-5 text-[14px] text-[var(--text-2)] shadow-sm"
                style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
              >
                {lang === "si" ? "දැනුම්දීම් නොමැත." : "No notifications available."}
              </div>
            ) : (
              notifications.map((item) => {
                const styles = getTypeStyles(item.type);

                return (
                  <div
                    key={item.id}
                    className="rounded-[18px] p-5 shadow-sm"
                    style={{
                      background: `var(--${styles.token}-tint)`,
                      border: `1px solid var(--${styles.token}-border)`,
                      color: `var(--${styles.token})`,
                    }}
                  >
                    <div className="flex items-start gap-4">
                      <div
                        className="flex h-11 w-11 items-center justify-center rounded-xl"
                        style={{ background: "var(--surface)" }}
                      >
                        <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
                          {styles.icon}
                        </span>
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2">
                            <h2 className="text-[15px] font-bold">
                              {item.title}
                            </h2>

                            {!item.read && (
                              <span
                                className="rounded-full px-2 py-0.5 text-[10px] font-bold"
                                style={{ background: "var(--danger)", color: "var(--surface)" }}
                              >
                                NEW
                              </span>
                            )}
                          </div>

                          <span className="text-[11px] opacity-70">
                            {formatTime(item.createdAt)}
                          </span>
                        </div>

                        <p className="mt-2 text-[13px] leading-6">
                          {item.message}
                        </p>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}