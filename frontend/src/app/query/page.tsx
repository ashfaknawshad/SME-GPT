"use client";

// Phase 3 Stage B — the AI Assistant chat IS the query page now.
// ChatGPT-style: thread sidebar (server-backed via /chat/threads), per-turn
// evidence + derivation trace, voice, bilingual. The old form-based flow lives
// at /query/classic during the transition (docs/phase3-retirement-plan.md).

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import ThemeToggle from "@/components/layout/ThemeToggle";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { getSession } from "@/lib/auth";
import { confirmDialog } from "@/lib/confirm";
import { formatMoney, otherPartyName } from "@/lib/format";
import { humanizeFlow } from "@/lib/humanize";
import { resolveBackendUrl } from "@/lib/backendUrl";
import Markdown from "@/components/ui/Markdown";
import SupplierHistoryDrawer from "@/components/ui/SupplierHistoryDrawer";

const BACKEND_URL = resolveBackendUrl();

type EvidenceItem = {
  document_id: string;
  document_type: string;
  date?: string;
  supplier_name?: string;
  order_id?: string;
  flow_type: string;
  flow_direction?: "income" | "expense";
  currency?: string;
  amount_used?: number;
  final_total_amount?: number;
  po_status?: string | null;
  dn_status?: string | null;
  invoice_status?: string | null;
};

type TraceStep = {
  step: number;
  tool: string;
  args: Record<string, unknown>;
  result: string | null;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  evidence?: EvidenceItem[];
  trace?: TraceStep[];
  isError?: boolean;
};

type ThreadSummary = {
  thread_id: string;
  title: string;
  last_message_preview: string;
  updated_at: string;
  document_id?: string | null;
};

type DocScope = {
  document_id: string;
  document_type?: string;
  image_url?: string | null;
};

function resolveImageUrl(url?: string | null): string | null {
  if (!url) return null;
  return /^https?:\/\//.test(url) ? url : `${BACKEND_URL}${url}`;
}

function getAuthToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

function newId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

// Human-friendly labels for the derivation trace (XAI): the LLM only planned by
// calling these deterministic tools — the trace shows exactly what ran.
const TOOL_LABEL: Record<string, { en: string; si: string }> = {
  aggregate_financials: { en: "Calculated a figure from your documents", si: "ඔබේ ලේඛන වලින් අගයක් ගණනය කළා" },
  search_documents:     { en: "Searched your documents", si: "ඔබේ ලේඛන සෙව්වා" },
  get_document_status:  { en: "Looked up a specific document", si: "නිශ්චිත ලේඛනයක් සෙව්වා" },
  find_discrepancies:   { en: "Checked invoice vs PO prices", si: "ඉන්වොයිස් සහ PO මිල පරීක්ෂා කළා" },
};

function toolLabel(tool: string, lang: AppLanguage): string {
  return TOOL_LABEL[tool]?.[lang] ?? tool;
}

function describeArgs(args: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(args || {})) {
    if (v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    const val = typeof v === "object" ? JSON.stringify(v) : String(v);
    parts.push(`${k}: ${val}`);
  }
  return parts.join(" · ");
}

export default function AiAssistantChatPage() {
  const router = useRouter();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const [lang, setLang] = useState<AppLanguage>("en");
  const [companyName, setCompanyName] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [threadLoading, setThreadLoading] = useState(false);
  const [error, setError] = useState("");
  const [isListening, setIsListening] = useState(false);
  const [expandedEvidence, setExpandedEvidence] = useState<Set<string>>(new Set());
  const [expandedTrace, setExpandedTrace] = useState<Set<string>>(new Set());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [docScope, setDocScope] = useState<DocScope | null>(null);
  const [docImageExpanded, setDocImageExpanded] = useState(false);
  // Supplier/customer names, used to turn mentions in an answer into tappable
  // pills that open that counterparty's transaction history.
  const [supplierNames, setSupplierNames] = useState<string[]>([]);
  const [historyFor, setHistoryFor] = useState<string | null>(null);

  const t = ui[lang];

  const authHeaders = useCallback(() => {
    const token = getAuthToken();
    return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  }, []);

  const loadThreads = useCallback(async () => {
    const token = getAuthToken();
    if (!token) return;
    try {
      const res = await fetch(`${BACKEND_URL}/chat/threads`, { headers: authHeaders() });
      if (!res.ok) return; // 503 (disabled) / 401 handled elsewhere — sidebar just stays empty
      const data = await res.json();
      if (data.success) setThreads(data.threads || []);
    } catch {
      // sidebar is non-critical; ignore
    }
  }, [authHeaders]);

  // Load one document's meta (image + type) to scope a conversation to it (Stage C).
  const fetchDocScope = useCallback(async (documentId: string) => {
    setDocScope({ document_id: documentId }); // show the banner immediately; enrich when loaded
    const token = getAuthToken();
    if (!token) return;
    try {
      const res = await fetch(`${BACKEND_URL}/documents/${encodeURIComponent(documentId)}`, { headers: authHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      const doc = data.document || {};
      setDocScope({
        document_id: documentId,
        document_type: doc.document_type,
        image_url: doc.image_url ?? null,
      });
    } catch {
      // banner still shows the id; image just won't render
    }
  }, [authHeaders]);

  useEffect(() => {
    setLang(getStoredLanguage());
    getSession().then((s) => {
      if (s?.companyName) setCompanyName(s.companyName);
    });
    loadThreads();

    // Load counterparty names once, so answers can linkify supplier mentions.
    fetch(`${BACKEND_URL}/suppliers`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((d) => {
        const names = (d?.suppliers || d?.data || d || [])
          .map((s: { name?: string }) => s?.name)
          .filter((n: unknown): n is string => typeof n === "string" && n.trim().length >= 3);
        setSupplierNames(names);
      })
      .catch(() => {});

    // Opened from "Ask about this document" (/query?doc=IN11): start a fresh,
    // document-scoped conversation.
    if (typeof window !== "undefined") {
      const docId = new URLSearchParams(window.location.search).get("doc");
      if (docId) {
        setMessages([]);
        setThreadId(null);
        fetchDocScope(docId);
      }
    }
  }, [loadThreads, fetchDocScope]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(Math.max(ta.scrollHeight, 44), 160)}px`;
  }, [input]);

  const handleVoice = () => {
    type SR = { new(): {
      lang: string; interimResults: boolean; continuous: boolean;
      onresult: ((e: { results: { [i: number]: { [j: number]: { transcript: string } } } }) => void) | null;
      onend: (() => void) | null;
      onerror: (() => void) | null;
      start: () => void;
    }};
    const w = window as unknown as Record<string, unknown>;
    const SRClass = (w.SpeechRecognition || w.webkitSpeechRecognition) as SR | undefined;
    if (!SRClass) { setError("Voice input is not supported in this browser. Use Chrome or Edge."); return; }
    const recognition = new SRClass();
    recognition.lang = lang === "si" ? "si-LK" : "en-US";
    recognition.interimResults = true;
    recognition.continuous = false;
    setIsListening(true);
    recognition.onresult = (e) => {
      const transcript = Array.from({ length: (e.results as unknown as ArrayLike<unknown>).length },
        (_, i) => (e.results[i][0] as {transcript: string}).transcript).join("");
      setInput(transcript);
    };
    recognition.onend = () => setIsListening(false);
    recognition.onerror = () => { setIsListening(false); setError("Voice input failed. Please try again."); };
    recognition.start();
  };

  const handleNewChat = () => {
    setMessages([]);
    setThreadId(null);
    setError("");
    setSidebarOpen(false);
    setDocScope(null);
    setDocImageExpanded(false);
    // Drop ?doc= from the URL so a refresh doesn't re-scope.
    if (typeof window !== "undefined" && window.location.search) {
      window.history.replaceState(null, "", "/query");
    }
  };

  const handleSelectThread = async (id: string) => {
    setSidebarOpen(false);
    if (id === threadId) return;
    setError("");
    // Restore this thread's document scope from the sidebar list (already loaded).
    const selected = threads.find((th) => th.thread_id === id);
    setDocImageExpanded(false);
    if (selected?.document_id) fetchDocScope(selected.document_id);
    else setDocScope(null);
    setMessages([]);       // clear stale turns so the area shows only the loader
    setThreadId(id);       // highlight the selected thread immediately
    setThreadLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/chat/threads/${encodeURIComponent(id)}`, { headers: authHeaders() });
      if (res.status === 401) { router.push("/login"); return; }
      if (!res.ok) { setError(t.aiAssistantThreadLoadError); return; }
      const data = await res.json();
      const loaded: ChatMessage[] = (data.messages || []).map((m: {
        role: "user" | "assistant"; content: string; evidence?: EvidenceItem[]; trace?: TraceStep[];
      }) => ({
        id: newId(),
        role: m.role,
        content: m.content,
        evidence: m.evidence || [],
        trace: m.trace || [],
      }));
      setMessages(loaded);
      setThreadId(id);
    } catch {
      setError(t.aiAssistantThreadLoadError);
    } finally {
      setThreadLoading(false);
    }
  };

  const handleRenameThread = async (id: string) => {
    const current = threads.find((th) => th.thread_id === id);
    const next = typeof window !== "undefined"
      ? window.prompt(t.aiAssistantRenamePrompt, current?.title || "")
      : null;
    if (!next || !next.trim()) return;
    try {
      const res = await fetch(`${BACKEND_URL}/chat/threads/${encodeURIComponent(id)}`, {
        method: "PATCH", headers: authHeaders(), body: JSON.stringify({ title: next.trim() }),
      });
      if (res.ok) {
        setThreads((prev) => prev.map((th) => th.thread_id === id ? { ...th, title: next.trim() } : th));
      }
    } catch { /* ignore */ }
  };

  const handleDeleteThread = async (id: string) => {
    const confirmed = await confirmDialog({
      title: t.aiAssistantDeleteConfirm,
      confirmLabel: t.delete,
      variant: "danger",
    });
    if (!confirmed) return;
    try {
      const res = await fetch(`${BACKEND_URL}/chat/threads/${encodeURIComponent(id)}`, {
        method: "DELETE", headers: authHeaders(),
      });
      if (res.ok) {
        setThreads((prev) => prev.filter((th) => th.thread_id !== id));
        if (id === threadId) handleNewChat();
      }
    } catch { /* ignore */ }
  };

  const toggleSet = (setter: React.Dispatch<React.SetStateAction<Set<string>>>, id: string) => {
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleSend = async () => {
    const question = input.trim();
    if (!question || loading) return;
    setError("");

    if (!companyName.trim()) {
      setError("Company name not found. Please update your profile with your company name.");
      return;
    }
    const token = getAuthToken();
    if (!token) { router.push("/login"); return; }

    const isNewThread = !threadId;
    setMessages((prev) => [...prev, { id: newId(), role: "user", content: question }]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          company_name: companyName.trim(),
          question,
          thread_id: threadId ?? undefined,
          document_id: docScope?.document_id ?? undefined,
        }),
      });

      if (res.status === 401) {
        localStorage.removeItem("token");
        sessionStorage.removeItem("token");
        router.push("/login");
        return;
      }
      if (res.status === 503) {
        setMessages((prev) => [...prev, { id: newId(), role: "assistant", content: t.aiAssistantDisabled, isError: true }]);
        return;
      }

      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.detail || data.message || t.aiAssistantSendError);
      }

      setMessages((prev) => [...prev, {
        id: newId(), role: "assistant", content: data.answer,
        evidence: data.evidence || [], trace: data.trace || [],
      }]);
      if (data.thread_id) setThreadId(data.thread_id);
      // Refresh the sidebar so a new thread appears / preview updates. On a new
      // thread the LLM title is generated in the background, so refetch once more
      // shortly after to pick it up.
      if (isNewThread) {
        loadThreads();
        window.setTimeout(loadThreads, 4000);
      } else {
        setThreads((prev) => prev.map((th) =>
          th.thread_id === data.thread_id ? { ...th, last_message_preview: data.answer } : th));
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : t.aiAssistantSendError;
      setMessages((prev) => [...prev, { id: newId(), role: "assistant", content: msg, isError: true }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const statusBadge = (item: EvidenceItem) => {
    const s = item.po_status || item.dn_status || item.invoice_status;
    if (!s) return null;
    return (
      <span className="rounded-full px-2 py-0.5 text-[9px] font-bold uppercase"
        style={{ background: "var(--surface-2)", color: "var(--text-2)" }}>
        {s.replace(/_/g, " ")}
      </span>
    );
  };

  // ── Sidebar (shared between desktop column + mobile drawer) ────────────────
  const Sidebar = (
    <div className="flex h-full flex-col" style={{ background: "var(--surface)" }}>
      <div className="p-3">
        <button
          onClick={handleNewChat}
          className="flex w-full items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-[13px] font-bold text-white transition hover:opacity-90"
          style={{ background: "var(--brand)" }}
        >
          <span className="material-symbols-outlined text-[18px]">add_comment</span>
          {t.aiAssistantNewChat}
        </button>
      </div>
      <p className="px-4 pb-1 text-[10px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)]">
        {t.aiAssistantThreads}
      </p>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {threads.length === 0 ? (
          <p className="px-2 py-3 text-[12px] leading-5 text-[var(--text-3)]">{t.aiAssistantNoThreads}</p>
        ) : (
          <div className="space-y-0.5">
            {threads.map((th) => (
              <div
                key={th.thread_id}
                className={`group flex cursor-pointer items-center gap-1 rounded-lg px-2 py-2 transition ${
                  th.thread_id === threadId
                    ? "bg-[var(--brand-tint)]"
                    : "hover:bg-[var(--surface-2)]"
                }`}
              >
                <button
                  onClick={() => handleSelectThread(th.thread_id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <p className="truncate text-[13px] font-semibold text-[var(--text-1)]">
                    {th.title || t.aiAssistant}
                  </p>
                  <p className="truncate text-[11px] text-[var(--text-3)]">{th.last_message_preview}</p>
                </button>
                <button
                  onClick={() => handleRenameThread(th.thread_id)}
                  title={t.aiAssistantRename}
                  className="shrink-0 opacity-0 transition group-hover:opacity-100"
                  style={{ color: "var(--text-3)" }}
                >
                  <span className="material-symbols-outlined text-[16px]">edit</span>
                </button>
                <button
                  onClick={() => handleDeleteThread(th.thread_id)}
                  title={t.aiAssistantDelete}
                  className="shrink-0 opacity-0 transition group-hover:opacity-100 hover:text-red-500"
                  style={{ color: "var(--text-3)" }}
                >
                  <span className="material-symbols-outlined text-[16px]">delete</span>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="border-t p-2" style={{ borderColor: "var(--border)" }}>
        <button
          onClick={() => router.push("/query/classic")}
          className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-[12px] font-semibold transition hover:opacity-80"
          style={{ color: "var(--text-3)" }}
        >
          <span className="material-symbols-outlined text-[16px]">tune</span>
          {t.aiAssistantClassicView}
        </button>
      </div>
    </div>
  );

  return (
    <MobileShell hideQuickActions>
      {/* h-[100dvh] (not h-screen/100vh) + overflow-hidden: with
          interactive-widget=resizes-content the dvh shrinks when the keyboard
          opens, so the fixed header stays pinned and only the messages pane
          scrolls — the header no longer scrolls off when you dismiss the
          keyboard. */}
      <div className="flex h-[100dvh] overflow-hidden" style={{ background: "var(--bg)" }}>
        {/* Desktop sidebar */}
        <aside className="hidden w-[272px] shrink-0 border-r lg:block" style={{ borderColor: "var(--border)" }}>
          {Sidebar}
        </aside>

        {/* Mobile drawer */}
        {sidebarOpen && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <div className="absolute inset-0" style={{ background: "rgba(0,0,0,0.4)" }} onClick={() => setSidebarOpen(false)} />
            <div className="absolute left-0 top-0 h-full w-[280px] shadow-2xl">{Sidebar}</div>
          </div>
        )}

        {/* Chat column */}
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Header */}
          <div className="shrink-0 px-4 py-3 sm:px-6" style={{ background: "var(--surface)", borderBottom: "1px solid var(--border)" }}>
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <button
                  onClick={() => setSidebarOpen(true)}
                  className="lg:hidden"
                  title={t.aiAssistantThreads}
                  style={{ color: "var(--text-2)" }}
                >
                  <span className="material-symbols-outlined text-[22px]">menu</span>
                </button>
                <div className="min-w-0">
                  <h1 className="truncate text-[16px] font-extrabold text-[var(--text-1)]">{t.aiAssistant}</h1>
                  <p className="truncate text-[11px] text-[var(--text-3)]">{companyName || "…"}</p>
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={handleNewChat}
                  title={t.aiAssistantNewChat}
                  className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] font-semibold transition hover:opacity-80 lg:hidden"
                  style={{ background: "var(--surface-2)", color: "var(--text-2)" }}
                >
                  <span className="material-symbols-outlined text-[16px]">add_comment</span>
                </button>
                <ThemeToggle />
                <LanguageSwitcher />
              </div>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6" style={{ paddingBottom: "160px" }}>
            <div className="mx-auto w-full max-w-[900px] space-y-4">
              {/* Document context card (Stage C) — the assistant "shows" the document
                  it has open so the user knows it's in this chat's memory. */}
              {docScope && !threadLoading && (
                <div className="flex justify-start">
                  <div className="w-full rounded-2xl px-4 py-3"
                    style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                    <p className="mb-2 text-[10px] font-bold uppercase tracking-[0.1em] text-[var(--text-3)]">
                      {t.aiAssistantViewingDoc}
                    </p>
                    <div className="flex items-start gap-3">
                      <button
                        onClick={() => docScope.image_url && setDocImageExpanded((v) => !v)}
                        className="relative shrink-0 overflow-hidden rounded-xl border transition hover:opacity-90"
                        style={{ borderColor: "var(--border)", cursor: docScope.image_url ? "zoom-in" : "default" }}
                        title={docScope.image_url ? (docImageExpanded ? t.aiAssistantHideImage : t.aiAssistantShowImage) : undefined}
                      >
                        {docScope.image_url ? (
                          <>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={resolveImageUrl(docScope.image_url) ?? ""}
                              alt={docScope.document_id}
                              className="h-16 w-16 object-cover"
                            />
                            <span className="material-symbols-outlined absolute bottom-0.5 right-0.5 rounded bg-black/50 text-[13px] text-white">
                              zoom_in
                            </span>
                          </>
                        ) : (
                          <span className="material-symbols-outlined flex h-16 w-16 items-center justify-center text-[26px]"
                            style={{ background: "var(--surface-2)", color: "var(--text-3)" }}>
                            description
                          </span>
                        )}
                      </button>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[14px] font-bold text-[var(--text-1)]">
                          {docScope.document_type ? `${docScope.document_type.toUpperCase()} · ` : ""}
                          {docScope.document_id}
                        </p>
                        <p className="mt-1 text-[13px] leading-5 text-[var(--text-2)]">
                          {t.aiAssistantDocGreeting}
                        </p>
                        <button
                          onClick={() => router.push(`/analysis/${docScope.document_id}`)}
                          className="mt-1.5 inline-flex items-center gap-0.5 text-[12px] font-semibold transition hover:opacity-75"
                          style={{ color: "var(--brand-mid)" }}
                        >
                          {t.aiAssistantOpenDoc}
                          <span className="material-symbols-outlined text-[14px]">open_in_new</span>
                        </button>
                      </div>
                    </div>
                    {docImageExpanded && docScope.image_url && (
                      <div className="mt-3 flex justify-center">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={resolveImageUrl(docScope.image_url) ?? ""}
                          alt={docScope.document_id}
                          className="max-h-[360px] w-auto rounded-xl border"
                          style={{ borderColor: "var(--border)" }}
                        />
                      </div>
                    )}
                  </div>
                </div>
              )}

              {messages.length === 0 && !threadLoading && !docScope && (
                <div className="rounded-2xl px-5 py-4 text-[14px] leading-6"
                  style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
                  {t.aiAssistantGreeting}
                </div>
              )}

              {threadLoading && (
                <div className="flex justify-center py-6">
                  <span className="material-symbols-outlined animate-spin text-[22px]" style={{ color: "var(--text-3)" }}>
                    progress_activity
                  </span>
                </div>
              )}

              {messages.map((m) => (
                <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[85%] ${m.role === "user" ? "" : "w-full"}`}>
                    <div className={`rounded-2xl px-4 py-3 text-[14px] leading-6 ${m.role === "user" || m.isError ? "whitespace-pre-line" : ""}`}
                      style={
                        m.role === "user"
                          ? { background: "var(--brand)", color: "#fff" }
                          : m.isError
                          ? { background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.2)", color: "#dc2626" }
                          : { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-1)" }
                      }>
                      {m.role === "assistant" && !m.isError
                        ? <Markdown text={m.content} entities={supplierNames} onEntityClick={setHistoryFor} />
                        : m.content}
                    </div>

                    {m.role === "assistant" && (
                      <div className="mt-1.5 flex flex-wrap gap-3">
                        {m.trace && m.trace.length > 0 && (
                          <button onClick={() => toggleSet(setExpandedTrace, m.id)}
                            className="flex items-center gap-1 text-[11px] font-semibold transition hover:opacity-75"
                            style={{ color: "var(--brand-mid)" }}>
                            <span className="material-symbols-outlined text-[14px]">
                              {expandedTrace.has(m.id) ? "expand_less" : "expand_more"}
                            </span>
                            {t.aiAssistantTrace}
                          </button>
                        )}
                        {m.evidence && m.evidence.length > 0 && (
                          <button onClick={() => toggleSet(setExpandedEvidence, m.id)}
                            className="flex items-center gap-1 text-[11px] font-semibold transition hover:opacity-75"
                            style={{ color: "var(--brand-mid)" }}>
                            <span className="material-symbols-outlined text-[14px]">
                              {expandedEvidence.has(m.id) ? "expand_less" : "expand_more"}
                            </span>
                            {t.aiAssistantSources} ({m.evidence.length})
                          </button>
                        )}
                      </div>
                    )}

                    {/* Derivation trace */}
                    {m.role === "assistant" && expandedTrace.has(m.id) && m.trace && (
                      <div className="mt-2 space-y-2 rounded-xl p-3"
                        style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                        {m.trace.map((s) => (
                          <div key={s.step} className="text-[12px]">
                            <p className="font-semibold text-[var(--text-1)]">
                              {t.aiAssistantTraceStep} {s.step}: {toolLabel(s.tool, lang)}
                            </p>
                            {describeArgs(s.args) && (
                              <p className="mt-0.5 break-words text-[var(--text-3)]">{describeArgs(s.args)}</p>
                            )}
                            {s.result && (
                              <p className="mt-0.5 break-words font-mono text-[11px] text-[var(--text-2)]">
                                → {s.result}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Evidence / sources */}
                    {m.role === "assistant" && expandedEvidence.has(m.id) && m.evidence && (
                      <div className="mt-2 space-y-2">
                        {m.evidence.map((item, idx) => (
                          <div key={`${item.document_id}-${idx}`} className="rounded-xl px-3 py-2.5 text-[12px]"
                            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <span className="font-bold text-[var(--text-1)]">
                                {otherPartyName(item) || item.document_id}
                              </span>
                              <div className="flex items-center gap-1.5">
                                {statusBadge(item)}
                                <span className="rounded-md px-2 py-0.5 text-[10px] font-bold uppercase"
                                  style={{ background: "var(--brand-tint)", color: "var(--brand-mid)" }}>
                                  {item.document_type}
                                </span>
                              </div>
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[var(--text-3)]">
                              <span>{item.document_id}</span>
                              {item.date && <span>{item.date}</span>}
                              <span>{humanizeFlow(item.flow_type, lang)}</span>
                              <span className="font-semibold text-[var(--text-2)]">
                                {formatMoney(item.amount_used ?? item.final_total_amount, item.currency) || "—"}
                              </span>
                              <button
                                onClick={() => router.push(`/analysis/${item.document_id}`)}
                                className="ml-auto flex items-center gap-0.5 font-semibold transition hover:opacity-75"
                                style={{ color: "var(--brand-mid)" }}
                              >
                                {t.aiAssistantOpenDoc}
                                <span className="material-symbols-outlined text-[13px]">chevron_right</span>
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}

              {loading && (
                <div className="flex justify-start">
                  <div className="flex items-center gap-2 rounded-2xl px-4 py-3 text-[13px]"
                    style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-3)" }}>
                    <span className="material-symbols-outlined animate-spin text-[16px]">progress_activity</span>
                    {t.aiAssistantSending}
                  </div>
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          </div>

          {/* Input bar — fixed above BottomNav */}
          <div className="fixed bottom-[64px] left-0 right-0 z-40 px-4 py-3 sm:px-6 lg:left-[272px]"
            style={{ background: "var(--surface)", borderTop: "1px solid var(--border)" }}>
            <div className="mx-auto w-full max-w-[900px]">
              {error && (
                <div className="mb-2 rounded-xl px-3 py-2 text-[12px] text-red-600"
                  style={{ background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.2)" }}>
                  {error}
                </div>
              )}
              {isListening && (
                <div className="mb-2 flex items-center gap-2 text-[12px] text-red-600">
                  <span className="material-symbols-outlined text-[14px] animate-pulse">mic</span>
                  {t.listeningMsg}
                </div>
              )}
              <div className="flex items-end gap-2 rounded-2xl px-2 py-2"
                style={{ background: "var(--bg)", border: "1px solid var(--border)" }}>
                <button onClick={handleVoice} title={t.voiceInput}
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition hover:opacity-70"
                  style={{ color: isListening ? "#dc2626" : "var(--text-3)" }}>
                  <span className="material-symbols-outlined text-[19px]">{isListening ? "stop_circle" : "mic"}</span>
                </button>
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={t.aiAssistantPlaceholder}
                  rows={1}
                  className="min-h-[36px] flex-1 resize-none overflow-y-auto bg-transparent px-1 py-1.5 text-[14px] text-[var(--text-1)] outline-none placeholder:text-[var(--text-3)]"
                />
                <button onClick={handleSend} disabled={loading || !input.trim()} title={t.aiAssistantSend}
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white transition hover:opacity-90 disabled:opacity-40"
                  style={{ background: "var(--brand)" }}>
                  <span className="material-symbols-outlined text-[19px]">send</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <BottomNav />

      {historyFor && (
        <SupplierHistoryDrawer name={historyFor} lang={lang} onClose={() => setHistoryFor(null)} />
      )}
    </MobileShell>
  );
}
