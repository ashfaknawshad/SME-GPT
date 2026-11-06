"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import TopBar from "@/components/layout/TopBar";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { addNotification } from "@/lib/notifications";
import { getSession } from "@/lib/auth";
import { otherPartyName } from "@/lib/format";
import { resolveBackendUrl } from "@/lib/backendUrl";
import ScanProgress from "@/components/ui/ScanProgress";
import MathCheck from "@/components/ui/MathCheck";

type PreviewItem = {
  description: string;
  quantity: string | number;
  unit_price: string | number;
  line_total: string | number;
};

type PreviewData = {
  document_type: string; order_id: string; po_ref?: string; flow_type: string;
  company_name: string; supplier_name: string; date: string;
  currency: string; raw_total_amount: string | number;
  final_total_amount: string | number; payable_amount: string | number;
  cash_return: string | number; received_status: string;
  paid_status: string; items: PreviewItem[];
  tax_amount?: string | number;
  tax_rate?: string | number;
  tax_inclusive?: boolean;
  discount_amount?: string | number;
};

type StreamEvent = {
  stage?: string;
  message?: string;
  step?: number;
  preview?: PreviewData;
  session_id?: string;
};

/** An extracted-but-not-yet-saved document, parked so a reload can't lose it. */
type UploadDraft = {
  sessionId: string;
  preview: PreviewData;
};

const BACKEND_URL = resolveBackendUrl();

// sessionStorage (not localStorage): the backend session this points at expires
// in ~2h, so the draft should die with the tab rather than resurface tomorrow
// pointing at a session that no longer exists.
const DRAFT_KEY = "sme_gpt_upload_draft";

function getAuthToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

// ── Field definitions per document type ─────────────────────────────────────

const DOC_TYPE_OPTS = [
  { label: "Select…", value: "" },
  { label: "Invoice",  value: "invoice"  },
  { label: "Receipt",  value: "receipt"  },
  { label: "PO",       value: "po"       },
  { label: "DN",       value: "dn"       },
  { label: "Unknown",  value: "unknown"  },
];

const FLOW_TYPE_OPTS = [
  { label: "Select…",      value: "" },
  { label: "Payable",      value: "payable"      },
  { label: "Receivable",   value: "receivable"   },
  { label: "Cash Inflow",  value: "cash_inflow"  },
  { label: "Cash Outflow", value: "cash_outflow" },
];

const RECEIVED_STATUS_OPTS = [
  { label: "Select…",      value: "" },
  { label: "Received",     value: "received"     },
  { label: "Not Received", value: "not_received" },
  { label: "Partial",      value: "partial"      },
  { label: "NULL",         value: "NULL"         },
];

const DELIVERY_STATUS_OPTS = [
  { label: "Select…",       value: "" },
  { label: "Delivered",     value: "delivered"     },
  { label: "Not Delivered", value: "not_delivered" },
  { label: "Partial",       value: "partial"       },
];

const PAID_STATUS_OPTS = [
  { label: "Select…",  value: "" },
  { label: "Paid",     value: "paid"     },
  { label: "Not Paid", value: "not_paid" },
  { label: "Partial",  value: "partial"  },
  { label: "NULL",     value: "NULL"     },
];

const PO_STATUS_OPTS = [
  { label: "Select…",   value: "" },
  { label: "Pending",   value: "not_paid"  },
  { label: "Approved",  value: "partial"   },
  { label: "Fulfilled", value: "paid"      },
  { label: "Cancelled", value: "NULL"      },
];

type FieldRow = { key: string; label: string; opts?: { label: string; value: string }[]; readonly?: boolean };

function getSupplierLabel(docType: string, flowType: string): string {
  if (docType === "dn")  return "Delivered By (Supplier)";
  if (docType === "po")  return "Supplier";
  if (docType === "invoice") {
    if (flowType === "receivable" || flowType === "cash_inflow") return "Bill To (Customer)";
    return "Bill From (Supplier)";
  }
  if (docType === "receipt") {
    if (flowType === "cash_inflow" || flowType === "receivable") return "Received From (Customer)";
    return "Paid To (Supplier)";
  }
  // unknown / fallback
  if (flowType === "receivable" || flowType === "cash_inflow") return "Customer";
  return "Supplier";
}

function getFieldRows(docType: string, flowType: string = ""): FieldRow[] {
  const supplierLabel = getSupplierLabel(docType, flowType);

  if (docType === "dn") return [
    { key: "document_type",   label: "Document Type",          opts: DOC_TYPE_OPTS },
    { key: "order_id",        label: "DN Number"               },
    { key: "po_ref",    label: "PO Reference (links to PO)" },
    { key: "company_name",    label: "Your Company",           readonly: true },
    { key: "supplier_name",   label: supplierLabel             },
    { key: "date",            label: "Delivery Date"           },
    { key: "received_status", label: "Delivery Status",        opts: DELIVERY_STATUS_OPTS },
  ];

  if (docType === "po") return [
    { key: "document_type",      label: "Document Type",       opts: DOC_TYPE_OPTS },
    { key: "order_id",           label: "PO Number"            },
    { key: "company_name",       label: "Your Company",        readonly: true },
    { key: "supplier_name",      label: supplierLabel          },
    { key: "date",               label: "Order Date"           },
    { key: "currency",           label: "Currency"             },
    { key: "tax_rate",           label: "Tax Rate (%)"         },
    { key: "tax_amount",         label: "Tax Amount"           },
    { key: "discount_amount",    label: "Discount"             },
    { key: "final_total_amount", label: "Order Total (items - discount + tax)", readonly: true },
    { key: "paid_status",        label: "PO Status",           opts: PO_STATUS_OPTS },
  ];

  // invoice / receipt / unknown — full form
  return [
    { key: "document_type",      label: "Document Type",       opts: DOC_TYPE_OPTS },
    { key: "order_id",           label: "Reference / Bill No." },
    // Only an invoice can belong to a purchase order.
    ...(docType === "invoice" ? [{ key: "po_ref", label: "PO Reference (links to PO)" }] : []),
    { key: "flow_type",          label: "Flow Type",           opts: FLOW_TYPE_OPTS },
    { key: "company_name",       label: "Your Company",        readonly: true },
    { key: "supplier_name",      label: supplierLabel          },
    { key: "date",               label: "Date"                 },
    { key: "currency",           label: "Currency"             },
    { key: "raw_total_amount",   label: "Raw Total (OCR)",     readonly: true },
    { key: "tax_rate",           label: "Tax Rate (%)"         },
    { key: "tax_amount",         label: "Tax Amount"           },
    { key: "discount_amount",    label: "Discount"             },
    { key: "final_total_amount", label: "Final Total (items - discount + tax)", readonly: true },
    { key: "payable_amount",     label: "Payable / Receivable",readonly: true },
    { key: "cash_return",        label: "Cash Return"          },
    { key: "received_status",    label: "Received Status",     opts: RECEIVED_STATUS_OPTS },
    { key: "paid_status",        label: "Paid Status",         opts: PAID_STATUS_OPTS },
  ];
}

function getItemFields(docType: string): ("description" | "quantity" | "unit_price")[] {
  // DN: only description + qty; everything else gets all three
  return docType === "dn" ? ["description", "quantity"] : ["description", "quantity", "unit_price"];
}

export default function UploadPage() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pdfInputRef = useRef<HTMLInputElement | null>(null);
  const previewRef  = useRef<HTMLDivElement | null>(null);
  const videoRef    = useRef<HTMLVideoElement | null>(null);
  const canvasRef   = useRef<HTMLCanvasElement | null>(null);
  const [lang, setLang] = useState<AppLanguage>("en");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [error, setError] = useState("");
  // After a successful save we show a "what next?" card instead of silently
  // resetting — lets the user query, view the doc, or keep uploading.
  const [savedDoc, setSavedDoc] = useState<{ id: string; title: string; linked?: string[] } | null>(null);
  // "This looks like the invoice for PO-2026-041" — existing documents this one
  // belongs with (POST /link-preview); ticked ones are linked on save.
  type LinkCandidate = { document_id: string; document_type: string; number: string; supplier_name: string;
    amount: number; currency: string; score: number; reasons: string[]; strong: boolean };
  const [linkCands, setLinkCands] = useState<LinkCandidate[]>([]);
  const [linkPicks, setLinkPicks] = useState<Set<string>>(new Set());
  const [uploadCount, setUploadCount] = useState(0);

  // Local object-URL for a visual thumbnail of the picked file (image or PDF),
  // shown before the user starts extraction. Revoked when the file changes.
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const isPdf = selectedFile?.type.includes("pdf") ?? false;
  // Full-screen file preview so the user can actually zoom/scroll the picked
  // document before extracting (the inline thumbnail is a small, static glance).
  const [showFilePreview, setShowFilePreview] = useState(false);
  const [previewZoom, setPreviewZoom] = useState(1);
  useEffect(() => {
    if (!selectedFile) { setPreviewUrl(null); return; }
    const url = URL.createObjectURL(selectedFile);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [selectedFile]);
  const [sessionId, setSessionId] = useState("");
  const [showDuplicateWarning, setShowDuplicateWarning] = useState(false);
  const [duplicateMessage, setDuplicateMessage] = useState("");
  const [existingDocumentId, setExistingDocumentId] = useState("");
  const [showAmountMismatch, setShowAmountMismatch] = useState(false);
  const [showCamera, setShowCamera] = useState(false);
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);
  // True when the preview below was recovered from a reload rather than just
  // extracted, so we can explain where it came from.
  const [draftRestored, setDraftRestored] = useState(false);

  useEffect(() => { setLang(getStoredLanguage()); }, []);

  // ── Surviving a reload ───────────────────────────────────────────────────
  // Extraction is expensive (remote OCR + LLM) and the result lives only in
  // this component's state, so a stray refresh used to throw it away and force
  // a full re-upload. The backend keeps the session for SESSION_TTL_SECS (2h
  // by default), and /confirm-save only needs {session_id, edited_preview} —
  // both of which survive in sessionStorage. So we can restore the whole
  // review step after a reload without re-running the pipeline. The picked
  // File itself can't be serialized, but nothing past extraction needs it.
  useEffect(() => {
    let raw: string | null = null;
    try { raw = sessionStorage.getItem(DRAFT_KEY); } catch { return; }
    if (!raw) return;
    try {
      const draft = JSON.parse(raw) as UploadDraft;
      if (!draft?.sessionId || !draft?.preview) throw new Error("incomplete draft");
      setSessionId(draft.sessionId);
      // Re-derive the total so a draft saved by an older build shows items + tax.
      setPreview(recalculate(draft.preview));
      setDraftRestored(true);
    } catch {
      try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* private mode */ }
    }
    // Mount-only restore; recalculate is a pure helper.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!preview || !sessionId) return;
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ sessionId, preview }));
    } catch { /* quota / private mode — reload recovery is best-effort */ }
  }, [preview, sessionId]);

  // Extraction in flight can't be recovered at all (the SSE stream dies with
  // the page), and an un-saved preview would at least cost the user a re-review
  // — so warn before either is lost. Browsers show their own wording here.
  const hasUnsavedWork = isProcessing || (preview !== null && !savedDoc);
  useEffect(() => {
    if (!hasUnsavedWork) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [hasUnsavedWork]);

  const t = ui[lang];

  // Bring the freshly-extracted preview into view by itself. This is what the
  // old "Extraction Done" button was wired to do on click, except the user had
  // to press it and it was usually already on screen. A restored draft is
  // skipped — the banner above it explains itself, and yanking the viewport on
  // load is worse than leaving the page where it opened.
  const autoScrolledToPreview = useRef(false);
  useEffect(() => {
    if (!preview) { autoScrolledToPreview.current = false; return; }
    if (autoScrolledToPreview.current || draftRestored) return;
    autoScrolledToPreview.current = true;
    previewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [preview, draftRestored]);

  const parseAmt = (v: string | number) => {
    const n = Number(String(v ?? "").replace(/,/g, "").replace(/Rs\.?/gi, "").trim());
    return Number.isFinite(n) ? n : 0;
  };

  // A blank / NULL quantity means one unit (e.g. a handwritten receipt line
  // "Glue ... 110"); an explicit 0 stays 0.
  const qtyOf = (v: string | number) => {
    const s = String(v ?? "").trim();
    return s === "" || s.toUpperCase() === "NULL" ? 1 : parseAmt(s);
  };

  // Final total is never typed in: it is always sum(qty x unit price) - discount
  // + tax. A tax rate (%) wins over a typed tax amount and recomputes it from the
  // discounted subtotal. When the prices already include the VAT (tax_inclusive,
  // set by the backend) it isn't added again. DNs carry no amounts and are left
  // as extracted.
  const recalculate = (p: PreviewData): PreviewData => {
    if (String(p.document_type || "").toLowerCase() === "dn") return p;
    const items = (p.items || []).map((item) => {
      const u = parseAmt(item.unit_price);
      return { ...item, line_total: u > 0 ? +(qtyOf(item.quantity) * u).toFixed(2) : item.line_total };
    });
    const subtotal = items.reduce((s, i) => s + parseAmt(i.line_total), 0);
    if (subtotal <= 0) return { ...p, items };  // no priced items: keep extracted total
    const taxable = subtotal - Math.max(0, parseAmt(p.discount_amount ?? 0));
    if (p.tax_inclusive) {
      const inclusiveTotal = +taxable.toFixed(2);
      return { ...p, items, final_total_amount: inclusiveTotal, payable_amount: inclusiveTotal };
    }
    const rate = parseAmt(p.tax_rate ?? 0);
    const taxAmount = rate > 0 ? +(taxable * rate / 100).toFixed(2) : Math.max(0, parseAmt(p.tax_amount ?? 0));
    const total = +(taxable + taxAmount).toFixed(2);
    return {
      ...p, items,
      tax_amount: rate > 0 ? taxAmount : p.tax_amount,
      final_total_amount: total, payable_amount: total,
    };
  };

  // Shared by the image/PDF inputs and drag-and-drop — all do the same reset-and-select.
  const selectFile = (f: File | null) => {
    setSelectedFile(f); setPreview(null); setError("");
    setSavedDoc(null); setSessionId(""); setShowDuplicateWarning(false);
    setShowAmountMismatch(false); clearDraft();
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    selectFile(e.target.files?.[0] || null);
    // Reset the input's value so picking the same file again still fires onChange.
    e.target.value = "";
  };

  const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp", "application/pdf"];
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (isProcessing) return;
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    if (!ACCEPTED_TYPES.includes(f.type)) {
      setError(lang === "si" ? "PDF, JPG හෝ PNG ගොනුවක් පමණක් දමන්න." : "Please drop a PDF, JPG or PNG file.");
      return;
    }
    selectFile(f);
  };

  const clearDraft = () => {
    try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* private mode */ }
    setDraftRestored(false);
  };

  const resetForm = () => {
    setPreview(null); setSelectedFile(null); setSessionId("");
    setShowDuplicateWarning(false); setDuplicateMessage(""); setExistingDocumentId("");
    setShowAmountMismatch(false); setError(""); setActiveStep(0);
    clearDraft();
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // ── Camera helpers ───────────────────────────────────────────────────────
  const openCamera = async () => {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
      });
      setCameraStream(stream);
      setShowCamera(true);
      // Attach stream to video element after the modal renders
      setTimeout(() => {
        if (videoRef.current) { videoRef.current.srcObject = stream; videoRef.current.play(); }
      }, 100);
    } catch {
      setError("Camera access denied. Please allow camera permission and try again.");
    }
  };

  const closeCamera = () => {
    cameraStream?.getTracks().forEach(t => t.stop());
    setCameraStream(null);
    setShowCamera(false);
  };

  const capturePhoto = () => {
    if (!videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width  = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    canvas.toBlob(blob => {
      if (!blob) return;
      const file = new File([blob], `photo_${Date.now()}.jpg`, { type: "image/jpeg" });
      setSelectedFile(file);
      setPreview(null); setError(""); setSessionId(""); clearDraft();
      closeCamera();
    }, "image/jpeg", 0.92);
  };

  const handleProcess = async () => {
    if (!selectedFile) return;
    const token = getAuthToken();
    if (!token) { router.push("/login"); return; }

    setIsProcessing(true); setError(""); setPreview(null); clearDraft();
    setActiveStep(1);

    addNotification({
      title: lang === "si" ? "ලේඛනය සකසමින් ඇත" : "Processing Document",
      message: lang === "si"
        ? `${selectedFile.name} — OCR සහ ක්ෂේත්‍ර ලබාගැනීම ආරම්භ විය.`
        : `${selectedFile.name} — OCR extraction started.`,
      type: "info",
    });

    try {
      const fd = new FormData();
      fd.append("file", selectedFile);
      const res = await fetch(`${BACKEND_URL}/process-document-stream`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });

      if (res.status === 401) { localStorage.removeItem("token"); router.push("/login"); return; }
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { message?: string };
        throw new Error(err.message || `Server error ${res.status}`);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let event: StreamEvent;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }

          if (event.stage === "error") throw new Error(event.message || "Processing failed.");
          if (typeof event.step === "number") setActiveStep(event.step);
          if (event.stage === "done") {
            // Auto-fill company_name from user profile so it's always correct.
            // Final total = items (qty x unit price) + tax (DNs left as extracted).
            const extracted = event.preview ?? null;
            const rawPreview = extracted ? recalculate(extracted) : extracted;
            if (rawPreview) {
              getSession().then(s => {
                if (s?.companyName) {
                  setPreview({ ...rawPreview, company_name: s.companyName });
                } else {
                  setPreview(rawPreview);
                }
              });
            } else {
              setPreview(rawPreview);
            }
            setSessionId(event.session_id ?? "");
          }
        }
      }
    } catch (err) {
      setError(friendlyError(err instanceof Error ? err.message : ""));
      setActiveStep(0);
    } finally {
      setIsProcessing(false);
    }
  };

  const previewKey = preview
    ? JSON.stringify([preview.document_type, preview.supplier_name, preview.order_id, preview.final_total_amount,
        preview.date, (preview.items || []).length])
    : "";
  useEffect(() => {
    if (!preview) { setLinkCands([]); setLinkPicks(new Set()); return; }
    const timer = window.setTimeout(() => {
      fetch(`${BACKEND_URL}/link-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getAuthToken()}` },
        body: JSON.stringify({ preview }),
      })
        .then((r) => r.json())
        .then((d) => {
          if (!d.success) return;
          const cands: LinkCandidate[] = d.candidates || [];
          setLinkCands(cands);
          setLinkPicks(new Set(cands.filter((c) => c.strong).map((c) => c.document_id)));
        })
        .catch(() => {});
    }, 600);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);

  const handleSave = async (force = false) => {
    if (!preview || !sessionId) return;
    const token = getAuthToken();
    if (!token) { router.push("/login"); return; }

    // Block the save (with a confirmation) when the numbers don't reconcile —
    // either the raw OCR total differs from the final, or the line items don't
    // add up to the final total. The user can still save anyway (e.g. when tax
    // or a discount explains the gap), but not by accident.
    // The final total is derived, so items + tax always add up; the only check
    // left is whether that sum matches the total OCR read off the paper.
    const toSave = recalculate(preview);
    if (!force) {
      const finalT = parseAmt(toSave.final_total_amount);
      // Only when OCR actually read a raw total (NULL / blank isn't a mismatch).
      const rawT = parseAmt(toSave.raw_total_amount);
      // The raw total may be the printed subtotal, before tax and discount.
      const subtotal = (toSave.items || []).reduce((s, i) => s + parseAmt(i.line_total), 0);
      const rawDiffers = rawT > 0 && finalT > 0 && Math.abs(rawT - finalT) > 0.01 && Math.abs(rawT - subtotal) > 0.01;
      if (rawDiffers) { setShowAmountMismatch(true); return; }
    }

    setIsSaving(true); setError("");
    const controller = new AbortController();
    const saveTimeout = setTimeout(() => controller.abort(), 60_000);
    try {
      const res = await fetch(`${BACKEND_URL}/confirm-save`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sessionId, edited_preview: toSave, force_save: force,
          link_targets: Array.from(linkPicks) }),
        signal: controller.signal,
      });
      if (res.status === 401) { localStorage.removeItem("token"); router.push("/login"); return; }
      // The backend evicts sessions after SESSION_TTL_SECS. A draft restored
      // from a reload can outlive that, so say plainly that it's gone rather
      // than leaving a Save button that will never work.
      if (res.status === 404) {
        // resetForm() clears `error`, so tear down the dead draft first and
        // only then say why it's gone.
        resetForm();
        setError(t.draftExpired);
        return;
      }
      const data = await res.json();

      if (data.duplicate_found && !data.success) {
        setShowDuplicateWarning(true);
        setDuplicateMessage(data.message || "Document already exists.");
        setExistingDocumentId(data.existing_document_id || "NULL");
        return;
      }

      if (!res.ok || !data.success) throw new Error(data.message || "Save failed.");

      const docId = data.document_id ?? "";
      const savedTitle = otherPartyName({ supplier_name: preview.supplier_name }) || docId;

      // Fire notification so the bell icon lights up
      addNotification({
        title: lang === "si" ? "ලේඛනය සාර්ථකව සුරකිනු ලැබිණි" : "Document Saved",
        message: lang === "si"
          ? `${docId} — OCR සහ ක්ෂේත්‍ර ලබාගැනීම සම්පූර්ණ විය. ලේඛනය ගබඩාවට එකතු කෙරිණි.`
          : `${docId} — OCR extraction complete. Document added to your repository.`,
        type: "success",
      });

      const linkedNumbers = linkCands.filter((c) => linkPicks.has(c.document_id)).map((c) => c.number || c.document_id);
      resetForm();
      setSavedDoc({ id: docId, title: savedTitle, linked: linkedNumbers });
      setUploadCount((c) => c + 1);
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        setError("Save timed out after 60 s. Please check your connection and try again.");
      } else {
        setError(err instanceof Error ? err.message : "Something went wrong while saving.");
      }
    } finally {
      clearTimeout(saveTimeout);
      setIsSaving(false);
    }
  };

  // Jargon-free progress copy shown next to the spinner. Driven by the backend's
  // real step number, but worded for SME owners (no "OCR" / "extraction" / "LLM").
  const PROGRESS_COPY: Record<number, { en: string; si: string }> = {
    1: { en: "Reading your document…",           si: "ඔබගේ ලේඛනය කියවමින්…" },
    2: { en: "Looking at the text…",             si: "පෙළ පරීක්ෂා කරමින්…" },
    3: { en: "Tidying up the details…",          si: "විස්තර පිළිවෙළට සකසමින්…" },
    4: { en: "Pulling out the key information…",  si: "වැදගත් තොරතුරු උකහා ගනිමින්…" },
  };
  const friendlyProgress =
    (PROGRESS_COPY[activeStep] ?? { en: "Getting started…", si: "ආරම්භ කරමින්…" })[
      lang === "si" ? "si" : "en"
    ];

  // Turn raw backend error text into plain language an SME owner understands.
  const friendlyError = (raw: string): string => {
    const r = (raw || "").toLowerCase();
    const si = lang === "si";
    if (r.includes("unsupported file") || r.includes("file type"))
      return si
        ? "මෙම ගොනු වර්ගය සහාය නොදක්වයි. PDF, JPG හෝ PNG එකක් උඩුගත කරන්න."
        : "That file type isn't supported. Please upload a PDF, JPG or PNG.";
    if (r.includes("ocr") || r.includes("empty text") || r.includes("no usable text") || r.includes("engine") || r.includes("no pages"))
      return si
        ? "අපට මෙම ලේඛනය කියවිය නොහැකි විය. පැහැදිලි ඡායාරූපයක් හෝ PDF එකක් උත්සාහ කරන්න."
        : "We couldn't read this document clearly. Try a sharper photo or a PDF.";
    if (r.includes("timed out") || r.includes("timeout"))
      return si
        ? "මෙය සැකසීමට සාමාන්‍යයට වඩා කාලයක් ගත විය. කරුණාකර නැවත උත්සාහ කරන්න."
        : "This took longer than expected. Please try again.";
    if (r.includes("401") || r.includes("unauthorized"))
      return si
        ? "ඔබගේ සැසිය කල් ඉකුත් විය. කරුණාකර නැවත පුරනය වන්න."
        : "Your session expired. Please log in again.";
    return si
      ? "සැකසීමේදී යම් දෝෂයක් ඇති විය. කරුණාකර නැවත උත්සාහ කරන්න."
      : "Something went wrong while processing. Please try again.";
  };

  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <TopBar title={t.uploadTitle} />

        <main className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <div className="rise-in mb-6 flex flex-wrap items-end justify-between gap-4">
            <div className="min-w-0 max-w-3xl">
              <h1 className="text-[24px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[30px]">
                {t.uploadTitle}
              </h1>
              <p className="mt-1.5 text-[13px] leading-6 text-[var(--text-2)] sm:text-[14px]">{t.uploadSubtitle}</p>
            </div>
            <button
              onClick={() => router.push("/manual-entry")}
              className="flex items-center gap-1.5 rounded-xl px-4 py-2 text-[13px] font-semibold transition hover:opacity-80"
              style={{ background: "rgba(234,108,10,0.1)", color: "#ea6c0a", border: "1px solid rgba(234,108,10,0.2)" }}
            >
              <span className="material-symbols-outlined text-[17px]">edit_note</span>
              {lang === "si" ? "අතින් ඇතුළත් කරන්න" : "Manual Entry"}
            </button>
          </div>

          {/* Two inputs rather than one, each scoped to a single kind: on mobile a
              mixed image+PDF picker is confusing and often jumps straight to the
              camera. Separate accepts give the OS an unambiguous target — the
              gallery for images, the document browser for PDFs. */}
          <input
            ref={fileInputRef} type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={handleFileInputChange}
          />
          <input
            ref={pdfInputRef} type="file"
            accept="application/pdf"
            className="hidden"
            onChange={handleFileInputChange}
          />

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px] 2xl:grid-cols-[minmax(0,1fr)_400px]">
          <div className="min-w-0">

          {/* Drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); if (!isProcessing) setIsDragging(true); }}
            onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragging(false); }}
            onDrop={handleDrop}
            className="relative overflow-hidden rounded-3xl px-6 py-10 text-center transition-all sm:py-14"
            style={{
              border: `2px dashed ${isDragging ? "var(--brand-mid)" : "color-mix(in srgb, var(--brand-mid) 45%, var(--border))"}`,
              background: isDragging
                ? "var(--brand-tint)"
                : "radial-gradient(ellipse at top, var(--brand-tint) 0%, var(--surface) 65%)",
              opacity: selectedFile && !isDragging ? 0.9 : 1,
              transform: isDragging ? "scale(1.005)" : undefined,
              boxShadow: "var(--card-shadow)",
            }}
          >
            <div
              className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl shadow-sm sm:h-20 sm:w-20"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
            >
              <span className="material-symbols-outlined" style={{ color: "var(--brand-mid)", fontSize: 36 }}>
                {selectedFile
                  ? selectedFile.type.includes("pdf") ? "picture_as_pdf" : "image"
                  : "upload_file"}
              </span>
            </div>
            <h2 className="mt-5 break-words text-[18px] font-extrabold text-[var(--text-1)] sm:text-[20px]">
              {isDragging ? (lang === "si" ? "මෙහි අතහරින්න" : "Release to upload") : selectedFile ? selectedFile.name : t.dragDrop}
            </h2>
            <p className="mt-1 text-[13px] text-[var(--text-2)]">
              {selectedFile
                ? `${(selectedFile.size / 1024).toFixed(0)} KB · Ready for OCR`
                : t.maxFileSize}
            </p>
            <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center gap-2 rounded-xl px-5 py-2.5 text-[13px] font-semibold transition hover:opacity-80"
                style={{ background: "var(--brand-tint)", color: "var(--brand-mid)" }}
              >
                <span className="material-symbols-outlined text-[17px]">image</span>
                {t.chooseImage}
              </button>
              <button
                onClick={() => pdfInputRef.current?.click()}
                className="flex items-center gap-2 rounded-xl px-5 py-2.5 text-[13px] font-semibold transition hover:opacity-80"
                style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}
              >
                <span className="material-symbols-outlined text-[17px]">picture_as_pdf</span>
                {t.choosePdf}
              </button>
              <button
                onClick={openCamera}
                className="flex items-center gap-2 rounded-xl px-5 py-2.5 text-[13px] font-semibold transition hover:opacity-80"
                style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}
              >
                <span className="material-symbols-outlined text-[17px]">photo_camera</span>
                {t.useCamera}
              </button>
            </div>
          </div>

          {selectedFile && (
            <div className="mt-4 rounded-2xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
              {/* Visual thumbnail — see the document before extracting */}
              <div
                className="relative mb-3 flex items-center justify-center overflow-hidden rounded-xl"
                style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
              >
                {previewUrl && (
                  <button
                    onClick={() => { setPreviewZoom(1); setShowFilePreview(true); }}
                    title={t.viewFull}
                    aria-label={t.viewFull}
                    className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] font-semibold shadow transition hover:opacity-90"
                    style={{ background: "var(--surface)", color: "var(--brand-mid)" }}
                  >
                    <span className="material-symbols-outlined text-[16px]" aria-hidden="true">fullscreen</span>
                    {t.viewFull}
                  </button>
                )}
                {previewUrl && !isPdf ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={previewUrl}
                    alt={selectedFile.name}
                    className="max-h-[260px] w-full object-contain"
                  />
                ) : previewUrl && isPdf ? (
                  <object
                    data={`${previewUrl}#toolbar=0&navpanes=0&scrollbar=0&view=FitH`}
                    type="application/pdf"
                    className="pointer-events-none h-[260px] w-full"
                  >
                    <div className="flex h-[260px] w-full items-center justify-center">
                      <span className="material-symbols-outlined text-[40px]" style={{ color: "var(--brand-mid)" }}>
                        picture_as_pdf
                      </span>
                    </div>
                  </object>
                ) : (
                  <div className="flex h-[140px] w-full items-center justify-center">
                    <span className="material-symbols-outlined text-[40px]" style={{ color: "var(--brand-mid)" }}>
                      {isPdf ? "picture_as_pdf" : "image"}
                    </span>
                  </div>
                )}
                {/* Scanner sweep over the document while it's being read */}
                {isProcessing && (
                  <div className="scan-overlay" aria-hidden="true">
                    <div className="scan-line" />
                  </div>
                )}
              </div>

              <div className="flex items-center gap-4">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl"
                  style={{ background: "var(--brand-tint)", color: "var(--brand-mid)" }}>
                  <span className="material-symbols-outlined text-[22px]">
                    {isPdf ? "picture_as_pdf" : "image"}
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[15px] font-semibold text-[var(--text-1)]">{selectedFile.name}</p>
                  <p className="text-[12px] text-[var(--text-3)]">
                    {(selectedFile.size / 1024).toFixed(0)} KB · OCR ready
                  </p>
                </div>
                <button onClick={resetForm} className="text-[var(--text-3)] transition hover:text-red-500">
                  <span className="material-symbols-outlined text-[22px]">close</span>
                </button>
              </div>
            </div>
          )}

          {/* Processing indicator — step-by-step checklist driven by the backend's real step */}
          {isProcessing && <ScanProgress activeStep={activeStep} lang={lang} />}

          {error && (
            <div className="mt-5 rounded-xl px-4 py-3 text-[13px] text-red-600"
              style={{ background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.2)" }}>
              {error}
            </div>
          )}

          {/* Post-save "what next?" card */}
          {savedDoc && (
            <div
              className="mt-6 rounded-2xl p-6 text-center"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
            >
              <div
                className="mx-auto flex h-12 w-12 items-center justify-center rounded-full"
                style={{ background: "rgba(22,163,74,0.12)" }}
              >
                <span className="material-symbols-outlined text-[26px]" style={{ color: "#16a34a" }}>
                  check_circle
                </span>
              </div>
              <p className="mt-3 text-[17px] font-extrabold text-[var(--text-1)]">
                {lang === "si" ? "ලේඛනය සුරැකිණි" : "Document saved"}
              </p>
              <p className="mt-1 text-[13px]">
                <span className="font-semibold text-[var(--text-1)]">{savedDoc.title}</span>
                <span className="text-[var(--text-3)]"> · {savedDoc.id}</span>
              </p>
              {savedDoc.linked && savedDoc.linked.length > 0 && (
                <p className="mt-2 inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12px] font-semibold"
                  style={{ background: "var(--brand-tint)", color: "var(--brand-mid)" }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 15 }}>link</span>
                  {lang === "si" ? "සම්බන්ධ කළා: " : "Linked to "}{savedDoc.linked.join(", ")}
                </p>
              )}

              <div className="mt-5 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
                {/* Scope the chat to the document just saved (same contract as
                    the repository's "Ask about this document"), so it opens with
                    the thumbnail and context instead of a blank thread. */}
                <button
                  onClick={() => router.push(`/query?doc=${encodeURIComponent(savedDoc.id)}`)}
                  className="flex items-center justify-center gap-2 rounded-2xl px-5 py-3 text-[14px] font-bold text-white transition hover:opacity-90"
                  style={{ background: "var(--brand)" }}
                >
                  <span className="material-symbols-outlined text-[18px]" aria-hidden="true">chat</span>
                  {lang === "si" ? "මෙම ලේඛනය ගැන අසන්න" : "Ask about this document"}
                </button>
                <button
                  onClick={() => router.push(`/analysis/${savedDoc.id}`)}
                  className="flex items-center justify-center gap-2 rounded-2xl px-5 py-3 text-[14px] font-bold transition hover:opacity-80"
                  style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-1)" }}
                >
                  <span className="material-symbols-outlined text-[18px]">visibility</span>
                  {lang === "si" ? "ලේඛනය බලන්න" : "View document"}
                </button>
                <button
                  onClick={() => setSavedDoc(null)}
                  className="flex items-center justify-center gap-2 rounded-2xl px-5 py-3 text-[14px] font-bold transition hover:opacity-80"
                  style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}
                >
                  <span className="material-symbols-outlined text-[18px]">add</span>
                  {lang === "si" ? "තවත් උඩුගත කරන්න" : "Upload another"}
                </button>
              </div>

              {uploadCount > 1 && (
                <p className="mt-4 text-[12px] text-[var(--text-3)]">
                  {lang === "si"
                    ? `මෙම සැසියේදී ලේඛන ${uploadCount}ක් සුරැකිණි`
                    : `${uploadCount} documents saved this session`}
                </p>
              )}
            </div>
          )}

          {/* Before extraction this is the primary action. Once a preview
              exists the only thing left to do is Confirm & Save at the foot of
              that preview, so this becomes a plain status chip — as a button it
              just scrolled to a preview already on screen, which read as
              broken, and it competed with the real green CTA below. */}
          {!savedDoc && !preview && (
            <button
              onClick={handleProcess}
              disabled={!selectedFile || isProcessing}
              className="mt-6 flex w-full items-center justify-center gap-2 rounded-2xl py-4 text-[15px] font-bold text-white shadow-md transition hover:-translate-y-px hover:shadow-lg disabled:translate-y-0 disabled:opacity-50 disabled:shadow-none"
              style={{ background: "linear-gradient(125deg, var(--hero-from) 0%, var(--hero-to) 100%)" }}
            >
              <span className="material-symbols-outlined text-[20px]">{isProcessing ? "progress_activity" : "auto_awesome"}</span>
              {isProcessing ? friendlyProgress : t.beginExtraction}
            </button>
          )}

          {!savedDoc && preview && !draftRestored && (
            <div
              role="status"
              className="mt-6 flex items-center justify-center gap-2 rounded-2xl py-3.5 text-[14px] font-bold"
              style={{ background: "rgba(22,163,74,0.12)", color: "#16a34a" }}
            >
              <span className="material-symbols-outlined text-[18px]" aria-hidden="true">check_circle</span>
              {t.extractionDone}
            </div>
          )}

          {/* A restored draft needs explaining, not congratulating — the user
              reloaded and would otherwise wonder why a filled-in form is here. */}
          {!savedDoc && preview && draftRestored && (
            <div
              role="status"
              className="mt-6 rounded-2xl p-4"
              style={{ background: "var(--brand-tint)", border: "1px solid var(--border)" }}
            >
              <div className="flex items-start gap-3">
                <span className="material-symbols-outlined text-[20px] text-[var(--brand-mid)]" aria-hidden="true">
                  restore
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[14px] font-bold text-[var(--text-1)]">{t.draftRestoredTitle}</p>
                  <p className="mt-1 text-[13px] leading-5 text-[var(--text-2)]">{t.draftRestoredBody}</p>
                </div>
                <button
                  onClick={resetForm}
                  className="shrink-0 rounded-xl px-3 py-1.5 text-[12px] font-semibold transition hover:opacity-80"
                  style={{ border: "1px solid var(--border)", color: "var(--text-2)" }}
                >
                  {t.draftDiscard}
                </button>
              </div>
            </div>
          )}

          {/* Preview */}
          {preview && (
            <div ref={previewRef} className="ui-card mt-8 p-5 sm:p-6">
              <h2 className="text-[19px] font-extrabold text-[var(--text-1)]">{t.extractedPreview}</h2>
              <p className="mt-1 text-[13px] text-[var(--text-2)]">{t.reviewBeforeSaving}</p>

              {preview.document_type !== "dn" && (
                <MathCheck
                  key={sessionId}
                  items={preview.items || []}
                  printedTotal={preview.raw_total_amount}
                  currency={preview.currency}
                  lang={lang}
                />
              )}

              <div className="mt-5 grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                {getFieldRows(String(preview.document_type || ""), String(preview.flow_type || "")).map((row) => {
                  const rawVal = String((preview as Record<string, unknown>)[row.key] ?? "");
                  const val = (row.key === "tax_rate" || row.key === "tax_amount") && rawVal.toUpperCase() === "NULL" ? "" : rawVal;
                  return (
                    <div key={row.key}>
                      <p className="mb-1.5 text-[12px] font-semibold text-[var(--text-2)]">{row.label}</p>
                      {row.opts ? (
                        <select
                          value={val}
                          onChange={(e) => {
                            const next = { ...preview, [row.key]: e.target.value };
                            // Auto-set flow_type when document_type changes
                            if (row.key === "document_type") {
                              if (e.target.value === "dn") next.flow_type = "expense";
                              if (e.target.value === "po") next.flow_type = "payable";
                            }
                            // Auto-set paid/received status when flow_type changes
                            if (row.key === "flow_type") {
                              const ft = e.target.value;
                              if (ft === "cash_outflow") { next.received_status = "NULL";         next.paid_status = "paid"; }
                              if (ft === "cash_inflow")  { next.received_status = "received";     next.paid_status = "NULL"; }
                              if (ft === "payable")      { next.received_status = "NULL";         next.paid_status = "not_paid"; }
                              if (ft === "receivable")   { next.received_status = "not_received"; next.paid_status = "NULL"; }
                            }
                            setPreview(next);
                          }}
                          className="field-input w-full rounded-xl border px-4 py-2.5 text-[14px] transition"
                        >
                          {row.opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                      ) : (
                        <input
                          value={val}
                          onChange={(e) => {
                            if (row.readonly) return;
                            const v = e.target.value;
                            if (row.key === "tax_rate") {
                              // Rate drives the tax amount; total follows.
                              setPreview(recalculate({ ...preview, tax_rate: v, tax_inclusive: false }));
                            } else if (row.key === "tax_amount") {
                              // A typed tax amount replaces any rate.
                              setPreview(recalculate({ ...preview, tax_amount: v, tax_rate: "", tax_inclusive: false }));
                            } else if (row.key === "discount_amount") {
                              setPreview(recalculate({ ...preview, discount_amount: v }));
                            } else {
                              setPreview({ ...preview, [row.key]: v });
                            }
                          }}
                          readOnly={row.readonly}
                          className="field-input w-full rounded-xl border px-4 py-2.5 text-[14px] transition"
                          style={row.readonly ? { background: "var(--input-bg-ro)", cursor: "not-allowed" } : {}}
                        />
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Items */}
              {preview.items && preview.items.length > 0 && (
                <div className="mt-6">
                  <p className="mb-3 text-[11px] font-bold uppercase tracking-[0.08em] text-[var(--text-3)]">{t.itemsLabel}</p>
                  <div className="space-y-3">
                    {preview.items.map((item, idx) => (
                      <div key={idx} className="grid gap-3 rounded-xl p-4 sm:grid-cols-3"
                        style={{ border: "1px solid var(--border)", background: "var(--surface-2)" }}>
                        {(getItemFields(String(preview.document_type || "")) as (keyof PreviewItem)[]).map((f) => (
                          <input
                            key={f}
                            value={String(item[f] ?? "")}
                            onChange={(e) => {
                              const items = [...preview.items];
                              items[idx] = { ...items[idx], [f]: e.target.value };
                              const next = { ...preview, items };
                              setPreview(f === "quantity" || f === "unit_price" ? recalculate(next) : next);
                            }}
                            placeholder={f === "quantity" ? "qty" : f.replace("_", " ")}
                            title={f === "quantity" ? "Quantity" : f === "unit_price" ? "Unit price" : "Description"}
                            className="field-input rounded-xl border px-3 py-2 text-[13px] transition"
                          />
                        ))}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Belongs with: PO / delivery note / invoice this document is part of */}
              {linkCands.length > 0 && (
                <div className="mt-6 rounded-2xl p-4" style={{ background: "var(--brand-tint)", border: "1px dashed var(--brand-mid)" }}>
                  <p className="flex items-center gap-2 text-[13px] font-bold text-[var(--text-1)]">
                    <span className="material-symbols-outlined" style={{ fontSize: 18, color: "var(--brand-mid)" }}>account_tree</span>
                    {lang === "si" ? "මෙම ලේඛනය අයත් විය හැකි ඇණවුම" : "This document seems to belong with"}
                  </p>
                  <div className="mt-3 space-y-2">
                    {linkCands.map((c) => {
                      const TYPE: Record<string, [string, string]> = {
                        po: ["purchase order", "මිලදී ගැනීමේ ඇණවුම"], dn: ["delivery note", "බෙදාහැරීමේ සටහන"],
                        invoice: ["invoice", "ඉන්වොයිසිය"], receipt: ["receipt", "රිසිට්පත"],
                      };
                      const mine = String(preview.document_type || "").toLowerCase();
                      const t = TYPE[c.document_type] ?? ["document", "ලේඛනය"];
                      const me = TYPE[mine] ?? ["document", "ලේඛනය"];
                      const checked = linkPicks.has(c.document_id);
                      return (
                        <label key={c.document_id} className="flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2.5 transition"
                          style={{ background: "var(--surface)", border: `1px solid ${checked ? "var(--brand-mid)" : "var(--border)"}` }}>
                          <input type="checkbox" checked={checked} className="mt-1 h-4 w-4 accent-[var(--brand-mid)]"
                            onChange={() => setLinkPicks((prev) => {
                              const next = new Set(prev);
                              if (next.has(c.document_id)) next.delete(c.document_id); else next.add(c.document_id);
                              return next;
                            })} />
                          <span className="min-w-0 flex-1">
                            <span className="block text-[13px] font-semibold text-[var(--text-1)]">
                              {lang === "si"
                                ? `මෙය ${c.number || c.document_id} (${t[1]}) සඳහා වන ${me[1]} ලෙස පෙනේ`
                                : `This looks like the ${me[0]} for ${t[0]} ${c.number || c.document_id}`}
                            </span>
                            <span className="mt-0.5 block text-[11px] text-[var(--text-3)]">
                              {Math.round(c.score * 100)}% {lang === "si" ? "ගැළපීම" : "match"} · {c.reasons.join(" · ")}
                              {c.supplier_name ? ` · ${c.supplier_name}` : ""}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                  <p className="mt-2 text-[11px] text-[var(--text-3)]">
                    {lang === "si" ? "සලකුණු කළ ඒවා සුරැකීමේදී සම්බන්ධ කෙරේ." : "Ticked documents are linked when you save."}
                  </p>
                </div>
              )}

              {/* Totals-don't-reconcile warning */}
              {showAmountMismatch && (() => {
                const cur = preview.currency && preview.currency !== "NULL" ? `${preview.currency} ` : "";
                const subtotal = parseAmt(preview.raw_total_amount);  // total printed on the paper
                const finalT = parseAmt(recalculate(preview).final_total_amount);
                return (
                  <div className="mt-6 rounded-xl p-4 text-[13px]"
                    style={{ background: "var(--warn-tint)", border: "1px solid var(--warn-border)", color: "var(--warn)" }}>
                    <p className="font-semibold">{t.totalsMismatchTitle}</p>
                    <p className="mt-1">
                      {t.totalsMismatchItems}: <b>{cur}{subtotal.toFixed(2)}</b> · {t.totalsMismatchFinal}: <b>{cur}{finalT.toFixed(2)}</b>
                    </p>
                    <p className="mt-1">{t.totalsMismatchHint}</p>
                    <div className="mt-3 flex gap-3">
                      <button onClick={() => { setShowAmountMismatch(false); handleSave(true); }}
                        disabled={isSaving}
                        className="rounded-xl px-4 py-2 text-[13px] font-bold text-white transition hover:opacity-90"
                        style={{ background: "var(--warn)" }}>{t.saveAnyway}</button>
                      <button onClick={() => setShowAmountMismatch(false)}
                        className="rounded-xl px-4 py-2 text-[13px] font-semibold transition hover:opacity-80"
                        style={{ border: "1px solid var(--warn-border)", color: "var(--warn)" }}>{t.cancel}</button>
                    </div>
                  </div>
                );
              })()}

              {/* Duplicate warning */}
              {showDuplicateWarning && (
                <div className="mt-6 rounded-xl p-4 text-[13px]"
                  style={{ background: "rgba(217,119,6,0.08)", border: "1px solid rgba(217,119,6,0.3)", color: "#92400e" }}>
                  <p className="font-semibold">{duplicateMessage}</p>
                  <p className="mt-1">Existing ID: {existingDocumentId}</p>
                  <div className="mt-3 flex gap-3">
                    <button onClick={() => handleSave(true)} disabled={isSaving}
                      className="rounded-xl px-4 py-2 text-[13px] font-bold text-white" style={{ background: "#d97706" }}>
                      Save Anyway
                    </button>
                    <button onClick={() => { setShowDuplicateWarning(false); setDuplicateMessage(""); }}
                      className="rounded-xl px-4 py-2 text-[13px] font-semibold"
                      style={{ border: "1px solid rgba(217,119,6,0.4)", color: "#92400e" }}>{t.cancel}</button>
                  </div>
                </div>
              )}

              <button
                onClick={() => handleSave(false)}
                disabled={isSaving}
                className="mt-6 w-full rounded-2xl py-4 text-[15px] font-bold text-white transition hover:opacity-90 disabled:opacity-60"
                style={{ background: "#16a34a" }}
              >
                {isSaving ? t.saving : t.confirmAndSave}
              </button>
            </div>
          )}
          </div>

          {/* ── Guide column ──────────────────────────────────────── */}
          <aside className="grid content-start gap-4 md:grid-cols-2 xl:grid-cols-1">
            {/* Live progress */}
            <div className="ui-card p-5">
              <p className="text-[11px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)]">
                {lang === "si" ? "එය ක්‍රියා කරන ආකාරය" : "How it works"}
              </p>
              <ol className="mt-4 space-y-1">
                {[
                  { icon: "upload_file", en: "Add your document", si: "ලේඛනය එක් කරන්න",
                    den: "Photo, scan or PDF — English or Sinhala", dsi: "ඡායාරූපය, ස්කෑන් හෝ PDF",
                    done: !!selectedFile || !!preview || !!savedDoc, active: !selectedFile && !preview && !savedDoc },
                  { icon: "document_scanner", en: "AI reads it", si: "AI එය කියවයි",
                    den: "OCR + extraction of parties, items and totals", dsi: "පාර්ශ්ව, අයිතම සහ එකතුව උකහා ගැනීම",
                    done: !!preview || !!savedDoc, active: isProcessing },
                  { icon: "fact_check", en: "Review & save", si: "සමාලෝචනය කර සුරකින්න",
                    den: "Check the fields, then confirm", dsi: "ක්ෂේත්‍ර පරීක්ෂා කර තහවුරු කරන්න",
                    done: !!savedDoc, active: !!preview && !savedDoc },
                ].map((step, i, arr) => (
                  <li key={step.icon} className="relative flex gap-3 pb-4 last:pb-0">
                    {i < arr.length - 1 && (
                      <span className="absolute left-[17px] top-10 h-[calc(100%-2.75rem)] w-[2px] rounded-full"
                        style={{ background: step.done ? "#16a34a" : "var(--border)" }} aria-hidden />
                    )}
                    <span
                      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${step.active ? "ring-4" : ""}`}
                      style={{
                        background: step.done ? "#16a34a" : step.active ? "var(--brand-mid)" : "var(--surface-2)",
                        color: step.done || step.active ? "#fff" : "var(--text-3)",
                        ["--tw-ring-color" as string]: "var(--brand-ring)",
                      }}
                    >
                      <span className={`material-symbols-outlined text-[18px] ${step.active && isProcessing && i === 1 ? "animate-pulse" : ""}`}>
                        {step.done ? "check" : step.icon}
                      </span>
                    </span>
                    <div className="min-w-0 pt-1">
                      <p className="text-[13px] font-bold" style={{ color: step.active ? "var(--brand-mid)" : "var(--text-1)" }}>
                        {lang === "si" ? step.si : step.en}
                      </p>
                      <p className="mt-0.5 text-[12px] leading-5 text-[var(--text-3)]">{lang === "si" ? step.dsi : step.den}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>

            {/* Supported types */}
            <div className="ui-card p-5">
              <p className="text-[11px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)]">
                {lang === "si" ? "සහාය දක්වන ලේඛන" : "Supported documents"}
              </p>
              <div className="mt-4 grid grid-cols-2 gap-2">
                {[
                  { icon: "description",    color: "var(--brand-mid)", en: "Invoices",        si: "ඉන්වොයිස්" },
                  { icon: "receipt_long",   color: "#16a34a",          en: "Receipts",        si: "රිසිට්" },
                  { icon: "shopping_cart",  color: "#7c3aed",          en: "Purchase Orders", si: "මිලදී ඇණවුම්" },
                  { icon: "local_shipping", color: "#ea6c0a",          en: "Delivery Notes",  si: "බෙදාහැරීම් සටහන්" },
                ].map((d) => (
                  <div key={d.icon} className="flex items-center gap-2 rounded-xl px-3 py-2.5"
                    style={{ background: `color-mix(in srgb, ${d.color} 9%, transparent)` }}>
                    <span className="material-symbols-outlined text-[18px]" style={{ color: d.color }}>{d.icon}</span>
                    <span className="truncate text-[12px] font-semibold text-[var(--text-1)]">{lang === "si" ? d.si : d.en}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Tips */}
            <div className="ui-card p-5">
              <p className="text-[11px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)]">
                {lang === "si" ? "හොඳම ප්‍රතිඵල සඳහා" : "Tips for best results"}
              </p>
              <ul className="mt-3 space-y-2.5">
                {[
                  { icon: "light_mode", en: "Shoot in good light, without shadows", si: "හොඳ ආලෝකයේ, සෙවනැලි නැතිව ගන්න" },
                  { icon: "crop_free",  en: "Keep the whole page inside the frame",  si: "මුළු පිටුවම රාමුව තුළ තබන්න" },
                  { icon: "straighten", en: "Hold the camera flat above the bill",   si: "කැමරාව බිල්පතට ඉහළින් සමතලව අල්ලන්න" },
                ].map((tip) => (
                  <li key={tip.icon} className="flex items-start gap-2.5 text-[12px] leading-5 text-[var(--text-2)]">
                    <span className="material-symbols-outlined mt-px text-[17px]" style={{ color: "var(--brand-mid)" }}>{tip.icon}</span>
                    {lang === "si" ? tip.si : tip.en}
                  </li>
                ))}
              </ul>
            </div>

            {/* Enterprise Security banner */}
            <div
              className="flex items-start gap-3 rounded-[1.25rem] px-4 py-4"
              style={{ background: "var(--brand-tint)", border: "1px solid var(--border)" }}
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl" style={{ background: "var(--surface)", color: "var(--brand-mid)" }}>
                <span className="material-symbols-outlined text-[19px]">shield_lock</span>
              </span>
              <p className="text-[12px] leading-5 text-[var(--text-2)]">
                <span className="block font-bold text-[var(--text-1)]">
                  {lang === "si" ? "ව්‍යාපාරික ආරක්ෂාව" : "Enterprise Security"}
                </span>
                {/* The translated string already opens with this label — don't print it twice. */}
                {t.securityBanner.replace(/^\s*(Enterprise Security|ව්‍යාපාරික ආරක්ෂාව)\s*:\s*/, "")}
              </p>
            </div>
          </aside>
          </div>
        </main>

        <BottomNav />

        {/* Hidden canvas for photo capture */}
        <canvas ref={canvasRef} className="hidden" />

        {/* Full-screen file preview (zoom/scroll before extracting) */}
        {showFilePreview && previewUrl && (
          <div className="fixed inset-0 z-[60] flex flex-col" style={{ background: "rgba(0,0,0,0.9)" }}>
            <div className="flex items-center justify-between gap-3 p-3">
              <span className="truncate text-[13px] font-semibold text-white/90">{selectedFile?.name}</span>
              <div className="flex items-center gap-2">
                {!isPdf && (
                  <>
                    <button
                      onClick={() => setPreviewZoom((z) => Math.max(+(z - 0.5).toFixed(2), 1))}
                      disabled={previewZoom <= 1}
                      aria-label="Zoom out"
                      className="flex h-9 w-9 items-center justify-center rounded-full bg-white/15 text-white transition hover:bg-white/25 disabled:opacity-40"
                    >
                      <span className="material-symbols-outlined text-[20px]">zoom_out</span>
                    </button>
                    <button
                      onClick={() => setPreviewZoom((z) => Math.min(+(z + 0.5).toFixed(2), 5))}
                      disabled={previewZoom >= 5}
                      aria-label="Zoom in"
                      className="flex h-9 w-9 items-center justify-center rounded-full bg-white/15 text-white transition hover:bg-white/25 disabled:opacity-40"
                    >
                      <span className="material-symbols-outlined text-[20px]">zoom_in</span>
                    </button>
                  </>
                )}
                <button
                  onClick={() => setShowFilePreview(false)}
                  aria-label={t.cancel}
                  className="flex h-9 w-9 items-center justify-center rounded-full bg-white/15 text-white transition hover:bg-white/25"
                >
                  <span className="material-symbols-outlined text-[20px]">close</span>
                </button>
              </div>
            </div>
            {isPdf ? (
              // Native PDF viewer — its own toolbar gives zoom, scroll and page nav.
              <iframe src={previewUrl} title={selectedFile?.name || "PDF"} className="min-h-0 flex-1 bg-white" />
            ) : (
              <div className="min-h-0 flex-1 overflow-auto p-2">
                <div style={{ width: `${previewZoom * 100}%`, margin: "0 auto" }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={previewUrl} alt={selectedFile?.name || ""} className="block w-full select-none" draggable={false} />
                </div>
              </div>
            )}
          </div>
        )}

        {/* Camera modal */}
        {showCamera && (
          <div
            className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black"
            style={{ background: "rgba(0,0,0,0.95)" }}
          >
            <div className="relative w-full max-w-[640px]">
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3">
                <p className="text-[15px] font-bold text-white">
                  {lang === "si" ? "ලේඛනය ඡායාරූප ගන්න" : "Take a photo of your document"}
                </p>
                <button onClick={closeCamera} className="text-white/70 hover:text-white">
                  <span className="material-symbols-outlined text-[26px]">close</span>
                </button>
              </div>

              {/* Viewfinder */}
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="w-full rounded-xl"
                style={{ maxHeight: "65vh", objectFit: "cover", background: "#111" }}
              />

              {/* Guide overlay */}
              <div
                className="pointer-events-none absolute inset-0 m-auto rounded-xl"
                style={{
                  width: "90%", height: "70%",
                  top: "15%", left: "5%",
                  border: "2px solid rgba(255,255,255,0.5)",
                  borderRadius: "8px",
                }}
              />

              {/* Capture button */}
              <div className="flex items-center justify-center gap-6 py-6">
                <button
                  onClick={closeCamera}
                  className="rounded-full px-5 py-2 text-[13px] font-semibold text-white/70 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  onClick={capturePhoto}
                  className="flex h-16 w-16 items-center justify-center rounded-full bg-white shadow-lg transition hover:scale-105 active:scale-95"
                >
                  <span className="material-symbols-outlined text-[32px]" style={{ color: "var(--brand)" }}>
                    photo_camera
                  </span>
                </button>
                <div className="w-20" />
              </div>

              <p className="pb-4 text-center text-[12px] text-white/50">
                {lang === "si" ? "ලේඛනය රාමුව තුළ ස්ථාන කර ශූල් ඔබන්න" : "Place the document inside the frame then tap the button"}
              </p>
            </div>
          </div>
        )}
      </div>
    </MobileShell>
  );
}
