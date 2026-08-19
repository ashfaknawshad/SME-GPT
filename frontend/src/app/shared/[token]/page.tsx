"use client";
// IT-45 — Public read-only document view (no login required)

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { resolveBackendUrl } from "@/lib/backendUrl";

const BACKEND_URL = resolveBackendUrl();

type SharedDoc = {
  document_id: string; document_type: string; date: string;
  company_name: string; supplier_name: string;
  final_total_amount: string | number; currency: string;
  order_id: string; items_json: string;
};

const TYPE_COLOR: Record<string,string> = { invoice:"#2252b5", receipt:"#16a34a", po:"#7c3aed", dn:"#ea6c0a" };

export default function SharedDocPage() {
  const { token } = useParams<{ token: string }>();
  const [doc, setDoc]         = useState<SharedDoc | null>(null);
  const [expiresAt, setExpiry] = useState<string>("");
  const [error, setError]      = useState("");
  const [loading, setLoading]  = useState(true);

  useEffect(() => {
    fetch(`${BACKEND_URL}/shared/${token}`)
      .then(r => r.json())
      .then(d => {
        if (!d.success) throw new Error(d.detail || "Not found or expired");
        setDoc(d.document);
        const exp = new Date(d.expires_at * 1000);
        setExpiry(exp.toLocaleDateString("en-GB", { day:"2-digit", month:"short", year:"numeric" }));
      })
      .catch(e => setError(e.message || "Link expired or invalid"))
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) return (
    <div className="flex min-h-svh items-center justify-center">
      <p className="text-[14px] text-gray-500">Loading shared document…</p>
    </div>
  );

  if (error || !doc) return (
    <div className="flex min-h-svh items-center justify-center px-6">
      <div className="w-full max-w-md rounded-2xl p-8 text-center shadow-lg" style={{ background:"#fff", border:"1px solid #e5e7eb" }}>
        <span className="material-symbols-outlined text-[48px] text-red-400">link_off</span>
        <p className="mt-3 text-[18px] font-bold text-gray-800">Link Expired or Invalid</p>
        <p className="mt-1 text-[13px] text-gray-500">{error}</p>
        <p className="mt-4 text-[12px] text-gray-400">Shared links are valid for 7 days. Ask the sender for a new link.</p>
      </div>
    </div>
  );

  const color  = TYPE_COLOR[doc.document_type] || "#64748b";
  let items: {description:string; quantity:number; unit_price:number; line_total:number}[] = [];
  try { items = JSON.parse(doc.items_json || "[]"); } catch {}
  const total = parseFloat(String(doc.final_total_amount || 0).replace(",","")) || 0;

  return (
    <div className="min-h-svh" style={{ background:"#f8fafc" }}>
      {/* Header */}
      <div className="px-6 py-4 text-white" style={{ background: color }}>
        <div className="mx-auto max-w-[680px] flex items-center justify-between">
          <div>
            <p className="text-[10px] font-bold uppercase opacity-80">SME-GPT · Shared Document</p>
            <p className="text-[20px] font-extrabold">{doc.company_name || "—"}</p>
          </div>
          <div className="text-right">
            <p className="text-[14px] font-bold uppercase">{doc.document_type}</p>
            <p className="text-[11px] opacity-80">Ref: {doc.order_id || doc.document_id}</p>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-[680px] px-6 py-6 space-y-5">
        {/* Parties */}
        <div className="grid grid-cols-2 gap-4 rounded-2xl p-5" style={{ background:"#fff", border:"1px solid #e5e7eb" }}>
          <div>
            <p className="text-[10px] font-bold uppercase text-gray-400">From</p>
            <p className="mt-1 font-semibold text-gray-800">{doc.company_name || "—"}</p>
          </div>
          <div>
            <p className="text-[10px] font-bold uppercase text-gray-400">
              {doc.document_type === "po" ? "Supplier" : doc.document_type === "invoice" ? "Bill To" : "Counterparty"}
            </p>
            <p className="mt-1 font-semibold text-gray-800">{doc.supplier_name || "—"}</p>
          </div>
          <div>
            <p className="text-[10px] font-bold uppercase text-gray-400">Date</p>
            <p className="mt-1 text-gray-800">{doc.date || "—"}</p>
          </div>
          <div>
            <p className="text-[10px] font-bold uppercase text-gray-400">Document ID</p>
            <p className="mt-1 text-gray-800">{doc.document_id}</p>
          </div>
        </div>

        {/* Items */}
        {items.length > 0 && (
          <div className="rounded-2xl p-5" style={{ background:"#fff", border:"1px solid #e5e7eb" }}>
            <p className="mb-3 text-[11px] font-bold uppercase text-gray-400">Line Items</p>
            {items.map((item, i) => (
              <div key={i} className="flex items-center justify-between py-2 border-b last:border-0" style={{ borderColor:"#f3f4f6" }}>
                <div>
                  <p className="text-[13px] font-semibold text-gray-800">{item.description}</p>
                  <p className="text-[11px] text-gray-400">Qty {item.quantity} × {doc.currency} {item.unit_price?.toLocaleString()}</p>
                </div>
                <p className="font-bold text-gray-800">{doc.currency} {item.line_total?.toLocaleString()}</p>
              </div>
            ))}
          </div>
        )}

        {/* Total */}
        <div className="flex items-center justify-between rounded-2xl px-6 py-5" style={{ background: color, color:"#fff" }}>
          <p className="text-[15px] font-bold">TOTAL ({doc.currency})</p>
          <p className="text-[24px] font-extrabold">{total.toLocaleString("en-US", { minimumFractionDigits:2 })}</p>
        </div>

        <p className="text-center text-[11px] text-gray-400">
          This link was shared via SME-GPT · Expires {expiresAt} · Read-only view
        </p>
      </div>
    </div>
  );
}
