"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import ThemeToggle from "@/components/layout/ThemeToggle";
import { getStoredToken } from "@/lib/auth";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { confirmDialog, noticeDialog } from "@/lib/confirm";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type QueryHistoryItem = {
  id: string;
  company_name: string;
  question: string;
  answer: string;
  explanation: string;
  metrics: Record<string, unknown>;
  evidence: unknown[];
  source_file: string;
  created_at: string;
};

export default function QueryHistoryPage() {
  const router = useRouter();
  const [lang, setLang] = useState<AppLanguage>("en");
  const [history, setHistory] = useState<QueryHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [clearing, setClearing] = useState(false);

  useEffect(() => { setLang(getStoredLanguage()); }, []);
  const t = ui[lang];

  function formatDateTime(value: string) {
    if (!value) return t.noDate;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  }

  const loadHistory = useCallback(async () => {
    const token = getStoredToken();

    if (!token) {
      router.push("/login");
      return;
    }

    try {
      setLoading(true);
      setError("");

      const res = await fetch(`${BACKEND_URL}/query-history`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
        cache: "no-store",
      });

      const data = await res.json();

      if (!res.ok || !data.success) {
        throw new Error(data.message || ui[getStoredLanguage()].qhLoadFailed);
      }

      setHistory(data.history || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : ui[getStoredLanguage()].qhLoadFailed);
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const openHistoryItem = (item: QueryHistoryItem) => {
    const payload = {
      success: true,
      company_name: item.company_name,
      question: item.question,
      answer: item.answer,
      explanation: item.explanation,
      evidence: item.evidence || [],
      metrics: item.metrics || {},
      source_file: item.source_file || "",
      opened_from_history: true,
    };

    sessionStorage.setItem("query_result", JSON.stringify(payload));
    sessionStorage.setItem("selected_query_history", JSON.stringify(item));

    router.push("/answer");
  };

  const handleDeleteOne = async (id: string) => {
    const confirmed = await confirmDialog({
      title: t.qhConfirmDeleteOne,
      confirmLabel: t.delete,
      variant: "danger",
    });
    if (!confirmed) return;

    try {
      setDeletingId(id);

      const token = getStoredToken();

      const res = await fetch(`${BACKEND_URL}/query-history/${id}`, {
        method: "DELETE",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const data = await res.json();

      if (!res.ok || !data.success) {
        throw new Error(data.message || t.qhLoadFailed);
      }

      setHistory((prev) => prev.filter((item) => item.id !== id));
    } catch (err) {
      await noticeDialog({ title: t.qhLoadFailed, message: err instanceof Error ? err.message : undefined });
    } finally {
      setDeletingId("");
    }
  };

  const handleClearAll = async () => {
    const confirmed = await confirmDialog({
      title: t.qhConfirmClear,
      confirmLabel: t.clearAll,
      variant: "danger",
    });
    if (!confirmed) return;

    try {
      setClearing(true);

      const token = getStoredToken();

      const res = await fetch(`${BACKEND_URL}/query-history`, {
        method: "DELETE",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const data = await res.json();

      if (!res.ok || !data.success) {
        throw new Error(data.message || t.qhLoadFailed);
      }

      setHistory([]);
    } catch (err) {
      await noticeDialog({ title: t.qhLoadFailed, message: err instanceof Error ? err.message : undefined });
    } finally {
      setClearing(false);
    }
  };

  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <main className="mx-auto w-full max-w-[980px] px-4 py-6 sm:px-6 lg:px-8">
          <div className="mb-4 flex items-center justify-between gap-3">
            <button
              onClick={() => router.push("/profile")}
              className="text-[14px] font-medium text-[var(--brand-mid)]"
            >
              ← {t.back}
            </button>

            <div className="flex items-center gap-2">
              <ThemeToggle />
              <LanguageSwitcher />
              <button
                onClick={handleClearAll}
                disabled={clearing || history.length === 0}
                className="rounded-xl border border-red-200 bg-red-50 px-4 py-2 text-[13px] font-bold text-red-600 disabled:opacity-50"
              >
                {clearing ? t.clearing : t.clearAll}
              </button>
            </div>
          </div>

          <h1 className="text-[24px] font-extrabold text-[var(--text-1)]">
            {t.qhTitle}
          </h1>

          <p className="mt-2 text-[14px] text-[var(--text-2)]">
            {t.qhSubtitle}
          </p>

          {loading ? (
            <div
              className="mt-6 rounded-[18px] p-5 text-[14px] text-[var(--text-2)] shadow-sm"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
            >
              {t.qhLoading}
            </div>
          ) : error ? (
            <div className="mt-6 rounded-[18px] border border-red-200 bg-red-50 p-5 text-[14px] text-red-700 shadow-sm">
              {error}
            </div>
          ) : history.length === 0 ? (
            <div
              className="mt-6 rounded-[18px] p-5 text-[14px] text-[var(--text-2)] shadow-sm"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
            >
              {t.qhNone}
            </div>
          ) : (
            <div className="mt-6 space-y-4">
              {history.map((item) => (
                <div
                  key={item.id}
                  className="rounded-[18px] p-5 shadow-sm"
                  style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
                >
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-3)]">
                        {item.company_name}
                      </p>

                      <h2 className="mt-2 text-[16px] font-bold text-[var(--text-1)]">
                        {item.question}
                      </h2>

                      <p className="mt-2 line-clamp-2 text-[13px] text-[var(--text-2)]">
                        {item.answer}
                      </p>

                      <p className="mt-3 text-[12px] text-[var(--text-3)]">
                        {formatDateTime(item.created_at)}
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => openHistoryItem(item)}
                        className="rounded-xl px-4 py-2 text-[13px] font-bold text-white"
                        style={{ background: "var(--brand-mid)" }}
                      >
                        {t.open}
                      </button>

                      <button
                        onClick={() => handleDeleteOne(item.id)}
                        disabled={deletingId === item.id}
                        className="rounded-xl border border-red-200 bg-red-50 px-4 py-2 text-[13px] font-bold text-red-600 disabled:opacity-50"
                      >
                        {deletingId === item.id ? t.deleting : t.delete}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}
