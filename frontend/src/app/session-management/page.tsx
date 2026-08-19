"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import ThemeToggle from "@/components/layout/ThemeToggle";
import { getSession } from "@/lib/auth";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { confirmDialog } from "@/lib/confirm";

type TrustedDevice = {
  id: string;
  deviceToken: string;
  deviceName: string | null;
  ipAddress: string | null;
  userAgent: string | null;
  trustedAt: string;
  lastUsedAt: string;
};

function formatDate(value: string) {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export default function SessionManagementPage() {
  const router = useRouter();

  const [lang, setLang] = useState<AppLanguage>("en");
  const [devices, setDevices] = useState<TrustedDevice[]>([]);
  const [currentDeviceToken, setCurrentDeviceToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => { setLang(getStoredLanguage()); }, []);
  const t = ui[lang];

  const loadSessions = useCallback(async () => {
    try {
      setLoading(true);
      setMessage("");

      const session = await getSession();
      if (!session) {
        router.push("/login");
        return;
      }

      const res = await fetch("/api/sessions");
      const data = await res.json();

      if (!res.ok) {
        setMessage(data.error || ui[getStoredLanguage()].smLoadFailed);
        return;
      }

      setDevices(data.devices || []);
      setCurrentDeviceToken(data.currentDeviceToken || null);
    } catch (error) {
      console.error("LOAD SESSIONS ERROR:", error);
      setMessage(ui[getStoredLanguage()].smLoadError);
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const handleRemoveDevice = async (deviceId: string) => {
    try {
      setActionLoading(true);
      setMessage("");

      const res = await fetch("/api/sessions", {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ deviceId }),
      });

      const data = await res.json();

      if (!res.ok) {
        setMessage(data.error || t.smRemoveFailed);
        return;
      }

      setMessage(t.smRemovedOne);
      await loadSessions();
    } catch (error) {
      console.error("REMOVE DEVICE ERROR:", error);
      setMessage(t.genericError);
    } finally {
      setActionLoading(false);
    }
  };

  const handleRemoveOthers = async () => {
    try {
      setActionLoading(true);
      setMessage("");

      const res = await fetch("/api/sessions", {
        method: "POST",
      });

      const data = await res.json();

      if (!res.ok) {
        setMessage(data.error || t.smRemoveOthersFailed);
        return;
      }

      setMessage(data.message || t.smRemovedOthers);
      await loadSessions();
    } catch (error) {
      console.error("REMOVE OTHERS ERROR:", error);
      setMessage(t.genericError);
    } finally {
      setActionLoading(false);
    }
  };

  const handleTrustCurrentDevice = async () => {
    try {
      setActionLoading(true);
      setMessage("");

      const res = await fetch("/api/sessions/trust-current", {
        method: "POST",
      });

      const data = await res.json();

      if (!res.ok) {
        setMessage(data.error || t.smTrustFailed);
        return;
      }

      setMessage(data.message || t.smTrusted);
      await loadSessions();
    } catch (error) {
      console.error("TRUST CURRENT DEVICE ERROR:", error);
      setMessage(t.genericError);
    } finally {
      setActionLoading(false);
    }
  };

  const currentTrustedDevice = devices.find(
    (device) => device.deviceToken === currentDeviceToken
  );

  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <main className="mx-auto w-full max-w-[980px] px-4 py-6 sm:px-6 lg:px-8">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h1 className="text-[24px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[28px]">
                {t.smTitle}
              </h1>
              <p className="mt-1 text-[13px] text-[var(--text-2)]">
                {t.smSubtitle}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <ThemeToggle />
              <LanguageSwitcher />
            </div>
          </div>

          <div className="mb-5 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => router.push("/profile")}
              className="rounded-xl px-4 py-2 text-[13px] font-bold text-[var(--text-2)]"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
            >
              {t.backToProfile}
            </button>

            <button
              type="button"
              onClick={async () => {
                if (!(await confirmDialog({ title: t.smConfirmOthers, confirmLabel: t.remove, variant: "danger" }))) return;
                handleRemoveOthers();
              }}
              disabled={actionLoading}
              className="rounded-xl px-4 py-2 text-[13px] font-bold text-white"
              style={{ background: "var(--brand)" }}
            >
              {actionLoading ? t.working : t.smLogoutOthers}
            </button>
          </div>

          {message && (
            <p className="mb-4 text-[13px] text-[var(--brand-mid)]">{message}</p>
          )}

          <div
            className="rounded-[20px] p-5 shadow-sm"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
          >
            <h2 className="text-[16px] font-bold text-[var(--text-1)]">
              {t.smCurrentStatus}
            </h2>

            <div
              className="mt-4 rounded-[16px] p-4"
              style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}
            >
              <p className="text-[15px] font-bold text-[var(--text-1)]">{t.smThisDevice}</p>

              {currentTrustedDevice ? (
                <>
                  <p className="mt-1 text-[13px] font-semibold text-[#16a34a]">
                    {t.smTrustedDevice}
                  </p>
                  <p className="mt-2 text-[12px] text-[var(--text-2)]">
                    {t.smLastUsed}: {formatDate(currentTrustedDevice.lastUsedAt)}
                  </p>
                  <p className="mt-1 text-[12px] text-[var(--text-2)] break-words">
                    {currentTrustedDevice.deviceName || t.smUnknownDevice}
                  </p>
                </>
              ) : (
                <>
                  <p className="mt-1 text-[13px] font-semibold text-red-500">
                    {t.smNotTrusted}
                  </p>

                  <button
                    onClick={handleTrustCurrentDevice}
                    disabled={actionLoading}
                    className="mt-3 rounded-xl px-4 py-2 text-[12px] font-bold text-white"
                    style={{ background: "var(--brand-mid)" }}
                  >
                    {actionLoading ? t.working : t.smTrustThis}
                  </button>
                </>
              )}
            </div>
          </div>

          <div
            className="mt-6 rounded-[20px] shadow-sm"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
          >
            <div className="px-5 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
              <h2 className="text-[16px] font-bold text-[var(--text-1)]">
                {t.smTrustedDevices}
              </h2>
              <p className="mt-1 text-[13px] text-[var(--text-2)]">
                {t.smRemovingNote}
              </p>
            </div>

            {loading ? (
              <div className="px-5 py-6 text-[14px] text-[var(--text-2)]">
                {t.smLoadingDevices}
              </div>
            ) : devices.length === 0 ? (
              <div className="px-5 py-6 text-[14px] text-[var(--text-2)]">
                {t.smNoDevices}
              </div>
            ) : (
              <div>
                {devices.map((device) => {
                  const isCurrent = device.deviceToken === currentDeviceToken;

                  return (
                    <div
                      key={device.id}
                      className="flex flex-col gap-4 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
                      style={{ borderTop: "1px solid var(--border)" }}
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-[15px] font-bold text-[var(--text-1)]">
                            {device.deviceName || t.smUnknownDevice}
                          </p>

                          {isCurrent && (
                            <span
                              className="rounded-full px-2.5 py-1 text-[10px] font-bold text-[var(--brand-mid)]"
                              style={{ background: "var(--brand-tint)" }}
                            >
                              {t.smThisDevice}
                            </span>
                          )}
                        </div>

                        <p className="mt-1 break-all text-[13px] text-[var(--text-2)]">
                          {device.ipAddress || t.smUnknownIP}
                        </p>

                        <p className="mt-1 break-words text-[12px] text-[var(--text-3)]">
                          {device.userAgent || t.smUnknownBrowser}
                        </p>

                        <div className="mt-2 text-[12px] text-[var(--text-2)]">
                          <p>{t.smTrustedAt}: {formatDate(device.trustedAt)}</p>
                          <p>{t.smLastUsed}: {formatDate(device.lastUsedAt)}</p>
                        </div>
                      </div>

                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={actionLoading}
                          onClick={async () => {
                            if (!(await confirmDialog({ title: t.smConfirmRemove, confirmLabel: t.remove, variant: "danger" }))) return;
                            handleRemoveDevice(device.id);
                          }}
                          className="rounded-xl border border-red-300 px-4 py-2 text-[12px] font-bold text-red-600"
                          style={{ background: "rgba(220,38,38,0.06)" }}
                        >
                          {t.remove}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div
            className="mt-6 rounded-[20px] p-5"
            style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.25)" }}
          >
            <h3 className="text-[15px] font-bold text-[var(--text-1)]">
              {t.smImportantNote}
            </h3>
            <p className="mt-2 text-[13px] leading-6 text-[var(--text-2)]">
              {t.smImportantBody}
            </p>
          </div>
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}
