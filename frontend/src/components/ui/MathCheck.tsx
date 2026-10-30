"use client";

import { useEffect, useRef, useState } from "react";
import type { AppLanguage } from "@/lib/i18n";

type MathCheckItem = { description: string; line_total: string | number };

type MathCheckProps = {
  items: MathCheckItem[];
  /** The total as printed on the document (raw OCR total). */
  printedTotal: string | number;
  currency?: string;
  lang: AppLanguage;
};

// How many rows to animate before collapsing the rest into "+N more".
const MAX_ROWS = 12;
// Delay between each row appearing, in ms.
const ROW_STAGGER = 140;

function toNumber(v: string | number): number {
  const n = Number(String(v ?? "").replace(/,/g, "").replace(/Rs\.?/gi, "").trim());
  return Number.isFinite(n) ? n : 0;
}

function fmt(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Animates a number from 0 to `target` once, over `durationMs`. */
function useCountUp(target: number, durationMs: number): number {
  const [value, setValue] = useState(0);
  const from = useRef(0);

  useEffect(() => {
    const start = performance.now();
    const initial = from.current;
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min((now - start) / durationMs, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(initial + (target - initial) * eased);
      if (t < 1) frame = requestAnimationFrame(tick);
      else from.current = target;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, durationMs]);

  return value;
}

/**
 * Adds the extracted line items up on screen and compares the result with the
 * total printed on the document — a visible "the app checks the maths" moment.
 * Purely presentational: the authoritative check is the backend's
 * arithmetic_validator and the save-time mismatch prompt.
 */
export default function MathCheck({ items, printedTotal, currency, lang }: MathCheckProps) {
  const si = lang === "si";
  const rows = items
    .map((i) => ({ description: String(i.description || "").trim(), amount: toNumber(i.line_total) }))
    .filter((r) => r.amount > 0);

  const subtotal = Math.round(rows.reduce((s, r) => s + r.amount, 0) * 100) / 100;
  const printed = toNumber(printedTotal);
  const shown = rows.slice(0, MAX_ROWS);
  const countMs = Math.max(shown.length * ROW_STAGGER, 600);
  const running = useCountUp(subtotal, countMs);

  // Hold the verdict back until the rows have finished adding up.
  const [showVerdict, setShowVerdict] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setShowVerdict(true), countMs + 150);
    return () => clearTimeout(id);
  }, [countMs]);

  if (rows.length === 0) return null;

  const cur = currency && currency !== "NULL" ? `${currency} ` : "";
  const hasPrinted = printed > 0;
  const matches = hasPrinted && Math.abs(subtotal - printed) <= 0.01;
  const diff = Math.abs(subtotal - printed);

  return (
    <div
      className="mt-5 rounded-2xl p-4"
      style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}
    >
      <p className="flex items-center gap-2 text-[14px] font-extrabold text-[var(--text-1)]">
        <span aria-hidden="true">🧮</span>
        {si ? "ගණිත පරීක්ෂාව" : "Math check"}
      </p>
      <p className="mt-0.5 text-[12px] text-[var(--text-2)]">
        {si ? "අයිතම එකින් එක එකතු කරමින්…" : "Adding up every item, one by one…"}
      </p>

      <ul className="mt-3 space-y-1">
        {shown.map((r, idx) => (
          <li
            key={idx}
            className="fade-up flex items-center justify-between gap-3 text-[13px]"
            style={{ animationDelay: `${idx * ROW_STAGGER}ms` }}
          >
            <span className="min-w-0 truncate text-[var(--text-2)]">{r.description || "—"}</span>
            <span className="shrink-0 font-semibold tabular-nums text-[var(--text-1)]">+ {fmt(r.amount)}</span>
          </li>
        ))}
        {rows.length > shown.length && (
          <li className="text-[12px] text-[var(--text-3)]">
            {si ? `+ තවත් අයිතම ${rows.length - shown.length}ක්` : `+ ${rows.length - shown.length} more`}
          </li>
        )}
      </ul>

      <div
        className="mt-3 flex items-center justify-between border-t pt-3"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="text-[13px] font-bold text-[var(--text-1)]">
          {si ? "අයිතම එකතුව" : "Items add up to"}
        </span>
        <span className="text-[18px] font-extrabold tabular-nums" style={{ color: "var(--brand-mid)" }}>
          {cur}{fmt(running)}
        </span>
      </div>

      {showVerdict && hasPrinted && (
        <div
          className="pop-in mt-3 rounded-xl px-3.5 py-3 text-[13px] leading-5"
          role="status"
          style={
            matches
              ? { background: "var(--success-tint)", border: "1px solid var(--success-border)", color: "var(--success)" }
              : { background: "var(--warn-tint)", border: "1px solid var(--warn-border)", color: "var(--warn)" }
          }
        >
          {matches ? (
            <p>
              <span className="mr-1.5 text-[16px]" aria-hidden="true">✅</span>
              <b>{si ? "ගණිතය නිවැරදියි!" : "The math checks out!"}</b>{" "}
              {si
                ? `අයිතම එකතුව ලේඛනයේ මුළු මුදලට (${cur}${fmt(printed)}) සමානයි.`
                : `The items match the total printed on the document (${cur}${fmt(printed)}).`}
            </p>
          ) : (
            <p>
              <span className="wiggle mr-1.5 text-[18px]" aria-hidden="true">🕵️</span>
              <b>{si ? "අල්ලා ගත්තා!" : "Caught it!"}</b>{" "}
              {si
                ? `අයිතම එකතුව ${cur}${fmt(subtotal)}, නමුත් ලේඛනයේ ${cur}${fmt(printed)} ලෙස මුද්‍රණය කර ඇත — ${cur}${fmt(diff)} ක වෙනසක්.`
                : `The items add up to ${cur}${fmt(subtotal)}, but the document says ${cur}${fmt(printed)} — a difference of ${cur}${fmt(diff)}.`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
