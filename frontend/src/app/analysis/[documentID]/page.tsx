"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import ProvenancePanel, { type ArithmeticJson } from "@/components/ui/ProvenancePanel";
import BboxOverlayViewer from "@/components/ui/BboxOverlayViewer";
import OrderTimeline from "@/components/ui/OrderTimeline";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { addNotification } from "@/lib/notifications";
import { confirmDialog, noticeDialog } from "@/lib/confirm";
import { humanizeFlow } from "@/lib/humanize";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type Item = {
  description: string;
  quantity: string | number;
  unit_price: string | number;
  line_total?: string | number;
};

type DocumentDetail = {
  document_id: string;
  document_type: string;
  company_name: string;
  supplier_name: string;
  date: string;
  raw_total_amount: string;
  final_total_amount: string;
  payable_amount: string;
  cash_return?: string;
  currency: string;
  status: string;
  language: string;
  order_id: string;
  flow_type: string;
  received_status: string;
  paid_status: string;
  items: Item[];
  image_url?: string | null;
  // Provenance fields (Iteration 7)
  arithmetic_status?: string;
  arithmetic_json?: ArithmeticJson | null;
  ocr_selected_version?: string;
  corrected_text?: string;
  // Spatial blobs (Iteration 9 + 10)
  spatial_chunks_json?: string | null;
  safe_boxes_json?: string | null;
  // Field→chunk map (Iteration 18 GAP-18C)
  field_chunk_map_json?: string | null;
  // Tax fields
  tax_amount?: string | number | null;
  tax_rate?: string | number | null;
  // Cash flow tracking
  cash_inflowed?: string | number | null;
  cash_outflowed?: string | number | null;
  category?: string | null;
  // IT-27 — PO approval workflow
  po_status?: string | null;
  approved_by?: string | null;
};

function getAuthToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

function InfoCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
      <p className="text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--text-2)]">
        {title}
      </p>
      <div className="mt-3 text-[14px] leading-7 text-[var(--text-2)]">{children}</div>
    </div>
  );
}

export default function AnalysisDetailPage() {
  const router = useRouter();
  const pathname = usePathname();

  const [lang, setLang] = useState<AppLanguage>("en");
  const [showProvenance, setShowProvenance] = useState(false);
  const [relatedDocs, setRelatedDocs] = useState<Array<{document_id:string;document_type:string;date:string;supplier_name:string;final_total_amount:string|number;currency:string;link_reason:string}>>([]);
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null);
  const t = ui[lang];
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [editedDocument, setEditedDocument] = useState<DocumentDetail | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  // Bumped after any save/edit so the Order Timeline refetches the reconciled
  // PO status (e.g. marking a DN delivered flips its linked PO to fulfilled).
  const [orderRefresh, setOrderRefresh] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [flowChangeMessage, setFlowChangeMessage] = useState("");
  // IT-27 — PO approval
  const [approverName, setApproverName] = useState("");
  const [poActionLoading, setPoActionLoading] = useState(false);

  const documentId = useMemo(() => {
    if (!pathname) return "";
    const parts = pathname.split("/").filter(Boolean);
    return parts.length >= 2 ? decodeURIComponent(parts[parts.length - 1]) : "";
  }, [pathname]);

  useEffect(() => {
    setLang(getStoredLanguage());
    // IT-27: remember who is approving, to stamp approved_by on PO actions.
    fetch("/api/auth/me")
      .then((r) => r.json())
      .then((d) => setApproverName(d?.user?.fullName || d?.user?.email || ""))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!documentId) {
      setLoading(false);
      setError("Document ID is missing.");
      return;
    }

    const fetchDocument = async () => {
      const token = getAuthToken();

      if (!token) {
        setError("Login token missing. Please log in again.");
        setLoading(false);
        router.push("/login");
        return;
      }

      try {
        setLoading(true);
        setError("");

        const res = await fetch(`${BACKEND_URL}/documents/${documentId}`, {
          method: "GET",
          cache: "no-store",
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        if (res.status === 401) {
          localStorage.removeItem("token");
          sessionStorage.removeItem("token");
          router.push("/login");
          return;
        }

        const data = await res.json();

        if (!res.ok || !data.success) {
          throw new Error(data.message || "Failed to load document.");
        }

        setDocument(data.document);
        setEditedDocument(data.document);

        // IT-22: fetch related documents (same order_id / same counterparty)
        const relRes = await fetch(`${BACKEND_URL}/documents/${documentId}/related`, {
          method: "GET", cache: "no-store", headers: { Authorization: `Bearer ${token}` },
        });
        if (relRes.ok) {
          const relData = await relRes.json();
          if (relData.success) setRelatedDocs(relData.related || []);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load document.");
      } finally {
        setLoading(false);
      }
    };

    fetchDocument();
  }, [documentId, router]);

  const formatParty = () => {
    const target = editedDocument || document;
    if (!target) return "NULL";
    if (target.company_name && target.company_name !== "NULL") return target.company_name;
    if (target.supplier_name && target.supplier_name !== "NULL") return target.supplier_name;
    return "NULL";
  };

  const updateField = <K extends keyof DocumentDetail>(
    key: K,
    value: DocumentDetail[K]
  ) => {
    setEditedDocument((prev) => {
      if (!prev) return prev;
      return { ...prev, [key]: value };
    });
  };

  const updateItemField = <K extends keyof Item>(
    index: number,
    key: K,
    value: Item[K]
  ) => {
    setEditedDocument((prev) => {
      if (!prev) return prev;
      const updatedItems = [...(prev.items || [])];
      updatedItems[index] = {
        ...updatedItems[index],
        [key]: value,
      };
      return {
        ...prev,
        items: updatedItems,
      };
    });
  };

  const handlePoAction = async (nextStatus: "approved" | "rejected") => {
    if (!document) return;
    try {
      setPoActionLoading(true);
      setError("");
      setSuccessMessage("");

      const token = getAuthToken();
      const res = await fetch(`${BACKEND_URL}/documents/${documentId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          po_status: nextStatus,
          approved_by: nextStatus === "approved" ? approverName : "",
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.message || t.poActionFailed);

      setDocument(data.document);
      setEditedDocument(data.document);
      setSuccessMessage(nextStatus === "approved" ? t.poApproveSuccess : t.poRejectSuccess);
      setOrderRefresh((k) => k + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.poActionFailed);
    } finally {
      setPoActionLoading(false);
    }
  };

  const handleSave = async () => {
    if (!editedDocument) return;

    try {
      setSaving(true);
      setError("");
      setSuccessMessage("");

      const token = getAuthToken();
      const res = await fetch(`${BACKEND_URL}/documents/${documentId}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          company_name: editedDocument.company_name,
          supplier_name: editedDocument.supplier_name,
          date: editedDocument.date,
          document_type: editedDocument.document_type,
          order_id: editedDocument.order_id,
          flow_type: editedDocument.flow_type,
          currency: editedDocument.currency,
          raw_total_amount: editedDocument.raw_total_amount,
          final_total_amount: editedDocument.final_total_amount,
          payable_amount: editedDocument.payable_amount,
          cash_return: editedDocument.cash_return,
          cash_inflowed: editedDocument.cash_inflowed,
          cash_outflowed: editedDocument.cash_outflowed,
          received_status: editedDocument.received_status,
          paid_status: editedDocument.paid_status,
          language: editedDocument.language,
          items: editedDocument.items,
          tax_amount: editedDocument.tax_amount,
          tax_rate: editedDocument.tax_rate,
        }),
      });

      const data = await res.json();

      if (!res.ok || !data.success) {
        throw new Error(data.message || "Failed to update document.");
      }

      setDocument(data.document);
      setEditedDocument(data.document);
      setEditMode(false);

      if (data.flow_change_message) {
        setFlowChangeMessage(data.flow_change_message);
      } else {
        setFlowChangeMessage("");
      }

      setSuccessMessage("Document updated successfully.");
      setOrderRefresh((k) => k + 1);
      addNotification({
        title: lang === "si" ? "ලේඛනය යාවත්කාලීන කෙරිණි" : "Document Updated",
        message: lang === "si"
          ? `${documentId} — ලේඛනයේ ක්ෂේත්‍ර සාර්ථකව සංස්කරණය කෙරිණි.`
          : `${documentId} — Document fields updated successfully.`,
        type: "success",
      });


    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update document.");
    } finally {
      setSaving(false);
    }
  };

  const target = editedDocument || document;
    const handleDelete = async () => {
    if (!documentId) return;

    const confirmed = await confirmDialog({
      title: lang === "si" ? "ලේඛනය මකන්නද?" : "Delete this document?",
      message: lang === "si"
        ? "ඔබට මෙම ලේඛනය මකා දැමීමට අවශ්‍යද? මෙය අවලංගු කළ නොහැක."
        : "Are you sure you want to delete this document? This can't be undone.",
      confirmLabel: lang === "si" ? "මකන්න" : "Delete",
      variant: "danger",
    });

    if (!confirmed) return;

    try {
      setDeleting(true);
      setError("");
      setSuccessMessage("");

      const token = getAuthToken();

      const res = await fetch(`${BACKEND_URL}/documents/${documentId}`, {
        method: "DELETE",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const data = await res.json();

      if (!res.ok || !data.success) {
        throw new Error(data.message || "Failed to delete document.");
      }

      addNotification({
        title: lang === "si" ? "ලේඛනය මකා දමන ලදී" : "Document Deleted",
        message: lang === "si"
          ? `${documentId} — ලේඛනය ගබඩාවෙන් ස්ථිරවශයෙන් ඉවත් කෙරිණි.`
          : `${documentId} — Document permanently removed from your repository.`,
        type: "info",
      });

      router.push("/repository");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete document.");
    } finally {
      setDeleting(false);
    }
  };

  
  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <main className="mx-auto w-full max-w-[1180px] px-4 py-6 sm:px-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <button
              onClick={() => router.back()}
              className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[var(--brand-mid)] transition hover:bg-[var(--brand-tint)]"
            >
              <span className="material-symbols-outlined">arrow_back</span>
            </button>

            {/* Action buttons wrap onto new rows on narrow screens instead of
                overflowing past the edge (which forced the PWA to zoom out). */}
            <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
  <LanguageSwitcher />

  {!loading && target ? (
    <>
      <button
        onClick={() => {
          if (editMode) {
            setEditedDocument(document);
            setEditMode(false);
            setFlowChangeMessage("");
          } else {
            setEditMode(true);
          }
        }}
        className="rounded-xl border border-[var(--border)] bg-[var(--surface)] px-4 py-2 text-[13px] font-semibold text-[var(--brand-mid)]"
      >
        {editMode ? "Cancel" : "Edit"}
      </button>

      <button
        onClick={handleSave}
        disabled={saving || !editMode}
        className="flex items-center gap-1.5 rounded-xl px-4 py-2 text-[13px] font-bold text-white disabled:opacity-40"
        style={{ background: "#2252b5" }}
      >
        <span className="material-symbols-outlined text-[14px]">verified</span>
        {saving ? "Saving…" : "Verify Data"}
      </button>

      {/* IT-45: Share button */}
      <button
        onClick={async () => {
          const token = localStorage.getItem("token") || sessionStorage.getItem("token") || "";
          const res  = await fetch(`${BACKEND_URL}/documents/${documentId}/share`, {
            method: "POST", headers: { Authorization: `Bearer ${token}` },
          });
          const data = await res.json();
          if (data.token) {
            const link = `${window.location.origin}/shared/${data.token}`;
            await navigator.clipboard.writeText(link).catch(() => {});
            await noticeDialog({
              title: lang === "si" ? "බෙදාගැනීමේ සබැඳිය පිටපත් කරන ලදී" : "Share link copied",
              message: lang === "si"
                ? `${link}\n\nදින 7කින් කල් ඉකුත් වේ.`
                : `${link}\n\nExpires in 7 days.`,
            });
          }
        }}
        className="flex items-center gap-1.5 rounded-xl border px-4 py-2 text-[13px] font-semibold transition hover:opacity-80"
        style={{ borderColor: "var(--border)", color: "var(--text-2)" }}
      >
        <span className="material-symbols-outlined text-[15px]">share</span>
        {lang === "si" ? "බෙදාගන්න" : "Share"}
      </button>

      {/* Stage C: open a document-scoped AI Assistant conversation */}
      <button
        onClick={() => router.push(`/query?doc=${encodeURIComponent(documentId)}`)}
        className="flex items-center gap-1.5 rounded-xl px-4 py-2 text-[13px] font-bold text-white transition hover:opacity-90"
        style={{ background: "var(--brand)" }}
      >
        <span className="material-symbols-outlined text-[15px]">forum</span>
        {t.aiAssistantAskAboutDoc ?? "Ask about this document"}
      </button>

      <button
        onClick={handleDelete}
        disabled={deleting}
        className="rounded-xl border border-[var(--danger-border)] bg-[var(--danger-tint)] px-4 py-2 text-[13px] font-semibold text-[var(--danger)] disabled:opacity-60"
      >
        {deleting ? "Deleting..." : "Delete"}
      </button>
    </>
  ) : null}
</div>
          </div>

          <div className="mb-5">
            <h1 className="text-[24px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[28px]">
              {loading ? "Loading..." : target?.document_id || "Document"}
            </h1>
            <p className="mt-1 text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
              Financial Document Analysis
            </p>
          </div>

          {/* Cross-document PO → DN → Invoice reconciliation (renders only when
              this document is part of a linked order). */}
          {!loading && target && (
            <OrderTimeline
              documentId={documentId}
              backendUrl={BACKEND_URL}
              lang={lang}
              refreshKey={orderRefresh}
            />
          )}

          {successMessage && (
            <div className="mb-4 rounded-[18px] border border-[var(--success-border)] bg-[var(--success-tint)] p-4 text-[14px] text-[var(--success)]">
              {successMessage}
            </div>
          )}
          {flowChangeMessage && (
  <div className="mb-4 rounded-[18px] border border-[var(--warn-border)] bg-[var(--warn-tint)] p-4 text-[14px] text-[var(--warn)]">
    {flowChangeMessage}
  </div>
)}
          {loading ? (
            <div className="rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-6 text-center text-[14px] text-[var(--text-2)]">
              Loading document...
            </div>
          ) : error ? (
            <div className="rounded-[18px] border border-[var(--danger-border)] bg-[var(--danger-tint)] p-6 text-center text-[14px] text-[var(--danger)]">
              {error}
            </div>
          ) : !target ? (
            <div className="rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-6 text-center text-[14px] text-[var(--text-2)]">
              Document not found.
            </div>
          ) : (
            <div className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
              <div className="rounded-[20px] bg-[var(--surface-2)] p-3 shadow-sm">
                {/* Language region tag */}
                <div className="mb-2 flex items-center gap-2">
                  <span
                    className="rounded-lg px-3 py-1 text-[10px] font-bold uppercase tracking-wider"
                    style={{ background: "rgba(34,82,181,0.1)", color: "#2252b5" }}
                  >
                    {target.language && target.language !== "NULL"
                      ? `${target.language.toUpperCase()} REGION`
                      : "ENGLISH REGION"}
                  </span>
                </div>
                <div className="rounded-[16px] bg-[var(--surface)] p-3">
                  {target.image_url ? (
                    <BboxOverlayViewer
                      imageUrl={/^https?:\/\//.test(target.image_url) ? target.image_url : `${BACKEND_URL}${target.image_url}`}
                      documentId={target.document_id}
                      spatialChunksJson={target.spatial_chunks_json}
                      activeChunkId={activeChunkId}
                      onChunkSelect={setActiveChunkId}
                    />
                  ) : (
                    <div className="flex min-h-[420px] items-center justify-center rounded-[16px] bg-[var(--surface-2)] sm:min-h-[520px]">
                      <div className="text-[13px] text-[var(--text-3)]">
                        No saved preview image for this document
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div>
                <div className="rounded-[20px] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--text-2)]">
                        {lang === "si" ? "උකහා ගත් දත්ත" : "Extracted Data"}
                      </p>
                      <h2 className="text-[18px] font-extrabold text-[var(--text-1)] sm:text-[20px]">
                        Document Detail
                      </h2>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-xl bg-[var(--brand-tint)] px-3 py-2 text-[12px] font-semibold text-[var(--brand-mid)]">
                        {target.document_type?.toUpperCase() || "UNKNOWN"}
                      </span>
                      <span className="rounded-xl bg-[var(--surface-2)] px-3 py-2 text-[12px] text-[var(--text-2)]">
                        {target.currency || "NULL"}
                      </span>
                      <span className="rounded-xl bg-[var(--success-tint)] px-3 py-2 text-[12px] font-semibold text-[var(--success)]">
                        {target.status || "ready"}
                      </span>
                    </div>
                  </div>

                  {/* AI/OCR Confidence badge */}
                  <div className="mt-3 flex items-center gap-2">
                    <span
                      className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] font-bold"
                      style={{ background: "rgba(34,82,181,0.08)", color: "#2252b5" }}
                    >
                      <span className="material-symbols-outlined text-[13px]">smart_toy</span>
                      AI / OCR
                    </span>
                    <span
                      className="rounded-lg px-3 py-1.5 text-[11px] font-bold"
                      style={
                        target.arithmetic_status === "matched"
                          ? { background: "rgba(22,163,74,0.08)", color: "#16a34a" }
                          : target.arithmetic_status === "mismatch"
                          ? { background: "rgba(220,38,38,0.08)", color: "#dc2626" }
                          : { background: "rgba(100,116,139,0.08)", color: "#64748b" }
                      }
                    >
                      {target.arithmetic_status === "matched" ? "99% CONFIDENCE"
                        : target.arithmetic_status === "mismatch" ? "VERIFY TOTALS"
                        : "OCR PROCESSED"}
                    </span>
                  </div>
                </div>

                {/* DN / PO notice badge */}
                {(target.document_type === "dn" || target.document_type === "po") && (
                  <div className="mt-3 flex items-center gap-2 rounded-xl px-4 py-2.5 text-[12px] font-semibold"
                    style={{
                      background: target.document_type === "dn" ? "rgba(234,108,10,0.08)" : "rgba(124,58,237,0.08)",
                      color: target.document_type === "dn" ? "#ea6c0a" : "#7c3aed",
                      border: `1px solid ${target.document_type === "dn" ? "rgba(234,108,10,0.2)" : "rgba(124,58,237,0.2)"}`,
                    }}>
                    <span className="material-symbols-outlined text-[15px]">
                      {target.document_type === "dn" ? "local_shipping" : "shopping_cart"}
                    </span>
                    {target.document_type === "dn"
                      ? (lang === "si" ? "බෙදාහැරීමේ සටහන — මූල්‍ය ගනුදෙනුවක් නොවේ" : "Delivery Note — no financial amounts")
                      : (lang === "si" ? "ගෙවීම් නියෝගය — ගෙවිය යුතු ගනුදෙනුව" : "Purchase Order — payable transaction")}
                  </div>
                )}

                <div className="mt-4 space-y-4">
                  <InfoCard title={t.metadataTitle}>
                    <span className="font-semibold text-[var(--text-1)]">{t.documentIdLabel}:</span> {target.document_id}
                    <br />
                    <span className="font-semibold text-[var(--text-1)]">
                      {target.document_type === "dn" ? (lang === "si" ? "PO යොමු:" : "PO Reference:") :
                       target.document_type === "po" ? (lang === "si" ? "PO අංකය:" : "PO Number:") :
                       `${t.orderIdLabel}:`}
                    </span>{" "}
                    {editMode ? (
                      <input
                        value={target.order_id || ""}
                        onChange={(e) => updateField("order_id", e.target.value)}
                        className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                      />
                    ) : target.order_id || "NULL"}
                    <br />
                    <span className="font-semibold text-[var(--text-1)]">{t.dateLabel}:</span>{" "}
                    {editMode ? (
                      <input
                        value={target.date || ""}
                        onChange={(e) => updateField("date", e.target.value)}
                        className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                      />
                    ) : target.date || "NULL"}
                    <br />
                    <span className="font-semibold text-[var(--text-1)]">{t.partyLabel}:</span> {formatParty()}
                    <br />
                    {/* Hide Flow Type and Category for DN (no amounts) and PO (always payable) */}
                    {target.document_type !== "dn" && target.document_type !== "po" && (
                      <>
                        <span className="font-semibold text-[var(--text-1)]">{t.flowTypeLabel}:</span>{" "}
                        {editMode ? (
                          <select
                            value={target.flow_type || "unknown"}
                            onChange={(e) => updateField("flow_type", e.target.value)}
                            className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                          >
                            <option value="unknown">{lang === "si" ? "නොදනී" : "Unknown"}</option>
                            <option value="payable">{humanizeFlow("payable", lang)}</option>
                            <option value="receivable">{humanizeFlow("receivable", lang)}</option>
                            <option value="cash_inflow">{humanizeFlow("cash_inflow", lang)}</option>
                            <option value="cash_outflow">{humanizeFlow("cash_outflow", lang)}</option>
                          </select>
                        ) : (target.flow_type && target.flow_type !== "NULL" ? humanizeFlow(target.flow_type, lang) : "—")}
                        <br />
                        <span className="font-semibold text-[var(--text-1)]">{t.categoryLabel}:</span>{" "}
                        {(() => {
                          const ft = (target.flow_type || "").toLowerCase();
                          const derived = (target.category && target.category !== "NULL")
                            ? target.category
                            : ["receivable","cash_inflow"].includes(ft) ? t.categoryRevenue
                            : ["payable","cash_outflow"].includes(ft) ? t.categoryExpenses
                            : t.categoryUnknown;
                          return (
                            <span className={`ml-1 inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-bold ${
                              derived === "Revenue"  ? "bg-[var(--success-tint)] text-[var(--success)]" :
                              derived === "Expenses" ? "bg-[var(--danger-tint)] text-[var(--danger)]"    :
                                                       "bg-[var(--surface-2)] text-[var(--text-3)]"
                            }`}>{derived}</span>
                          );
                        })()}
                        <br />
                      </>
                    )}
                  </InfoCard>

                  <InfoCard title={t.partiesTitle}>
                    <span className="font-semibold text-[var(--text-1)]">
                      {lang === "si" ? "ඔබගේ සමාගම:" : "Your Company:"}
                    </span>{" "}
                    {editMode ? (
                      <input
                        value={target.company_name || ""}
                        onChange={(e) => updateField("company_name", e.target.value)}
                        className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                      />
                    ) : target.company_name || "NULL"}
                    <br />

                    {/* DN: role selector — Delivered By or Delivered To */}
                    {target.document_type === "dn" && (
                      <>
                        <span className="font-semibold text-[var(--text-1)]">
                          {lang === "si" ? "භූමිකාව:" : "Role:"}
                        </span>{" "}
                        <select
                          value={target.flow_type === "income" ? "delivered_to" : "delivered_by"}
                          onChange={(e) => updateField("flow_type", e.target.value === "delivered_to" ? "income" : "expense")}
                          className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                        >
                          <option value="delivered_by">{lang === "si" ? "අපට ලබා දෙනු ලැබිණි (Delivered By)" : "Delivered By (we received goods)"}</option>
                          <option value="delivered_to">{lang === "si" ? "අපි ලබා දුන්නෙමු (Delivered To)" : "Delivered To (we sent goods)"}</option>
                        </select>
                        <br />
                        <span className="font-semibold text-[var(--text-1)]">
                          {target.flow_type === "income"
                            ? (lang === "si" ? "ලබා දුන් ගනුදෙනුකරු:" : "Delivered To (Customer):")
                            : (lang === "si" ? "ලබාදෙන්නා (සැපයුම්කරු):" : "Delivered By (Supplier):")}
                        </span>{" "}
                        {editMode ? (
                          <input
                            value={target.supplier_name || ""}
                            onChange={(e) => updateField("supplier_name", e.target.value)}
                            className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                          />
                        ) : target.supplier_name || "NULL"}
                      </>
                    )}

                    {/* Non-DN: standard supplier label */}
                    {target.document_type !== "dn" && (
                      <>
                        <span className="font-semibold text-[var(--text-1)]">
                          {(() => {
                            const dt = target.document_type || "";
                            const ft = target.flow_type || "";
                            if (dt === "po") return lang === "si" ? "සැපයුම්කරු:" : "Supplier:";
                            if (dt === "invoice") {
                              if (["receivable","cash_inflow"].includes(ft)) return lang === "si" ? "ගනුදෙනුකරු:" : "Bill To (Customer):";
                              return lang === "si" ? "සැපයුම්කරු:" : "Bill From (Supplier):";
                            }
                            if (dt === "receipt") {
                              if (["cash_inflow","receivable"].includes(ft)) return lang === "si" ? "ලැබුනේ:" : "Received From (Customer):";
                              return lang === "si" ? "ගෙව්වේ:" : "Paid To (Supplier):";
                            }
                            return ["receivable","cash_inflow"].includes(ft)
                              ? (lang === "si" ? "ගනුදෙනුකරු:" : "Customer:")
                              : (lang === "si" ? "සැපයුම්කරු:" : "Supplier:");
                          })()}
                        </span>{" "}
                        {editMode ? (
                          <input
                            value={target.supplier_name || ""}
                            onChange={(e) => updateField("supplier_name", e.target.value)}
                            className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                          />
                        ) : target.supplier_name || "NULL"}
                      </>
                    )}
                  </InfoCard>

                  {target.document_type !== "dn" && <InfoCard title={target.document_type === "po" ? (lang === "si" ? "ඇණවුම් සාරාංශය" : "Order Summary") : t.financialSummaryTitle}>
                    {(() => {
                      const ft  = (target.flow_type || "").toLowerCase();
                      const cur = target.currency && target.currency !== "NULL" ? target.currency : "LKR";
                      const finalTotal    = parseFloat(String(target.final_total_amount ?? 0)) || 0;
                      const cashInflowed  = parseFloat(String(target.cash_inflowed  ?? 0)) || 0;
                      const cashOutflowed = parseFloat(String(target.cash_outflowed ?? 0)) || 0;
                      const receivableAmt = Math.max(0, finalTotal - cashInflowed);
                      const payableAmt    = Math.max(0, finalTotal - cashOutflowed);

                      return (
                        <>
                          {/* Raw total always shown */}
                          <span className="font-semibold text-[var(--text-1)]">{t.rawTotalLabel}:</span>{" "}
                          {editMode ? (
                            <input value={String(target.raw_total_amount || "")} onChange={(e) => updateField("raw_total_amount", e.target.value)} className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]" />
                          ) : target.raw_total_amount || "NULL"}
                          <br />

                          {/* Final total always shown */}
                          <span className="font-semibold text-[var(--text-1)]">{t.finalTotalLabel}:</span>{" "}
                          {editMode ? (
                            <input value={String(target.final_total_amount || "")} onChange={(e) => updateField("final_total_amount", e.target.value)} className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]" />
                          ) : `${cur} ${finalTotal.toFixed(2)}`}
                          <br />

                          {/* RECEIVABLE — track partial payments */}
                          {ft === "receivable" && (
                            <>
                              <span className="font-semibold text-[var(--text-1)]">{t.cashInflowedLabel}:</span>{" "}
                              {editMode ? (
                                <input type="number" min="0" value={String(target.cash_inflowed ?? "")} onChange={(e) => updateField("cash_inflowed", e.target.value)} className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px] w-32" />
                              ) : `${cur} ${cashInflowed.toFixed(2)}`}
                              <br />
                              <span className="font-semibold text-[var(--text-1)]">{t.receivableAmountLabel}:</span>{" "}
                              <span className={receivableAmt === 0 ? "ml-1 font-bold text-[var(--success)]" : "ml-1 font-bold text-[var(--brand-mid)]"}>
                                {cur} {receivableAmt.toFixed(2)}{receivableAmt === 0 ? " {t.fullyReceived}" : ""}
                              </span>
                              <br />
                            </>
                          )}

                          {/* CASH INFLOW — amount is the final total, no tracking needed */}
                          {ft === "cash_inflow" && (
                            <>
                              <span className="font-semibold text-[var(--text-1)]">{t.cashInflowedLabel}:</span>{" "}
                              <span className="ml-1 font-bold text-[var(--success)]">{cur} {finalTotal.toFixed(2)} ✓ Received</span>
                              <br />
                            </>
                          )}

                          {/* PAYABLE — track partial payments */}
                          {ft === "payable" && (
                            <>
                              <span className="font-semibold text-[var(--text-1)]">{t.cashOutflowedLabel}:</span>{" "}
                              {editMode ? (
                                <input type="number" min="0" value={String(target.cash_outflowed ?? "")} onChange={(e) => updateField("cash_outflowed", e.target.value)} className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px] w-32" />
                              ) : `${cur} ${cashOutflowed.toFixed(2)}`}
                              <br />
                              <span className="font-semibold text-[var(--text-1)]">{t.payableAmountLabel}:</span>{" "}
                              <span className={payableAmt === 0 ? "ml-1 font-bold text-[var(--success)]" : "ml-1 font-bold text-[var(--danger)]"}>
                                {cur} {payableAmt.toFixed(2)}{payableAmt === 0 ? " {t.fullyPaid}" : ""}
                              </span>
                              <br />
                            </>
                          )}

                          {/* CASH OUTFLOW — amount is the final total, no tracking needed */}
                          {ft === "cash_outflow" && (
                            <>
                              <span className="font-semibold text-[var(--text-1)]">{t.cashOutflowedLabel}:</span>{" "}
                              <span className="ml-1 font-bold text-[var(--success)]">{cur} {finalTotal.toFixed(2)} ✓ Paid</span>
                              <br />
                            </>
                          )}

                          <span className="font-semibold text-[var(--text-1)]">{t.currencyLabel}:</span>{" "}
                          {editMode ? (
                            <input value={target.currency || ""} onChange={(e) => updateField("currency", e.target.value)} className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]" />
                          ) : target.currency || "NULL"}
                        </>
                      );
                    })()}
                  </InfoCard>}

                  {/* DN: Delivery Status only | PO: PO Status only | others: full status */}
                  <InfoCard title={
                    target.document_type === "dn" ? (lang === "si" ? "බෙදාහැරීමේ තත්ත්වය" : "Delivery Status") :
                    target.document_type === "po" ? (lang === "si" ? "PO තත්ත්වය" : "PO Status") :
                    t.statusTitle
                  }>
                    {target.document_type === "dn" ? (
                      <>
                        <span className="font-semibold text-[var(--text-1)]">{lang === "si" ? "බෙදාහැරීමේ තත්ත්වය:" : "Delivery Status:"}</span>{" "}
                        {editMode ? (
                          <select value={target.received_status || ""}
                            onChange={(e) => updateField("received_status", e.target.value)}
                            className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]">
                            <option value="">Select…</option>
                            <option value="delivered">{lang === "si" ? "ලබාදෙන ලදී" : "Delivered"}</option>
                            <option value="not_delivered">{lang === "si" ? "ලබා නොදුනේ" : "Not Delivered"}</option>
                            <option value="partial">{lang === "si" ? "අර්ධ" : "Partial"}</option>
                          </select>
                        ) : (
                          <span className={`ml-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${
                            target.received_status === "delivered" ? "bg-[var(--success-tint)] text-[var(--success)]" :
                            target.received_status === "not_delivered" ? "bg-[var(--danger-tint)] text-[var(--danger)]" :
                            "bg-[var(--warn-tint)] text-[var(--warn)]"
                          }`}>
                            {target.received_status === "delivered" ? (lang === "si" ? "ලබාදෙන ලදී" : "Delivered") :
                             target.received_status === "not_delivered" ? (lang === "si" ? "ලබා නොදුනේ" : "Not Delivered") :
                             target.received_status === "partial" ? (lang === "si" ? "අර්ධ" : "Partial") :
                             target.received_status || "—"}
                          </span>
                        )}
                        <br />
                        <span className="font-semibold text-[var(--text-1)]">{t.languageLabel}:</span>{" "}
                        {target.language || "NULL"}
                      </>
                    ) : target.document_type === "po" ? (
                      <>
                        <span className="font-semibold text-[var(--text-1)]">{lang === "si" ? "PO තත්ත්වය:" : "PO Status:"}</span>{" "}
                        {editMode ? (
                          <select value={target.paid_status || ""}
                            onChange={(e) => updateField("paid_status", e.target.value)}
                            className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]">
                            <option value="">Select…</option>
                            <option value="not_paid">{lang === "si" ? "අනුමත නොකළ" : "Pending"}</option>
                            <option value="partial">{lang === "si" ? "අනුමත කළ" : "Approved"}</option>
                            <option value="paid">{lang === "si" ? "සම්පූර්ණ කළ" : "Fulfilled"}</option>
                            <option value="NULL">{lang === "si" ? "අවලංගු" : "Cancelled"}</option>
                          </select>
                        ) : (
                          <span className={`ml-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${
                            target.paid_status === "paid" ? "bg-[var(--success-tint)] text-[var(--success)]" :
                            target.paid_status === "not_paid" ? "bg-[var(--warn-tint)] text-[var(--warn)]" :
                            "bg-[var(--surface-2)] text-[var(--text-3)]"
                          }`}>
                            {target.paid_status === "paid" ? "Fulfilled" :
                             target.paid_status === "not_paid" ? "Pending" :
                             target.paid_status === "partial" ? "Approved" :
                             target.paid_status === "NULL" ? "Cancelled" :
                             target.paid_status || "Pending"}
                          </span>
                        )}
                        <br />
                        <span className="font-semibold text-[var(--text-1)]">{t.languageLabel}:</span>{" "}
                        {target.language || "NULL"}

                        {/* IT-27: PO approval workflow */}
                        <div className="mt-3 border-t border-[var(--border)] pt-3">
                          <span className="font-semibold text-[var(--text-1)]">
                            {lang === "si" ? "අනුමැතිය:" : "Approval:"}
                          </span>{" "}
                          <span className={`ml-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${
                            target.po_status === "approved" ? "bg-[var(--success-tint)] text-[var(--success)]" :
                            target.po_status === "rejected" ? "bg-[var(--danger-tint)] text-[var(--danger)]" :
                            "bg-[var(--warn-tint)] text-[var(--warn)]"
                          }`}>
                            {target.po_status === "approved" ? t.poApproved :
                             target.po_status === "rejected" ? t.poRejected :
                             (lang === "si" ? "පොරොත්තුවෙන්" : "Pending")}
                          </span>

                          {target.approved_by && target.po_status === "approved" && (
                            <p className="mt-1 text-[12px] text-[var(--text-2)]">
                              {t.poApprovedBy}: {target.approved_by}
                            </p>
                          )}

                          {!editMode && (
                            <div className="mt-2 flex gap-2">
                              <button
                                onClick={() => handlePoAction("approved")}
                                disabled={poActionLoading || target.po_status === "approved"}
                                className="rounded-lg bg-green-600 px-3 py-1.5 text-[12px] font-bold text-white transition hover:opacity-90 disabled:opacity-40"
                              >
                                {t.poApprove}
                              </button>
                              <button
                                onClick={() => handlePoAction("rejected")}
                                disabled={poActionLoading || target.po_status === "rejected"}
                                className="rounded-lg border border-[var(--danger-border)] bg-[var(--danger-tint)] px-3 py-1.5 text-[12px] font-bold text-[var(--danger)] transition hover:opacity-90 disabled:opacity-40"
                              >
                                {t.poReject}
                              </button>
                            </div>
                          )}
                        </div>
                      </>
                    ) : (
                      // Invoice / Receipt — full status
                      <>
                        <span className="font-semibold text-[var(--text-1)]">{t.receivedStatusLabel}:</span>{" "}
                        {editMode ? (
                          <select value={target.received_status || "NULL"} onChange={(e) => updateField("received_status", e.target.value)} className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]">
                            <option value="NULL">NULL</option>
                            <option value="received">received</option>
                            <option value="not_received">not_received</option>
                            <option value="partial">partial</option>
                          </select>
                        ) : target.received_status || "NULL"}
                        <br />
                        <span className="font-semibold text-[var(--text-1)]">{t.paidStatusLabel}:</span>{" "}
                        {editMode ? (
                          <select value={target.paid_status || "NULL"} onChange={(e) => updateField("paid_status", e.target.value)} className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]">
                            <option value="NULL">NULL</option>
                            <option value="paid">paid</option>
                            <option value="not_paid">not_paid</option>
                            <option value="partial">partial</option>
                          </select>
                        ) : target.paid_status || "NULL"}
                        <br />
                        <span className="font-semibold text-[var(--text-1)]">{t.languageLabel}:</span>{" "}
                        {editMode ? (
                          <select value={target.language || "en"} onChange={(e) => updateField("language", e.target.value)} className="ml-2 rounded border border-[var(--border)] px-2 py-1 text-[13px]">
                            <option value="en">en</option>
                            <option value="si">si</option>
                          </select>
                        ) : target.language || "NULL"}
                      </>
                    )}
                  </InfoCard>

                  {/* TAX DETAILS — hidden for DN (no financial value) */}
                  {target.document_type !== "dn" && <InfoCard title={t.taxDetailsTitle}>
                    {(() => {
                      const cur = target.currency && target.currency !== "NULL" ? target.currency : "LKR";

                      // Subtotal computed from line items (falls back to qty × unit_price)
                      const subtotal = (target.items || []).reduce((sum, item) => {
                        const lt = item.line_total != null && item.line_total !== ""
                          ? parseFloat(String(item.line_total))
                          : (parseFloat(String(item.quantity || 0)) || 0) * (parseFloat(String(item.unit_price || 0)) || 0);
                        return sum + (isNaN(lt) ? 0 : lt);
                      }, 0);

                      const rawTax  = target.tax_amount;
                      const rawRate = target.tax_rate;
                      const taxAmt  = rawTax  != null && rawTax  !== "NULL" && rawTax  !== "" ? parseFloat(String(rawTax))  : 0;
                      const taxRate = rawRate != null && rawRate !== "NULL" && rawRate !== "" ? parseFloat(String(rawRate)) : 0;
                      const finalTotal = parseFloat(String(target.final_total_amount ?? 0)) || 0;
                      const expectedTotal = subtotal + (isNaN(taxAmt) ? 0 : taxAmt);
                      const mismatch = subtotal > 0 && Math.abs(expectedTotal - finalTotal) > 0.5;

                      // Typing a tax rate auto-computes the tax amount from the subtotal
                      const handleRateChange = (val: string) => {
                        updateField("tax_rate", val);
                        const rateNum = parseFloat(val);
                        if (!isNaN(rateNum) && subtotal > 0) {
                          updateField("tax_amount", (subtotal * rateNum / 100).toFixed(2));
                        }
                      };

                      if (editMode) {
                        return (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-[var(--text-1)]">{lang === "si" ? "උප එකතුව (අයිතම වලින්)" : "Subtotal (from items)"}</span>
                              <span className="font-bold text-[var(--text-2)]">{cur} {subtotal.toFixed(2)}</span>
                            </div>
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-[var(--text-1)]">{lang === "si" ? "බදු % (Tax Rate)" : "Tax Rate (%)"}</span>
                              <input
                                type="number" min="0" step="0.01"
                                value={rawRate != null && rawRate !== "NULL" ? String(rawRate) : ""}
                                onChange={(e) => handleRateChange(e.target.value)}
                                placeholder="e.g. 15"
                                className="ml-2 w-28 rounded border border-[var(--border)] px-2 py-1 text-right text-[13px]"
                              />
                            </div>
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-[var(--text-1)]">{lang === "si" ? "බදු මුදල (Tax Amount)" : "Tax Amount"}</span>
                              <input
                                type="number" min="0" step="0.01"
                                value={rawTax != null && rawTax !== "NULL" ? String(rawTax) : ""}
                                onChange={(e) => updateField("tax_amount", e.target.value)}
                                placeholder="0.00"
                                className="ml-2 w-28 rounded border border-[var(--border)] px-2 py-1 text-right text-[13px]"
                              />
                            </div>
                            <div className="flex items-center justify-between border-t border-[var(--border)] pt-2">
                              <span className="font-semibold text-[var(--text-1)]">{lang === "si" ? "අපේක්ෂිත එකතුව" : "Expected Total"}</span>
                              <span className={`font-bold ${mismatch ? "text-[var(--danger)]" : "text-[var(--success)]"}`}>{cur} {expectedTotal.toFixed(2)}</span>
                            </div>
                            {mismatch && (
                              <p className="rounded-lg bg-[var(--danger-tint)] px-3 py-2 text-[12px] text-[var(--danger)]">
                                {lang === "si"
                                  ? `නොගැලපේ: උප එකතුව + බදුව (${cur} ${expectedTotal.toFixed(2)}) ≠ මුළු එකතුව (${cur} ${finalTotal.toFixed(2)})`
                                  : `Mismatch: Subtotal + Tax (${cur} ${expectedTotal.toFixed(2)}) ≠ Final Total (${cur} ${finalTotal.toFixed(2)})`}
                              </p>
                            )}
                          </div>
                        );
                      }

                      // Read-only view
                      if (!isNaN(taxAmt) && taxAmt > 0) {
                        const label = !isNaN(taxRate) && taxRate > 0 ? `Tax (${taxRate}%)` : "Tax";
                        return (
                          <div className="space-y-1.5">
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-[var(--text-1)]">{label}</span>
                              <span className="font-bold text-[var(--brand-mid)]">{cur} {taxAmt.toFixed(2)}</span>
                            </div>
                            {mismatch && (
                              <p className="rounded-lg bg-[var(--danger-tint)] px-3 py-2 text-[12px] text-[var(--danger)]">
                                {lang === "si"
                                  ? `ගණිතමය නොගැලපීම: ${cur} ${expectedTotal.toFixed(2)} ≠ ${cur} ${finalTotal.toFixed(2)}`
                                  : `Arithmetic mismatch: Subtotal + Tax (${cur} ${expectedTotal.toFixed(2)}) doesn't match Final Total (${cur} ${finalTotal.toFixed(2)})`}
                              </p>
                            )}
                          </div>
                        );
                      }
                      return (
                        <div className="space-y-1.5">
                          <span className="text-[var(--text-3)]">{t.noTaxOnDocument}</span>
                          {mismatch && (
                            <p className="rounded-lg bg-[var(--warn-tint)] px-3 py-2 text-[12px] text-[var(--warn)]">
                              {lang === "si"
                                ? `උප එකතුව (${cur} ${subtotal.toFixed(2)}) මුළු එකතුවට (${cur} ${finalTotal.toFixed(2)}) නොගැලපේ — බදුවක් මග හැරී ඇතිදැයි පරීක්ෂා කරන්න.`
                                : `Subtotal (${cur} ${subtotal.toFixed(2)}) doesn't match the Final Total (${cur} ${finalTotal.toFixed(2)}) — check if a tax amount was missed during extraction.`}
                            </p>
                          )}
                        </div>
                      );
                    })()}
                  </InfoCard>}

                  <div className="rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
                    <p className="text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--text-2)]">
                      {t.itemsTitle}
                    </p>

                    <div className="mt-3 space-y-3">
                      {target.items && target.items.length > 0 ? (
                        target.items.map((item, index) => (
                          <div
                            key={index}
                            className={`grid gap-2 rounded-[12px] border border-[var(--border)] p-3 ${target.document_type === "dn" ? "sm:grid-cols-2" : "sm:grid-cols-3"}`}
                          >
                            <div className="text-[14px] text-[var(--text-1)]">
                              <span className="font-semibold">{t.descriptionLabel}:</span>{" "}
                              {editMode ? (
                                <input
                                  value={String(item.description || "")}
                                  onChange={(e) => updateItemField(index, "description", e.target.value)}
                                  className="mt-1 w-full rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                                />
                              ) : item.description || "NULL"}
                            </div>
                            <div className="text-[14px] text-[var(--text-1)]">
                              <span className="font-semibold">{t.quantityLabel}:</span>{" "}
                              {editMode ? (
                                <input
                                  value={String(item.quantity ?? "")}
                                  onChange={(e) => updateItemField(index, "quantity", e.target.value)}
                                  className="mt-1 w-full rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                                />
                              ) : item.quantity ?? "NULL"}
                            </div>
                            {/* Unit price hidden for DN — no financial value on delivery notes */}
                            {target.document_type !== "dn" && (
                              <div className="text-[14px] text-[var(--text-1)]">
                                <span className="font-semibold">{t.unitPriceLabel}:</span>{" "}
                                {editMode ? (
                                  <input
                                    value={String(item.unit_price ?? "")}
                                    onChange={(e) => updateItemField(index, "unit_price", e.target.value)}
                                    className="mt-1 w-full rounded border border-[var(--border)] px-2 py-1 text-[13px]"
                                  />
                                ) : item.unit_price ?? "NULL"}
                              </div>
                            )}
                          </div>
                        ))
                      ) : (
                        <p className="text-[14px] text-[var(--text-2)]">No items available.</p>
                      )}
                    </div>
                  </div>
                </div>

                {/* IT-22 — Related Documents (PO→DN→Invoice link) */}
                {relatedDocs.length > 0 && (
                  <div className="mt-4 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
                    <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-[var(--text-2)]">
                      {lang === "si" ? "සම්බන්ධිත ලේඛන" : "Related Documents"}
                    </p>
                    <div className="mt-3 space-y-2">
                      {relatedDocs.map((rd) => (
                        <button
                          key={rd.document_id}
                          onClick={() => router.push(`/analysis/${rd.document_id}`)}
                          className="flex w-full items-center justify-between rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] px-4 py-3 text-left transition hover:border-[var(--brand-mid)] hover:bg-[var(--brand-tint)]"
                        >
                          <div>
                            <span className="text-[11px] font-bold uppercase" style={{ color: rd.document_type === "po" ? "#7c3aed" : rd.document_type === "dn" ? "#ea6c0a" : "#2252b5" }}>
                              {rd.document_type?.toUpperCase()}
                            </span>
                            <p className="text-[14px] font-semibold text-[var(--text-1)]">{rd.document_id}</p>
                            <p className="text-[11px] text-[var(--text-2)]">{rd.supplier_name} · {rd.date}</p>
                            <p className="text-[10px] text-[var(--text-3)]">{rd.link_reason}</p>
                          </div>
                          <div className="text-right">
                            {rd.final_total_amount && String(rd.final_total_amount) !== "NULL" && (
                              <p className="text-[13px] font-bold text-[var(--text-1)]">{rd.currency || "LKR"} {rd.final_total_amount}</p>
                            )}
                            <span className="material-symbols-outlined text-[16px] text-[var(--text-3)]">chevron_right</span>
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Provenance Panel — Iteration 7 */}
                <div className="mt-4 rounded-[18px] border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                  <button
                    onClick={() => setShowProvenance((prev) => !prev)}
                    className="flex w-full items-center justify-between px-5 py-4 text-left"
                  >
                    <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text-2)]">
                      {t.fieldProvenance ?? "Field Provenance"}
                    </span>
                    <span className="material-symbols-outlined text-[var(--text-2)]">
                      {showProvenance ? "expand_less" : "expand_more"}
                    </span>
                  </button>
                  {showProvenance && (
                    <div className="border-t border-[var(--border)] px-4 py-4">
                      <ProvenancePanel
                        doc={target as Record<string, unknown>}
                        arithmeticJson={target.arithmetic_json}
                        activeChunkId={activeChunkId}
                        fieldChunkMap={
                          target.field_chunk_map_json
                            ? (() => { try { return JSON.parse(target.field_chunk_map_json!); } catch { return null; } })()
                            : null
                        }
                        onChunkSelect={setActiveChunkId}
                      />
                    </div>
                  )}
                </div>

                {editMode ? (
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="mt-5 w-full rounded-[18px] bg-[var(--brand)] py-4 text-[15px] font-bold text-white shadow-[0_10px_24px_rgba(37,99,255,0.22)] disabled:opacity-60"
                  >
                    {saving ? "Saving..." : "Save Changes"}
                  </button>
                ) : (
                  <button
                    onClick={() => router.push("/repository")}
                    className="mt-5 w-full rounded-[18px] bg-[var(--brand)] py-4 text-[15px] font-bold text-white shadow-[0_10px_24px_rgba(37,99,255,0.22)]"
                  >
                    {t.backToDashboard ?? "Back to Repository"}
                  </button>
                )}
              </div>
            </div>
          )}
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}