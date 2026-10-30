"use client";

import { useEffect, useState } from "react";
import type { AppLanguage } from "@/lib/i18n";

// Cycled while the assistant works, so a long answer doesn't look frozen.
const PHRASES: { en: string; si: string }[] = [
  { en: "Looking through your documents…", si: "ඔබගේ ලේඛන පරීක්ෂා කරමින්…" },
  { en: "Doing the math…",                 si: "ගණනය කරමින්…" },
  { en: "Writing the answer…",             si: "පිළිතුර ලියමින්…" },
];

export default function TypingIndicator({ lang }: { lang: AppLanguage }) {
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setIdx((i) => (i + 1) % PHRASES.length), 2200);
    return () => clearInterval(id);
  }, []);

  return (
    <div
      className="fade-up flex items-center gap-3 rounded-2xl px-4 py-3 text-[13px]"
      role="status"
      style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}
    >
      <span className="flex items-center gap-1" aria-hidden="true">
        <span className="typing-dot" />
        <span className="typing-dot" />
        <span className="typing-dot" />
      </span>
      <span key={idx} className="fade-up">{PHRASES[idx][lang === "si" ? "si" : "en"]}</span>
    </div>
  );
}
