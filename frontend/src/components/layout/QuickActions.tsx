"use client";
// IT-42: Quick Cash Entry + IT-43: Auto-Crop Scanner — floating action button

import { useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { getStoredLanguage } from "@/lib/i18n";
import { addNotification } from "@/lib/notifications";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

const CASH_CATEGORIES = [
  { key: "transport",  icon: "directions_car",  en: "Transport",  si: "ප‍ොදු",       color: "#2252b5" },
  { key: "food",       icon: "restaurant",       en: "Food",       si: "ආහාර",        color: "#16a34a" },
  { key: "supplies",   icon: "inventory_2",      en: "Supplies",   si: "ද්‍රව්‍ය",   color: "#7c3aed" },
  { key: "utilities",  icon: "bolt",             en: "Utilities",  si: "විදුලිය",     color: "#ea6c0a" },
  { key: "services",   icon: "build",            en: "Services",   si: "සේවා",        color: "#0891b2" },
  { key: "rent",       icon: "home",             en: "Rent",       si: "කුලිය",       color: "#d97706" },
  { key: "marketing",  icon: "campaign",         en: "Marketing",  si: "අලෙවිකරණය",  color: "#dc2626" },
  { key: "other",      icon: "more_horiz",       en: "Other",      si: "වෙනත්",       color: "#64748b" },
];

function getToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

// ── Quick Cash Entry Sheet ───────────────────────────────────────────────────
function QuickCashSheet({ onClose }: { onClose: () => void }) {
  const lang = getStoredLanguage();
  const [cat, setCat]       = useState<typeof CASH_CATEGORIES[0] | null>(null);
  const [amount, setAmount] = useState("");
  const [note, setNote]     = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!cat || !amount) return;
    setSaving(true);
    const token = getToken();
    try {
      const res = await fetch(`${BACKEND_URL}/documents/manual`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          document_type:      "receipt",
          supplier_name:      cat.en,
          currency:           "LKR",
          items:              [{ description: note || cat.en, quantity: 1, unit_price: parseFloat(amount), line_total: parseFloat(amount) }],
          notes:              `quick-entry:${cat.key}${note ? " — " + note : ""}`,
          flow_type_override: "cash_outflow",
          force_save:         false,
        }),
      });
      const data = await res.json();
      if (data.success) {
        addNotification({ type: "success", title: lang === "si" ? "ගෙවීම සටහන් විය" : "Cash entry saved", message: `${data.document_id} · LKR ${parseFloat(amount).toLocaleString()}` });
        onClose();
      }
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center" style={{ background: "rgba(0,0,0,0.4)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        className="w-full max-w-[480px] rounded-t-3xl p-6 shadow-2xl"
        style={{
          background: "var(--surface)",
          paddingBottom: "calc(1.5rem + var(--safe-bottom))",
          maxHeight: "var(--app-height)",
          overflowY: "auto",
          overscrollBehavior: "contain",
        }}
      >
        <p className="mb-4 text-[17px] font-extrabold text-[var(--text-1)]">
          {lang === "si" ? "ඉක්මන් ගෙවීම" : "Quick Cash Entry"}
        </p>

        {/* Category grid */}
        <div className="mb-4 grid grid-cols-4 gap-2">
          {CASH_CATEGORIES.map(c => (
            <button key={c.key} onClick={() => setCat(c)}
              className="flex flex-col items-center gap-1 rounded-xl py-3 transition"
              style={cat?.key === c.key
                ? { background: c.color, color: "#fff" }
                : { background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
              <span className="material-symbols-outlined text-[22px]">{c.icon}</span>
              <span className="text-[9px] font-bold">{lang === "si" ? c.si : c.en}</span>
            </button>
          ))}
        </div>

        {/* Amount */}
        <div className="mb-3 flex items-center gap-2 rounded-xl border px-4 py-3"
          style={{ borderColor: cat ? cat.color : "var(--border)", boxShadow: cat ? `0 0 0 2px ${cat.color}20` : "none" }}>
          <span className="text-[14px] font-bold text-[var(--text-3)]">LKR</span>
          <input type="number" value={amount} onChange={e => setAmount(e.target.value)}
            placeholder="0.00" autoFocus
            className="flex-1 bg-transparent text-[22px] font-extrabold text-[var(--text-1)] outline-none" />
        </div>

        <input value={note} onChange={e => setNote(e.target.value)}
          placeholder={lang === "si" ? "සටහන (විකල්ප)" : "Note (optional)"}
          className="mb-4 w-full rounded-xl border px-4 py-2.5 text-[13px] text-[var(--text-1)] outline-none"
          style={{ background: "var(--bg)", borderColor: "var(--border)" }} />

        <button onClick={save} disabled={!cat || !amount || saving}
          className="flex w-full items-center justify-center gap-2 rounded-2xl py-4 text-[15px] font-bold text-white transition disabled:opacity-50"
          style={{ background: cat ? cat.color : "#64748b" }}>
          {saving
            ? <span className="material-symbols-outlined animate-spin text-[18px]">progress_activity</span>
            : <span className="material-symbols-outlined text-[18px]">save</span>}
          {lang === "si" ? "සුරකින්න" : "Save"}
        </button>
      </div>
    </div>
  );
}

// ── Floating Action Button ───────────────────────────────────────────────────
export default function QuickActions() {
  const lang = getStoredLanguage();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen]     = useState(false);
  const [sheet, setSheet]   = useState<"cash"|null>(null);

  // The chat page has its own fixed input bar in the same bottom-right corner —
  // avoid stacking this FAB on top of it.
  if (pathname?.startsWith("/query")) return null;

  return (
    <>
      {sheet === "cash" && <QuickCashSheet onClose={() => setSheet(null)} />}

      {/* Fan-out menu */}
      {open && (
        <div
          className="fixed right-4 z-40 flex flex-col items-end gap-2"
          style={{ bottom: "calc(var(--bottom-nav-h) + 5.5rem)", right: "calc(1rem + var(--safe-right))" }}
        >
          {/* Scan goes to the full upload page — same Image / PDF / Camera choice,
              preview and total-reconciliation guards as the main upload flow,
              instead of a separate image-only scanner. */}
          <button onClick={() => { setOpen(false); router.push("/upload"); }}
            className="flex items-center gap-2 rounded-2xl px-4 py-3 shadow-lg text-white text-[13px] font-bold transition hover:opacity-90"
            style={{ background: "#2252b5" }}>
            <span className="material-symbols-outlined text-[18px]">document_scanner</span>
            {lang === "si" ? "ස්කෑන්" : "Scan Doc"}
          </button>
          <button onClick={() => { setOpen(false); setSheet("cash"); }}
            className="flex items-center gap-2 rounded-2xl px-4 py-3 shadow-lg text-white text-[13px] font-bold transition hover:opacity-90"
            style={{ background: "#16a34a" }}>
            <span className="material-symbols-outlined text-[18px]">payments</span>
            {lang === "si" ? "ඉක්මන් ගෙවීම" : "Quick Cash"}
          </button>
        </div>
      )}

      {/* FAB */}
      <button
        onClick={() => setOpen(o => !o)}
        className="fixed z-40 flex h-14 w-14 items-center justify-center rounded-full shadow-xl text-white transition hover:opacity-90 active:scale-95"
        style={{
          background: open ? "#64748b" : "var(--brand-mid)",
          // Sits on the measured nav height rather than a hardcoded 72px, so it
          // clears the home indicator and a wrapped Sinhala nav label alike.
          bottom: "calc(var(--bottom-nav-h) + 0.75rem)",
          right: "calc(1rem + var(--safe-right))",
        }}
        aria-label="Quick actions">
        <span className="material-symbols-outlined text-[26px]">{open ? "close" : "add"}</span>
      </button>
    </>
  );
}
