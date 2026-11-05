"use client";

import { useEffect, useMemo, useState } from "react";
import PageShell from "@/components/layout/PageShell";
import { getStoredLanguage, ui, AppLanguage } from "@/lib/i18n";
import {
  getNotifications,
  clearNotifications,
  markAllNotificationsRead,
  AppNotification,
} from "@/lib/notifications";

type Filter = "all" | "alert" | "success" | "info";

function formatTime(value: string, lang: AppLanguage) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString(lang === "si" ? "si-LK" : "en-GB", { hour: "2-digit", minute: "2-digit" });
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

const filterOf = (type: AppNotification["type"]): Filter =>
  type === "warning" || type === "error" ? "alert" : type === "success" ? "success" : "info";

/** "Today" / "Yesterday" / a full date, for grouping the timeline. */
function dayLabel(value: string, si: boolean) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return si ? "වෙනත්" : "Earlier";
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(new Date()) - start(d)) / 86_400_000);
  if (diff === 0) return si ? "අද" : "Today";
  if (diff === 1) return si ? "ඊයේ" : "Yesterday";
  return d.toLocaleDateString(si ? "si-LK" : "en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

export default function NotificationsPage() {
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [lang, setLang] = useState<AppLanguage>("en");
  const [filter, setFilter] = useState<Filter>("all");
  const t = ui[lang];
  const si = lang === "si";

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

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: notifications.length, alert: 0, success: 0, info: 0 };
    notifications.forEach((n) => { c[filterOf(n.type)] += 1; });
    return c;
  }, [notifications]);

  const groups = useMemo(() => {
    const list = filter === "all" ? notifications : notifications.filter((n) => filterOf(n.type) === filter);
    const out: { label: string; items: AppNotification[] }[] = [];
    list.forEach((n) => {
      const label = dayLabel(n.createdAt, si);
      const last = out[out.length - 1];
      if (last && last.label === label) last.items.push(n);
      else out.push({ label, items: [n] });
    });
    return out;
  }, [notifications, filter, si]);

  const FILTERS: { key: Filter; icon: string; label: string; token: string }[] = [
    { key: "all",     icon: "inbox",        label: si ? "සියල්ල" : "All",            token: "info" },
    { key: "alert",   icon: "warning",      label: si ? "ඇඟවීම්" : "Alerts",          token: "warn" },
    { key: "success", icon: "check_circle", label: si ? "සාර්ථක" : "Completed",       token: "success" },
    { key: "info",    icon: "info",         label: si ? "තොරතුරු" : "Updates",        token: "info" },
  ];

  const clearButton = (
    <button
      onClick={handleClearAll}
      disabled={notifications.length === 0}
      className="flex items-center gap-1.5 rounded-xl px-4 py-2 text-[13px] font-semibold text-[var(--text-2)] transition hover:bg-[var(--surface-2)] disabled:opacity-40"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <span className="material-symbols-outlined text-[17px]">delete_sweep</span>
      {t.clearAll}
    </button>
  );

  return (
    <PageShell
      backLabel={t.back}
      title={si ? "දැනුම්දීම්" : "Notifications"}
      subtitle={si ? "මෑත පද්ධති යාවත්කාලීන සහ ක්‍රියාකාරකම් ඇඟවීම්." : "Recent system updates and activity alerts."}
      topBarRight={clearButton}
    >
      <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)] xl:grid-cols-[340px_minmax(0,1fr)]">
        {/* ── Summary / filters ─────────────────────────────────────── */}
        <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
          <div className="ui-card p-2">
            <div className="grid grid-cols-4 gap-1 lg:grid-cols-1">
              {FILTERS.map((f) => {
                const active = filter === f.key;
                return (
                  <button
                    key={f.key}
                    onClick={() => setFilter(f.key)}
                    className="flex flex-col items-center gap-1 rounded-xl px-2 py-2.5 text-[11px] font-semibold transition lg:flex-row lg:gap-3 lg:px-3 lg:text-[13px]"
                    style={{
                      background: active ? "var(--brand-tint)" : "transparent",
                      color: active ? "var(--brand-mid)" : "var(--text-2)",
                    }}
                  >
                    <span
                      className="flex h-8 w-8 items-center justify-center rounded-lg"
                      style={{ background: `var(--${f.token}-tint)`, color: `var(--${f.token})` }}
                    >
                      <span className="material-symbols-outlined text-[18px]">{f.icon}</span>
                    </span>
                    <span className="truncate lg:flex-1 lg:text-left">{f.label}</span>
                    <span
                      className="rounded-full px-2 py-0.5 text-[10px] font-bold lg:text-[11px]"
                      style={{ background: active ? "var(--surface)" : "var(--surface-2)", color: "var(--text-2)" }}
                    >
                      {counts[f.key]}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <p
            className="flex gap-2 rounded-2xl px-4 py-3 text-[12px] leading-5"
            style={{ background: "var(--warn-tint)", border: "1px solid var(--warn-border)", color: "var(--warn)" }}
          >
            <span className="material-symbols-outlined shrink-0 text-[17px]">devices</span>
            {si
              ? "දැනුම්දීම් මෙම උපාංගයේ පමණක් ගබඩා වේ. බ්‍රවුසර් දත්ත ඉවත් කළහොත් ඒවා ද ඉවත් වේ."
              : "Notifications are stored on this device only and will be cleared if you clear browser data."}
          </p>
        </aside>

        {/* ── Timeline ──────────────────────────────────────────────── */}
        <section className="min-w-0 space-y-6">
          {groups.length === 0 ? (
            <div className="ui-card flex flex-col items-center px-6 py-16 text-center">
              <span
                className="flex h-16 w-16 items-center justify-center rounded-2xl"
                style={{ background: "var(--brand-tint)", color: "var(--brand-mid)" }}
              >
                <span className="material-symbols-outlined text-[32px]">notifications_off</span>
              </span>
              <p className="mt-4 text-[16px] font-bold text-[var(--text-1)]">
                {si ? "දැනුම්දීම් නොමැත." : "You're all caught up"}
              </p>
              <p className="mt-1 text-[13px] text-[var(--text-3)]">
                {si ? "නව ක්‍රියාකාරකම් මෙහි පෙන්වනු ඇත." : "New activity and alerts will appear here."}
              </p>
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.label}>
                <p className="mb-2.5 px-1 text-[11px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)]">
                  {group.label}
                </p>
                <div className="ui-card divide-y divide-[var(--border)] overflow-hidden">
                  {group.items.map((item) => {
                    const styles = getTypeStyles(item.type);
                    return (
                      <div key={item.id} className="relative flex items-start gap-4 px-4 py-4 transition-colors hover:bg-[var(--surface-2)] sm:px-5">
                        <span
                          className="absolute inset-y-3 left-0 w-[3px] rounded-r-full"
                          style={{ background: `var(--${styles.token})` }}
                          aria-hidden
                        />
                        <span
                          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
                          style={{
                            background: `var(--${styles.token}-tint)`,
                            border: `1px solid var(--${styles.token}-border)`,
                            color: `var(--${styles.token})`,
                          }}
                        >
                          <span className="material-symbols-outlined text-[20px]" aria-hidden="true">{styles.icon}</span>
                        </span>

                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                            <div className="flex min-w-0 items-center gap-2">
                              <h2 className="truncate text-[14px] font-bold text-[var(--text-1)]">{item.title}</h2>
                              {!item.read && (
                                <span
                                  className="rounded-full px-2 py-0.5 text-[10px] font-bold"
                                  style={{ background: "var(--danger)", color: "var(--surface)" }}
                                >
                                  NEW
                                </span>
                              )}
                            </div>
                            <span className="text-[11px] font-medium text-[var(--text-3)]">
                              {formatTime(item.createdAt, lang)}
                            </span>
                          </div>
                          <p className="mt-1 break-words text-[13px] leading-6 text-[var(--text-2)]">{item.message}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </section>
      </div>
    </PageShell>
  );
}
