"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import PageShell from "@/components/layout/PageShell";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import { resolveBackendUrl } from "@/lib/backendUrl";
import SupplierHistoryDrawer from "@/components/ui/SupplierHistoryDrawer";

const BACKEND_URL = resolveBackendUrl();

type Supplier = {
  name: string; document_count: number;
  total_payable: number; total_receivable: number; net_position: number;
  total_paid: number; total_received: number;
  document_types: string[]; last_transaction: string;
};
type DupGroup = string[];

const TYPE_COLOR: Record<string, string> = {
  invoice:"#2252b5", receipt:"#16a34a", po:"#7c3aed", dn:"#ea6c0a",
};

function getToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

export default function SuppliersPage() {
  const router = useRouter();
  const [lang, setLang]           = useState<AppLanguage>("en");
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState("");
  const [search, setSearch]       = useState("");
  const [tab, setTab]             = useState<"all"|"supplier"|"customer">("all");
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [dupGroups, setDupGroups]   = useState<DupGroup[]>([]);
  const [merging, setMerging]       = useState<DupGroup | null>(null);

  useEffect(() => { setLang(getStoredLanguage()); }, []);
  const t = ui[lang];

  const fetchSuppliers = async (q = "") => {
    const token = getToken();
    if (!token) { router.push("/login"); return; }
    setLoading(true); setError("");
    try {
      const res = await fetch(`${BACKEND_URL}/suppliers?q=${encodeURIComponent(q)}`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (res.status === 401) { localStorage.removeItem("token"); router.push("/login"); return; }
      const data = await res.json();
      if (!data.success) throw new Error(data.message || "Failed");
      setSuppliers(data.suppliers || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally { setLoading(false); }
  };

  const fetchDuplicates = async () => {
    const token = getToken();
    try {
      const res = await fetch(`${BACKEND_URL}/suppliers/duplicates`, { headers: { Authorization: `Bearer ${token}` } });
      const data = await res.json();
      if (data.success) setDupGroups(data.groups || []);
    } catch { /* non-fatal */ }
  };

  useEffect(() => { fetchSuppliers(); fetchDuplicates(); }, []);
  const handleSearch = (v: string) => { setSearch(v); fetchSuppliers(v); };

  const doMerge = async (canonical: string, aliases: string[]) => {
    const token = getToken();
    await fetch(`${BACKEND_URL}/suppliers/merge`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ canonical_name: canonical, aliases }),
    });
    setMerging(null);
    setDupGroups(g => g.filter(gr => !gr.includes(canonical)));
    fetchSuppliers(search);
  };

  const filtered = suppliers.filter(s => {
    if (tab === "supplier") return s.total_payable > 0;
    if (tab === "customer") return s.total_receivable > 0;
    return true;
  });

  const typeColor = TYPE_COLOR;

  return (
    <>
      {historyFor && (
        <SupplierHistoryDrawer name={historyFor} lang={lang} onClose={() => setHistoryFor(null)} />
      )}
    <PageShell
      backLabel={t.backToDashboard}
      title={lang === "si" ? "සැපයුම්කරුවන් සහ ගනුදෙනුකරුවන්" : "Suppliers & Customers"}
      subtitle={lang === "si" ? "ඔබගේ ලේඛනවල ඇති සියලු ගනුදෙනු පාර්ශ්ව" : "All counterparties across your documents with full transaction history."}
      width="standard"
    >

          {/* Duplicate vendor alert */}
          {dupGroups.map((group, gi) => (
            <div key={gi} className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl px-4 py-3"
              style={{ background: "rgba(234,108,10,0.06)", border: "1px solid rgba(234,108,10,0.25)" }}>
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-[18px]" style={{ color: "#ea6c0a" }}>warning</span>
                <p className="text-[12px] font-semibold" style={{ color: "#ea6c0a" }}>
                  {lang === "si" ? "සමාන නම් හමු විය:" : "Possible duplicate vendors:"}
                  {" "}{group.join(", ")}
                </p>
              </div>
              <button onClick={() => setMerging(group)}
                className="rounded-lg px-3 py-1.5 text-[11px] font-bold text-white transition hover:opacity-90"
                style={{ background: "#ea6c0a" }}>
                {lang === "si" ? "ඒකාබද්ධ කරන්න" : "Merge"}
              </button>
            </div>
          ))}

          {/* Merge modal */}
          {merging && (
            <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.4)" }}>
              <div className="w-full max-w-sm rounded-2xl p-6" style={{ background: "var(--surface)" }}>
                <p className="mb-4 font-bold text-[var(--text-1)]">
                  {lang === "si" ? "ඒකාබද්ධ කිරීම" : "Merge duplicate vendors"}
                </p>
                <p className="mb-3 text-[12px] text-[var(--text-2)]">
                  {lang === "si" ? "ප‍්‍රධාන නම:" : "Choose canonical name:"}
                </p>
                <div className="space-y-2">
                  {merging.map(name => (
                    <button key={name} onClick={() => doMerge(name, merging)}
                      className="w-full rounded-xl px-4 py-2.5 text-left text-[13px] font-semibold transition hover:opacity-80"
                      style={{ background: "var(--brand-tint)", color: "var(--brand-mid)" }}>
                      ✓ {name}
                    </button>
                  ))}
                </div>
                <button onClick={() => setMerging(null)}
                  className="mt-4 w-full rounded-xl py-2 text-[12px] text-[var(--text-3)] hover:opacity-70">
                  {lang === "si" ? "අවලංගු කරන්න" : "Cancel"}
                </button>
              </div>
            </div>
          )}

          {/* Search + tabs */}
          <div className="mt-5 flex items-center gap-2 rounded-xl px-4 py-2.5"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            <span className="material-symbols-outlined text-[18px]" style={{ color: "var(--text-3)" }}>search</span>
            <input type="text" value={search} onChange={e => handleSearch(e.target.value)}
              placeholder={lang === "si" ? "නම සොයන්න..." : "Search by name..."}
              className="flex-1 bg-transparent text-[14px] text-[var(--text-1)] outline-none placeholder:text-[var(--text-3)]" />
          </div>

          <div className="mt-3 flex gap-2">
            {(["all","supplier","customer"] as const).map(tp => (
              <button key={tp} onClick={() => setTab(tp)}
                className="rounded-xl px-4 py-1.5 text-[12px] font-semibold transition"
                style={tab === tp
                  ? { background: "var(--brand-mid)", color: "#fff" }
                  : { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
                {tp === "all" ? (lang === "si" ? "සියල්ල" : "All")
                  : tp === "supplier" ? (lang === "si" ? "සැපයුම්කරුවන්" : "Suppliers")
                  : (lang === "si" ? "ගනුදෙනුකරුවන්" : "Customers")}
              </button>
            ))}
            <span className="ml-auto text-[12px] text-[var(--text-3)] self-center">
              {filtered.length} {lang === "si" ? "ප‍්‍රතිඵල" : "results"}
            </span>
          </div>

          {loading ? (
            <div className="mt-8 rounded-2xl py-10 text-center text-[14px] text-[var(--text-2)]"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
              {t.loading}
            </div>
          ) : error ? (
            <div className="mt-5 rounded-2xl px-4 py-3 text-[13px] text-red-600"
              style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.2)" }}>
              {error}
            </div>
          ) : filtered.length === 0 ? (
            <div className="mt-8 rounded-2xl py-10 text-center text-[14px] text-[var(--text-2)]"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
              {lang === "si" ? "සැපයුම්කරුවන් හමු නොවිය." : "No suppliers found."}
            </div>
          ) : (
            <div className="mt-5 card-list">
              {filtered.map((s) => (
                <div key={s.name} className="rounded-2xl p-5"
                  style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-[16px] font-bold text-[var(--text-1)]">{s.name}</p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {s.document_types.map(dt => (
                          <span key={dt} className="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase"
                            style={{ background: `${typeColor[dt] || "#64748b"}18`, color: typeColor[dt] || "#64748b" }}>
                            {dt}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-[11px] text-[var(--text-3)]">
                        {s.document_count} {lang === "si" ? "ලේඛන" : "documents"}
                      </p>
                      {s.last_transaction && (
                        <p className="text-[11px] text-[var(--text-3)]">
                          {lang === "si" ? "අවසාන:" : "Last:"} {s.last_transaction}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Row 1 — Receivable | Payable */}
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    {[
                      { label: lang === "si" ? "ලැබිය යුතු" : "Receivable",  value: s.total_receivable, color: "#16a34a", bg: "rgba(22,163,74,0.06)",   border: "rgba(22,163,74,0.2)"  },
                      { label: lang === "si" ? "ගෙවිය යුතු" : "Payable",     value: s.total_payable,    color: "#dc2626", bg: "rgba(220,38,38,0.06)",  border: "rgba(220,38,38,0.2)" },
                    ].map(({ label, value, color, bg, border }) => (
                      <div key={label} className="rounded-xl p-3 text-center" style={{ background: bg, border: `1px solid ${border}` }}>
                        <p className="text-[9px] font-bold uppercase tracking-wider" style={{ color }}>{label}</p>
                        <p className="mt-1 text-[14px] font-extrabold" style={{ color }}>
                          {value === 0 ? "—" : `LKR ${value.toLocaleString()}`}
                        </p>
                      </div>
                    ))}
                  </div>

                  {/* Row 2 — Total Received | Total Paid */}
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    {[
                      { label: lang === "si" ? "ලැබුණු මුදල" : "Total Received", value: s.total_received, color: "#0891b2", bg: "rgba(8,145,178,0.06)",  border: "rgba(8,145,178,0.2)"  },
                      { label: lang === "si" ? "ගෙව්වා"       : "Total Paid",     value: s.total_paid,     color: "#7c3aed", bg: "rgba(124,58,237,0.06)", border: "rgba(124,58,237,0.2)" },
                    ].map(({ label, value, color, bg, border }) => (
                      <div key={label} className="rounded-xl p-3 text-center" style={{ background: bg, border: `1px solid ${border}` }}>
                        <p className="text-[9px] font-bold uppercase tracking-wider" style={{ color }}>{label}</p>
                        <p className="mt-1 text-[14px] font-extrabold" style={{ color }}>
                          {value === 0 ? "—" : `LKR ${value.toLocaleString()}`}
                        </p>
                      </div>
                    ))}
                  </div>

                  {/* Net Position — full width */}
                  <div className="mt-2 flex items-center justify-between rounded-xl px-4 py-2.5"
                    style={{
                      background: s.net_position >= 0 ? "rgba(34,82,181,0.06)" : "rgba(234,108,10,0.06)",
                      border: `1px solid ${s.net_position >= 0 ? "rgba(34,82,181,0.15)" : "rgba(234,108,10,0.2)"}`,
                    }}>
                    <p className="text-[10px] font-bold uppercase tracking-wider"
                      style={{ color: s.net_position >= 0 ? "#2252b5" : "#ea6c0a" }}>
                      {lang === "si" ? "ශේෂය" : "Net Position"}
                    </p>
                    <p className="text-[15px] font-extrabold"
                      style={{ color: s.net_position >= 0 ? "#2252b5" : "#ea6c0a" }}>
                      {s.net_position === 0 ? "—" : `${s.net_position >= 0 ? "+" : ""}LKR ${s.net_position.toLocaleString()}`}
                    </p>
                  </div>

                  <div className="mt-3 flex items-center gap-4">
                    <button onClick={() => setHistoryFor(s.name)}
                      className="flex items-center gap-1.5 text-[12px] font-bold transition hover:opacity-75"
                      style={{ color: "var(--brand-mid)" }}>
                      <span className="material-symbols-outlined text-[15px]">history</span>
                      {lang === "si" ? "ඉතිහාසය බලන්න" : "View history"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
    </PageShell>
    </>
  );
}
