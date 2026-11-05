"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import PageShell from "@/components/layout/PageShell";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import * as XLSX from "xlsx";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type PnlMonth = { month: string; revenue: number; expenses: number; net: number; doc_count: number };

function getAuthToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

export default function PnlPage() {
  const router = useRouter();
  const [lang, setLang] = useState<AppLanguage>("en");
  const [months, setMonths] = useState(12);
  const [data, setData] = useState<{total_revenue:number;total_expenses:number;net_profit:number;data:PnlMonth[]}|null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => { setLang(getStoredLanguage()); }, []);
  const t = ui[lang];

  const fetchPnl = async (m: number) => {
    const token = getAuthToken();
    if (!token) { router.push("/login"); return; }
    setLoading(true); setError("");
    try {
      const res = await fetch(`${BACKEND_URL}/reports/pnl?months=${m}`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (res.status === 401) { localStorage.removeItem("token"); router.push("/login"); return; }
      const d = await res.json();
      if (!d.success) throw new Error(d.message || "Failed");
      setData(d);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed"); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchPnl(months); }, [months]);

  const exportExcel = () => {
    if (!data) return;
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(data.data), "P&L");
    XLSX.writeFile(wb, `pnl-report-${months}months.xlsx`);
  };

  const fmt = (n: number) => `LKR ${n.toLocaleString("en-US", { minimumFractionDigits: 2 })}`;

  return (
    <PageShell
      backLabel={t.backToDashboard}
      title={lang === "si" ? "ලාභ සහ පාඩු වාර්තාව" : "Profit & Loss Report"}
      subtitle={lang === "si" ? "ආදායම සහ වියදම් මාසික සාරාංශය" : "Monthly revenue vs expenses from your documents."}
      width="standard"
      topBarRight={
        <div className="flex items-center gap-2">
          <select value={months} onChange={(e) => setMonths(Number(e.target.value))}
            className="field-input rounded-xl border px-3 py-2 text-[13px]">
            {[3, 6, 12, 24].map((m) => (
              <option key={m} value={m}>{m} {lang === "si" ? "මාස" : "months"}</option>
            ))}
          </select>
          {data && data.data.length > 0 && (
            <button onClick={exportExcel}
              className="flex items-center gap-1.5 rounded-xl px-4 py-2 text-[12px] font-bold text-white transition hover:opacity-80"
              style={{ background: "#16a34a" }}>
              <span className="material-symbols-outlined text-[15px]">table_view</span>
              {lang === "si" ? "Excel" : "Export"}
            </button>
          )}
        </div>
      }
    >

          {/* Summary cards — show pulsing skeletons while (re)fetching so revenue/
              expenses/net don't display stale values on a month-count change. */}
          {(loading || data) && !error && (
            <div className="mt-5 grid grid-cols-3 gap-3">
              {[
                { label: lang === "si" ? "ආදායම" : "Revenue",  value: data?.total_revenue,  color: "#16a34a" },
                { label: lang === "si" ? "වියදම" : "Expenses",  value: data?.total_expenses, color: "#dc2626" },
                { label: lang === "si" ? "ශුද්ධ ලාභය" : "Net Profit", value: data?.net_profit,
                  color: (data?.net_profit ?? 0) >= 0 ? "#2252b5" : "#ea6c0a" },
              ].map(({ label, value, color }) => (
                <div key={label} className="rounded-2xl p-4"
                  style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                  <p className="text-[10px] font-bold uppercase text-[var(--text-3)]">{label}</p>
                  {loading ? (
                    <div className="mt-1 h-[18px] w-3/4 animate-pulse rounded-md" style={{ background: "var(--border)" }} aria-hidden />
                  ) : (
                    <p className="mt-1 text-[15px] font-extrabold" style={{ color }}>
                      {fmt(value ?? 0)}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}

          {loading ? (
            <>
              {/* Chart skeleton */}
              <div className="mt-5 rounded-2xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <div className="mb-3 h-[11px] w-24 animate-pulse rounded" style={{ background: "var(--border)" }} aria-hidden />
                <div className="h-[240px] w-full animate-pulse rounded-xl" style={{ background: "var(--border)" }} aria-hidden />
              </div>
              {/* Table skeleton */}
              <div className="mt-4 space-y-3 rounded-2xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--border)" }} aria-busy aria-label={t.loading}>
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="h-[16px] w-full animate-pulse rounded" style={{ background: "var(--border)" }} aria-hidden />
                ))}
              </div>
            </>
          ) : error ? (
            <div className="mt-5 rounded-2xl px-4 py-3 text-[13px] text-red-600"
              style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.2)" }}>{error}</div>
          ) : data && data.data.length > 0 ? (
            <>
              {/* Bar chart */}
              <div className="mt-5 rounded-2xl p-4"
                style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <p className="mb-3 text-[11px] font-bold uppercase text-[var(--text-3)]">
                  {lang === "si" ? "මාසික ප්‍රස්ථාරය" : "Monthly Chart"}
                </p>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={data.data} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="month" tick={{ fontSize: 10 }} stroke="var(--text-3)" />
                    <YAxis tick={{ fontSize: 10 }} stroke="var(--text-3)"
                      tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
                    <Tooltip
                      formatter={(value, name) => [
                        fmt(Number(value ?? 0)),
                        name === "revenue"  ? (lang === "si" ? "ආදායම" : "Revenue")
                        : name === "expenses" ? (lang === "si" ? "වියදම" : "Expenses")
                        : (lang === "si" ? "ශේෂය" : "Net"),
                      ]}
                    />
                    <Legend formatter={(v) =>
                      v === "revenue" ? (lang === "si" ? "ආදායම" : "Revenue")
                        : v === "expenses" ? (lang === "si" ? "වියදම" : "Expenses")
                        : (lang === "si" ? "ශේෂය" : "Net")
                    } />
                    <Bar dataKey="revenue"  fill="#16a34a" radius={[3,3,0,0]} />
                    <Bar dataKey="expenses" fill="#dc2626" radius={[3,3,0,0]} />
                    <Bar dataKey="net"      fill="#2252b5" radius={[3,3,0,0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* Table */}
              <div className="mt-4 overflow-hidden rounded-2xl"
                style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <table className="w-full text-[12px]">
                  <thead style={{ background: "var(--bg)" }}>
                    <tr className="text-left text-[var(--text-3)]">
                      {[lang === "si" ? "මාසය" : "Month",
                        lang === "si" ? "ආදායම" : "Revenue",
                        lang === "si" ? "වියදම" : "Expenses",
                        lang === "si" ? "ශේෂය" : "Net",
                        lang === "si" ? "ලේඛන" : "Docs"].map((h) => (
                        <th key={h} className="px-4 py-3 font-semibold">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.data.map((m) => (
                      <tr key={m.month} className="border-t" style={{ borderColor: "var(--border)" }}>
                        <td className="px-4 py-3 font-semibold text-[var(--text-1)]">{m.month}</td>
                        <td className="px-4 py-3 font-bold" style={{ color: "#16a34a" }}>
                          {m.revenue > 0 ? fmt(m.revenue) : "—"}
                        </td>
                        <td className="px-4 py-3 font-bold" style={{ color: "#dc2626" }}>
                          {m.expenses > 0 ? fmt(m.expenses) : "—"}
                        </td>
                        <td className="px-4 py-3 font-bold"
                          style={{ color: m.net >= 0 ? "#2252b5" : "#ea6c0a" }}>
                          {m.net !== 0 ? fmt(m.net) : "—"}
                        </td>
                        <td className="px-4 py-3 text-[var(--text-3)]">{m.doc_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="mt-8 rounded-2xl py-10 text-center text-[14px] text-[var(--text-2)]"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
              {lang === "si" ? "P&L දත්ත නොමැත." : "No P&L data found. Upload some invoices or receipts first."}
            </div>
          )}
    </PageShell>
  );
}
