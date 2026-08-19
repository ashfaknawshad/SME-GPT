"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

function getAuthToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

export default function QueryPage() {
  const router = useRouter();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const [lang, setLang] = useState<AppLanguage>("en");
  const [companyName, setCompanyName] = useState("");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setLang(getStoredLanguage());
    const savedCompany = localStorage.getItem("query_company_name") || "";
    setCompanyName(savedCompany);
  }, []);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.style.height = "auto";
    const nextHeight = Math.min(Math.max(textarea.scrollHeight, 120), 320);
    textarea.style.height = `${nextHeight}px`;
  }, [question]);

  const t = ui[lang];

  const handleAsk = async () => {
    setError("");

    if (!companyName.trim()) {
      setError("Please enter your company name first.");
      return;
    }

    if (!question.trim()) {
      setError("Please enter a question.");
      return;
    }

    const token = getAuthToken();
    if (!token) {
      setError("Login token missing. Please log in again.");
      router.push("/login");
      return;
    }

    localStorage.setItem("query_company_name", companyName.trim());
    setLoading(true);

    try {
      const res = await fetch(`${BACKEND_URL}/ask-query`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          company_name: companyName.trim(),
          question: question.trim(),
        }),
      });

      if (res.status === 401) {
        localStorage.removeItem("token");
        sessionStorage.removeItem("token");
        router.push("/login");
        return;
      }

      const data = await res.json();

      if (!res.ok || !data.success) {
        throw new Error(data.message || data.explanation || "Failed to answer query.");
      }

      sessionStorage.setItem("query_result", JSON.stringify(data));
      sessionStorage.removeItem("selected_query_history");
      router.push("/answer");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong while asking the question.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <main className="mx-auto w-full max-w-[980px] px-4 py-6 sm:px-6 lg:px-8">
          <div className="mb-4 flex items-center justify-between">
            <button
              onClick={() => router.push("/")}
              className="text-[14px] font-medium text-[var(--brand-mid)]"
            >
              ← Back
            </button>
            <div className="flex items-center gap-2">
              <LanguageSwitcher />
            </div>
          </div>

          <h1 className="text-[24px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[28px]">
            {t.askQuestion}
          </h1>

          <p className="mt-4 max-w-4xl text-[14px] leading-8 text-[var(--text-2)]">
            Ask questions using only data from your saved financial documents.
          </p>

          <div
            className="mt-6 rounded-[20px] p-5 shadow-sm"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
          >
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
              Company Context
            </p>
            <input
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="Enter your company name (example: AIESEC)"
              className="field-input mt-3 w-full rounded-[14px] border px-4 py-3 text-[15px] outline-none"
            />
            <p className="mt-2 text-[12px] text-[var(--text-3)]">
              This company name will be used as the main context before answering your question.
            </p>
          </div>

          <div
            className="mt-6 rounded-[20px] shadow-sm"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
          >
            <textarea
              ref={textareaRef}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Example: What is the receivable amount we have?"
              rows={1}
              className="min-h-[120px] w-full resize-none overflow-y-auto rounded-t-[20px] border-0 bg-transparent px-5 py-5 text-[18px] text-[var(--text-1)] outline-none placeholder:text-[var(--text-3)]"
            />
            <div
              className="flex items-center justify-between rounded-b-[20px] px-5 py-3 text-[var(--text-3)]"
              style={{ borderTop: "1px solid var(--border)" }}
            >
              <div className="text-[12px]">Source: your saved documents only</div>
              <span className="text-[12px]">Explainable answer enabled</span>
            </div>
          </div>

          {error && (
            <div
              className="mt-4 rounded-[16px] px-4 py-3 text-[14px]"
              style={{ background: "var(--danger-tint)", border: "1px solid var(--danger-border)", color: "var(--danger)" }}
            >
              {error}
            </div>
          )}

          <div className="mt-6">
            <button
              onClick={handleAsk}
              disabled={loading}
              className="w-full rounded-[18px] py-4 text-[15px] font-bold text-white shadow-[0_10px_24px_var(--brand-ring)] disabled:opacity-60"
              style={{ background: "var(--brand-mid)" }}
            >
              {loading ? "Analyzing..." : "Ask Question"}
            </button>
          </div>
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}