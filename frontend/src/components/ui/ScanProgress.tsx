"use client";

import { useEffect, useState } from "react";
import type { AppLanguage } from "@/lib/i18n";

// The four stages /process-document-stream reports (its `step` field), worded
// so a first-time viewer can follow what the machine is doing.
const STEPS: { emoji: string; en: string; si: string }[] = [
  { emoji: "📷", en: "Opening the picture",       si: "පින්තූරය විවෘත කරමින්" },
  { emoji: "👀", en: "Reading every word",        si: "සෑම වචනයක්ම කියවමින්" },
  { emoji: "✏️", en: "Fixing spelling mistakes",  si: "අකුරු වැරදි නිවැරදි කරමින්" },
  { emoji: "🧠", en: "Understanding the numbers", si: "ඉලක්කම් තේරුම් ගනිමින්" },
];

type ScanProgressProps = {
  /** Current backend step, 1–4. */
  activeStep: number;
  lang: AppLanguage;
};

export default function ScanProgress({ activeStep, lang }: ScanProgressProps) {
  const si = lang === "si";
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  const pct = Math.min(Math.max(activeStep - 1, 0) / STEPS.length, 1) * 100;

  return (
    <div
      className="mt-6 rounded-2xl px-5 py-5"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div className="flex items-center justify-between">
        <p className="text-[15px] font-extrabold text-[var(--text-1)]">
          {si ? "AI ඔබගේ ලේඛනය කියවමින්…" : "The AI is reading your document…"}
        </p>
        <span className="text-[12px] font-semibold tabular-nums text-[var(--text-3)]">⏱ {seconds}s</span>
      </div>

      <div className="mt-3 h-2 overflow-hidden rounded-full" style={{ background: "var(--brand-tint)" }}>
        <div
          className="h-full rounded-full transition-[width] duration-700 ease-out"
          style={{ width: `${Math.max(pct, 6)}%`, background: "linear-gradient(90deg, #2252b5, #38bdf8)" }}
        />
      </div>

      <ol className="mt-4 space-y-2.5">
        {STEPS.map((s, i) => {
          const n = i + 1;
          const done = activeStep > n;
          const active = activeStep === n;
          return (
            <li
              key={s.en}
              className="flex items-center gap-3 rounded-xl px-3 py-2 transition-all duration-300"
              style={{
                background: active ? "var(--brand-tint)" : "transparent",
                opacity: done || active ? 1 : 0.45,
              }}
            >
              <span className="text-[20px]" aria-hidden="true">{s.emoji}</span>
              <span
                className={`min-w-0 flex-1 text-[14px] ${active ? "font-bold" : "font-medium"}`}
                style={{ color: active ? "var(--brand-mid)" : "var(--text-1)" }}
              >
                {si ? s.si : s.en}
              </span>
              {done ? (
                <span
                  className="material-symbols-outlined pop-in text-[20px]"
                  style={{ color: "var(--success)" }}
                  aria-label={si ? "සම්පූර්ණයි" : "done"}
                >
                  check_circle
                </span>
              ) : active ? (
                <span
                  className="h-4 w-4 animate-spin rounded-full"
                  style={{ border: "2px solid var(--brand-tint)", borderTopColor: "var(--brand-mid)" }}
                />
              ) : null}
            </li>
          );
        })}
      </ol>

      <p className="mt-3 text-[12px] text-[var(--text-2)]">
        {si
          ? "මෙයට මොහොතක් ගත විය හැක — කරුණාකර මෙම පිටුව විවෘතව තබන්න."
          : "This can take a moment — please keep this page open."}
      </p>
    </div>
  );
}
