"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import DerivationTrace from "@/components/ui/DerivationTrace";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { isHiddenMetric, metricLabel, metricValue, humanizeFlow } from "@/lib/humanize";
import { formatMoney, otherPartyName } from "@/lib/format";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type EvidenceItem = {
  document_id: string;
  document_type: string;
  date: string;
  company_name: string;
  supplier_name: string;
  order_id: string;
  flow_type: string;
  flow_direction?: "income" | "expense";
  received_status: string;
  paid_status: string;
  // Iteration 10: workflow status fields
  po_status?: string | null;
  dn_status?: string | null;
  invoice_status?: string | null;
  due_date?: string | null;
  delivery_date?: string | null;
  approved_by?: string | null;
  proof_of_delivery?: boolean | null;
  signed?: boolean | null;
  currency?: string;
  final_total_amount: number;
  payable_amount: number;
  amount_used?: number;
  reason_used: string;
  items?: {
  description: string;
  quantity: number | string | null;
  unit_price: number | string | null;
  line_total: number | string | null;
}[];
};

type DiscrepancyItem = {
  description: string;
  invoice_price: number;
  po_price: number;
  diff_pct: number;
  is_discrepancy: boolean;
  direction: "higher" | "lower";
};

type QueryResult = {
  success: boolean;
  company_name: string;
  question: string;
  answer: string;
  explanation: string;
  evidence: EvidenceItem[];
  metrics: Record<string, unknown>;
  source_file: string;
  history_saved?: boolean;
  history_error?: string;
  opened_from_history?: boolean;
  discrepancies?: DiscrepancyItem[];
};

function getAuthToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

function formatValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "NULL";
  return String(value);
}

export default function AnswerPage() {
  const router = useRouter();
  const [lang, setLang] = useState<AppLanguage>("en");
  const [result, setResult] = useState<QueryResult | null>(null);
  // Conversation thread: every Q&A turn, so the initial query stays visible.
  const [thread, setThread] = useState<QueryResult[]>([]);
  const [followUpQuestion, setFollowUpQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [followUpError, setFollowUpError] = useState("");
  const [showExplanation, setShowExplanation] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  const [flagged, setFlagged] = useState(false);
  const [notifyOpen, setNotifyOpen] = useState(false);
  const t = ui[lang];

  useEffect(() => {
    setLang(getStoredLanguage());

    const raw = sessionStorage.getItem("query_result");
    if (!raw) {
      setResult(null);
      return;
    }

    try {
      const parsed = JSON.parse(raw);
      setResult(parsed);
      setThread([parsed]);
    } catch {
      setResult(null);
      setThread([]);
    }
  }, []);

  const filteredMetrics = useMemo(() => {
    const metrics = result?.metrics || {};
    // Drop internal/technical metrics (operation, question_type, engine, …) so
    // the SME owner only sees plain-language figures.
    return Object.entries(metrics).filter(([key]) => !isHiddenMetric(key));
  }, [result]);

  const handleFollowUp = async () => {
    try {
      setFollowUpError("");
      setAsking(true);

      if (!followUpQuestion.trim()) {
        setFollowUpError("Please enter another question.");
        return;
      }

      const token = getAuthToken();
      if (!token) {
        setFollowUpError("Login token missing. Please log in again.");
        router.push("/login");
        return;
      }

      const res = await fetch(`${BACKEND_URL}/ask-query`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          company_name: result?.company_name || "",
          question: followUpQuestion.trim(),
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
        throw new Error(data.message || "Failed to answer follow-up question.");
      }

      sessionStorage.setItem("query_result", JSON.stringify(data));
      sessionStorage.removeItem("selected_query_history");

      setResult(data);
      setThread((prev) => [...prev, data]);
      setFollowUpQuestion("");
      setShowExplanation(false);
      setShowEvidence(false);
    } catch (err) {
      setFollowUpError(
        err instanceof Error ? err.message : "Failed to ask follow-up question."
      );
    } finally {
      setAsking(false);
    }
  };

  const handleStartNewChat = () => {
    sessionStorage.removeItem("query_result");
    sessionStorage.removeItem("selected_query_history");
    router.push("/query");
  };

  const handleBack = () => {
    if (result?.opened_from_history) {
      router.push("/profile/query-history");
      return;
    }
    router.push("/query");
  };

  if (!result) {
    return (
      <MobileShell>
        <div className="pad-nav bg-[var(--bg)]">
          <main className="mx-auto w-full max-w-[980px] px-4 py-6 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3">
              <button
                onClick={() => router.push("/query")}
                className="text-[14px] font-medium text-[var(--brand-mid)]"
              >
                ← {t.back}
              </button>
              <button
                onClick={handleStartNewChat}
                className="rounded-xl bg-[var(--brand)] px-4 py-2 text-[13px] font-bold text-white"
              >
                {t.startNewChat}
              </button>
            </div>

            <div className="mt-8 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-6 text-center text-[14px] text-[var(--text-2)] shadow-sm">
              {t.noQueryResult}
            </div>
          </main>
          <BottomNav />
        </div>
      </MobileShell>
    );
  }

  return (
    <MobileShell>
      <div className="pad-nav bg-[var(--bg)]">
        <main className="mx-auto w-full max-w-[980px] px-4 py-6 sm:px-6 lg:px-8">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <button
              onClick={handleBack}
              className="text-[14px] font-medium text-[var(--brand-mid)]"
            >
              ← {t.back}
            </button>

            <div className="flex items-center gap-2">
              <LanguageSwitcher />
              <button
                onClick={handleStartNewChat}
                className="rounded-xl bg-[var(--brand)] px-4 py-2 text-[13px] font-bold text-white"
              >
                {t.startNewChat}
              </button>
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
                {t.insightsAnalysis}
              </p>
              <h1 className="text-[24px] font-extrabold text-[var(--text-1)]">
                {t.businessInsight}
              </h1>
            </div>
            <span
              className="rounded-xl px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider"
              style={{ background: "rgba(34,82,181,0.08)", color: "#2252b5" }}
            >
              {t.accuracy}
            </span>
          </div>

          {result.history_saved === false && (
            <div className="mt-4 rounded-[16px] border border-[var(--warn-border)] bg-[var(--warn-tint)] px-4 py-3 text-[14px] text-[var(--warn)]">
              Query answered, but history was not saved to DB.
              {result.history_error ? ` (${result.history_error})` : ""}
            </div>
          )}

          <div className="mt-6 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
              {t.companyContextLabel}
            </p>
            <p className="mt-3 text-[14px] font-semibold text-[var(--text-1)]">
              {result.company_name}
            </p>
          </div>

          {/* Conversation thread — earlier turns so the initial query stays visible */}
          {thread.length > 1 && (
            <div className="mt-6">
              <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
                {t.conversationLabel}
              </p>
              <div className="space-y-3">
                {thread.slice(0, -1).map((turn, i) => (
                  <div key={i} className="rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-4 shadow-sm">
                    <p className="flex items-start gap-1.5 text-[13px] font-semibold text-[var(--text-1)]">
                      <span className="material-symbols-outlined text-[16px] text-[var(--brand-mid)]">person</span>
                      <span>{turn.question}</span>
                    </p>
                    <p className="mt-1.5 whitespace-pre-line pl-[22px] text-[13px] leading-6 text-[var(--text-2)]">
                      {turn.answer}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-6 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
              {t.questionLabel}
            </p>
            <p className="mt-2 text-[14px] text-[var(--text-1)]">{result.question}</p>
          </div>

          <div className="mt-6 rounded-[18px] border border-[var(--border)] bg-[var(--brand-tint)] p-5">
            <p className="text-[12px] font-bold uppercase tracking-[0.08em] text-[var(--brand-mid)]">
              {t.answerLabel}
            </p>
            <p className="mt-3 whitespace-pre-line text-[16px] font-semibold leading-7 text-[var(--text-1)]">
  {result.answer}
</p>
          </div>

          {/* Action buttons — UI Design 6 */}
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <button
              onClick={() => {
                const firstDoc = result.evidence?.[0];
                if (firstDoc) router.push(`/analysis/${firstDoc.document_id}`);
              }}
              className="flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[12px] font-bold transition hover:opacity-80"
              style={{ background: "rgba(34,82,181,0.08)", color: "#2252b5", border: "1px solid rgba(34,82,181,0.18)" }}
            >
              <span className="material-symbols-outlined text-[15px]">edit_note</span>
              {t.adjustTotal}
            </button>
            <button
              onClick={() => {
                const supplier = result.evidence?.[0]?.supplier_name || result.company_name || "";
                const subject = encodeURIComponent(`Document Query: ${result.question}`);
                const body = encodeURIComponent(
                  `Dear ${supplier || "Supplier"},\n\n` +
                  `We have the following query regarding our documents:\n\n` +
                  `Question: ${result.question}\n\nAnswer: ${result.answer}\n\n` +
                  `Please respond at your earliest convenience.\n\nRegards`
                );
                window.open(`mailto:?subject=${subject}&body=${body}`);
              }}
              className="flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[12px] font-bold transition hover:opacity-80"
              style={{ background: "rgba(34,82,181,0.08)", color: "#2252b5", border: "1px solid rgba(34,82,181,0.18)" }}
            >
              <span className="material-symbols-outlined text-[15px]">mail</span>
              {t.notifySupplier}
            </button>
            <button
              onClick={() => {
                const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `query-result-${Date.now()}.json`;
                a.click();
                URL.revokeObjectURL(url);
              }}
              className="flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[12px] font-bold transition hover:opacity-80"
              style={{ background: "rgba(22,163,74,0.08)", color: "#16a34a", border: "1px solid rgba(22,163,74,0.18)" }}
            >
              <span className="material-symbols-outlined text-[15px]">download</span>
              {t.export}
            </button>
            <button
              onClick={() => {
                const next = !flagged;
                setFlagged(next);
                try {
                  const stored = JSON.parse(localStorage.getItem("flagged_queries") || "[]") as object[];
                  if (next) {
                    stored.push({ question: result.question, answer: result.answer, company: result.company_name, date: new Date().toISOString() });
                  } else {
                    const idx = stored.findIndex((f: unknown) => (f as {question:string}).question === result.question);
                    if (idx !== -1) stored.splice(idx, 1);
                  }
                  localStorage.setItem("flagged_queries", JSON.stringify(stored));
                } catch {}
              }}
              className="flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[12px] font-bold transition hover:opacity-80"
              style={
                flagged
                  ? { background: "rgba(220,38,38,0.1)", color: "#dc2626", border: "1px solid rgba(220,38,38,0.2)" }
                  : { background: "#0f172a", color: "#fff" }
              }
            >
              <span className="material-symbols-outlined text-[15px]">flag</span>
              {flagged ? t.flaggedDone : t.flagForReview}
            </button>
          </div>


          {/* GAP-19B: Price discrepancy card (Iter 19 — SRS UI-D6 use case) */}
          {result.discrepancies && result.discrepancies.length > 0 && (
            <div className="mt-4 rounded-[18px] border border-[var(--warn-border)] bg-[var(--warn-tint)] p-5">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-[18px] text-[var(--warn)]">warning</span>
                <p className="text-[13px] font-bold text-[var(--warn)] uppercase tracking-wide">
                  {t.priceDiscrepancyFound}
                </p>
              </div>
              <div className="mt-3 space-y-3">
                {result.discrepancies.filter(d => d.is_discrepancy).map((d, i) => (
                  <div key={i} className="rounded-[12px] border border-[var(--warn-border)] bg-[var(--surface)] px-4 py-3">
                    <p className="text-[13px] font-semibold text-[var(--text-1)]">{d.description}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-4 text-[12px]">
                      <span className="text-[var(--text-2)]">
                        Invoice: <span className="font-bold text-red-600">{d.invoice_price.toLocaleString()}</span>
                      </span>
                      <span className="text-[var(--text-2)]">
                        PO: <span className="font-bold text-[var(--success)]">{d.po_price.toLocaleString()}</span>
                      </span>
                      <span
                        className="rounded-lg px-2 py-0.5 text-[11px] font-bold"
                        style={{
                          background: d.direction === "higher" ? "rgba(220,38,38,0.1)" : "rgba(22,163,74,0.1)",
                          color: d.direction === "higher" ? "#dc2626" : "#16a34a",
                        }}
                      >
                        {d.diff_pct > 0 ? "+" : ""}{d.diff_pct}% {d.direction.toUpperCase()}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-6 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <button
              onClick={() => setShowExplanation((prev) => !prev)}
              className="flex w-full items-center justify-between px-5 py-4 text-left"
            >
              <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
                {t.explanationLabel}
              </span>
              <span className="material-symbols-outlined text-[var(--text-2)]">
                {showExplanation ? "expand_less" : "expand_more"}
              </span>
            </button>

            {showExplanation && (
  <div className="border-t border-[var(--border)] px-5 py-4 text-[14px] leading-7 text-[var(--text-2)]">
    <p>{result.explanation}</p>

    <div className="mt-4 rounded-[12px] bg-[var(--surface-2)] px-4 py-3">
      <p className="text-[12px] font-semibold text-[var(--text-2)]">
        Source used: <span className="font-bold">{result.source_file}</span>
      </p>
    </div>

    <div className="mt-4">
      <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
        {t.metricsLabel}
      </p>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {filteredMetrics.length === 0 ? (
          <div className="text-[14px] text-[var(--text-2)]">No metrics available.</div>
        ) : (
          filteredMetrics.map(([key, value]) => (
            <div
              key={key}
              className="rounded-[14px] border border-[var(--border)] px-4 py-3"
            >
              <p className="text-[11px] text-[var(--text-3)]">{metricLabel(key, lang)}</p>
              <p className="mt-1 text-[15px] font-semibold text-[var(--text-1)]">
                {metricValue(key, value, lang)}
              </p>
            </div>
          ))
        )}
      </div>
    </div>
  </div>
)}
          </div>

          <div className="mt-6 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <button
              onClick={() => setShowEvidence((prev) => !prev)}
              className="flex w-full items-center justify-between px-5 py-4 text-left"
            >
              <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
                {t.evidenceLabel}
              </span>
              <span className="material-symbols-outlined text-[var(--text-2)]">
                {showEvidence ? "expand_less" : "expand_more"}
              </span>
            </button>

            {showEvidence && (
              <div className="border-t border-[var(--border)] px-5 py-4">
                <div className="space-y-4">
                  {result.evidence && result.evidence.length > 0 ? (
                    result.evidence.map((item, index) => (
                      <div
                        key={`${item.document_id}-${index}`}
                        className="rounded-[14px] border border-[var(--border)] p-4"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-[15px] font-bold text-[var(--text-1)]">
                              {otherPartyName(item) || item.document_id}
                            </p>
                            <p className="mt-0.5 text-[11px] font-semibold text-[var(--text-3)]">
                              {item.document_id}
                            </p>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="rounded-xl bg-[var(--brand-tint)] px-3 py-1.5 text-[11px] font-semibold text-[var(--brand-mid)]">
                              {item.document_type?.toUpperCase()}
                            </span>
                            {(item.flow_direction === "income" || (!item.flow_direction && ["receivable","cash_inflow"].includes(item.flow_type))) && (
                              <span className="rounded-xl bg-green-100 px-3 py-1.5 text-[11px] font-semibold text-green-700">
                                INCOME
                              </span>
                            )}
                            {(item.flow_direction === "expense" || (!item.flow_direction && ["payable","cash_outflow"].includes(item.flow_type))) && (
                              <span className="rounded-xl bg-red-100 px-3 py-1.5 text-[11px] font-semibold text-red-600">
                                EXPENSE
                              </span>
                            )}
                            {item.document_type === "po" && (
                              <button
                                onClick={() => router.push(`/analysis/${item.document_id}`)}
                                className="rounded-xl border border-[var(--brand-mid)] px-3 py-1 text-[11px] font-bold text-[var(--brand-mid)] hover:bg-[var(--brand-tint)] transition"
                              >
                                GO TO PO →
                              </button>
                            )}
                          </div>
                        </div>

                        <div className="mt-3 grid gap-2 sm:grid-cols-2">
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Date:</span> {item.date}</p>
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Order ID:</span> {item.order_id}</p>
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Company:</span> {item.company_name}</p>
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Supplier:</span> {item.supplier_name}</p>
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">{t.flowTypeLabel}:</span> {humanizeFlow(item.flow_type, lang)}</p>
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Currency:</span> {formatValue(item.currency)}</p>
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Final Total:</span> {formatMoney(item.final_total_amount, item.currency) || "—"}</p>
                          <p className="text-[13px] text-[var(--text-2)]">
                            <span className="font-semibold">
                              {(item.flow_direction === "income" || ["receivable","cash_inflow"].includes(item.flow_type))
                                ? "Receivable Amount:" : "Payable Amount:"}
                            </span>{" "}
                            {formatMoney(item.payable_amount, item.currency) || "—"}
                          </p>
                          <p className="text-[13px] text-[var(--text-2)]">
                            <span className="font-semibold">Amount Used:</span>{" "}
                            {formatMoney(item.amount_used ?? item.final_total_amount, item.currency) || "—"}
                          </p>
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Received Status:</span> {item.received_status}</p>
                          <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Paid Status:</span> {item.paid_status}</p>
                          {item.due_date && <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Due Date:</span> {item.due_date}</p>}
                          {item.delivery_date && <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Delivery Date:</span> {item.delivery_date}</p>}
                          {item.approved_by && <p className="text-[13px] text-[var(--text-2)]"><span className="font-semibold">Approved By:</span> {item.approved_by}</p>}
                        </div>
                        {/* Iteration 10: workflow status badges */}
                        {(item.po_status || item.dn_status || item.invoice_status) && (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {item.po_status && (
                              <span className={`rounded-full px-3 py-1 text-[11px] font-bold ${
                                item.po_status === "approved" ? "bg-green-100 text-green-700" :
                                item.po_status === "rejected" ? "bg-red-100 text-red-700" :
                                item.po_status === "cancelled" ? "bg-gray-200 text-gray-600" :
                                item.po_status === "fulfilled" ? "bg-blue-100 text-blue-700" :
                                item.po_status === "partially_delivered" ? "bg-yellow-100 text-yellow-700" :
                                "bg-orange-100 text-orange-700"
                              }`}>
                                PO: {item.po_status.replace(/_/g, " ").toUpperCase()}
                              </span>
                            )}
                            {item.dn_status && (
                              <span className={`rounded-full px-3 py-1 text-[11px] font-bold ${
                                item.dn_status === "delivered" ? "bg-green-100 text-green-700" :
                                item.dn_status === "delayed" ? "bg-red-100 text-red-700" :
                                item.dn_status === "failed" ? "bg-red-200 text-red-800" :
                                item.dn_status === "returned" ? "bg-purple-100 text-purple-700" :
                                item.dn_status === "partially_delivered" ? "bg-yellow-100 text-yellow-700" :
                                "bg-orange-100 text-orange-700"
                              }`}>
                                DN: {item.dn_status.replace(/_/g, " ").toUpperCase()}
                              </span>
                            )}
                            {item.invoice_status && (
                              <span className={`rounded-full px-3 py-1 text-[11px] font-bold ${
                                item.invoice_status === "paid" ? "bg-green-100 text-green-700" :
                                item.invoice_status === "overdue" ? "bg-red-100 text-red-700" :
                                item.invoice_status === "cancelled" ? "bg-gray-200 text-gray-600" :
                                item.invoice_status === "partially_paid" ? "bg-yellow-100 text-yellow-700" :
                                "bg-orange-100 text-orange-700"
                              }`}>
                                INV: {item.invoice_status.replace(/_/g, " ").toUpperCase()}
                              </span>
                            )}
                            {item.proof_of_delivery === true && (
                              <span className="rounded-full bg-teal-100 px-3 py-1 text-[11px] font-bold text-teal-700">✓ PROOF OF DELIVERY</span>
                            )}
                            {item.signed === true && (
                              <span className="rounded-full bg-teal-100 px-3 py-1 text-[11px] font-bold text-teal-700">✓ SIGNED</span>
                            )}
                            {item.signed === false && (
                              <span className="rounded-full bg-red-100 px-3 py-1 text-[11px] font-bold text-red-600">UNSIGNED</span>
                            )}
                          </div>
                        )}

                        <div className="mt-3 rounded-[12px] bg-[var(--surface-2)] px-3 py-3 text-[12px] text-[var(--text-2)]">
                          <span className="font-semibold">Reason used:</span> {item.reason_used}
                        </div>
                        {item.items && item.items.length > 0 && (
  <div className="mt-3 rounded-[12px] border border-[var(--border)] bg-[var(--surface)] px-3 py-3">
    <p className="text-[12px] font-bold uppercase tracking-[0.08em] text-[var(--text-2)]">
      Items
    </p>

    <div className="mt-2 space-y-2">
      {item.items.map((it, idx) => (
        <div
          key={idx}
          className="rounded-[10px] bg-[var(--surface-2)] px-3 py-2 text-[12px] text-[var(--text-2)]"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="font-semibold">{formatValue(it.description)}</p>
            <span className="shrink-0 rounded-md bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] font-bold text-[var(--text-2)]">
              ROW {idx + 1}
            </span>
          </div>
          <p className="mt-0.5">
            Qty: {formatValue(it.quantity)} · Unit Price: {formatValue(it.unit_price)} · Line Total: {formatValue(it.line_total)}
          </p>
        </div>
      ))}
    </div>
  </div>
)}
                      </div>
                    ))
                  ) : (
                    <div className="text-[14px] text-[var(--text-2)]">No evidence documents available.</div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Derivation Trace — Iteration 7 */}
          <div className="mt-6 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <button
              onClick={() => setShowTrace((prev) => !prev)}
              className="flex w-full items-center justify-between px-5 py-4 text-left"
            >
              <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
                {t.derivationTrace ?? "Derivation Trace"}
              </span>
              <span className="material-symbols-outlined text-[var(--text-2)]">
                {showTrace ? "expand_less" : "expand_more"}
              </span>
            </button>
            {showTrace && (
              <div className="border-t border-[var(--border)] px-5 py-4">
                <DerivationTrace
                  evidence={result.evidence || []}
                  metrics={result.metrics || {}}
                  questionType={
                    (result.metrics?.question_type as string) || "summary"
                  }
                  companyName={result.company_name}
                  lang={lang}
                />
              </div>
            )}
          </div>

          <div className="mt-6 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
              {t.askFollowUpTitle}
            </p>

            <textarea
              value={followUpQuestion}
              onChange={(e) => setFollowUpQuestion(e.target.value)}
              placeholder={t.answerFollowUpPlaceholder}
              rows={4}
              className="mt-4 w-full rounded-[14px] border border-[var(--border)] px-4 py-3 text-[15px] text-[var(--text-1)] outline-none focus:border-[var(--brand-mid)]"
            />

            {followUpError && (
              <div className="mt-4 rounded-[14px] border border-[var(--danger-border)] bg-[var(--danger-tint)] px-4 py-3 text-[13px] text-[var(--danger)]">
                {followUpError}
              </div>
            )}

            <div className="mt-4 flex flex-wrap gap-3">
              <button
                onClick={handleFollowUp}
                disabled={asking}
                className="rounded-xl bg-[var(--brand)] px-4 py-2 text-[13px] font-bold text-white disabled:opacity-60"
              >
                {asking ? t.analyzing : t.askFollowUpBtn}
              </button>

              <button
                onClick={handleStartNewChat}
                className="rounded-xl border border-[var(--border)] bg-[var(--surface)] px-4 py-2 text-[13px] font-bold text-[var(--text-2)]"
              >
                {t.startNewChat}
              </button>
            </div>
          </div>
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}