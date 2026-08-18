"use client";

// Dashboard widget: this month's actuals vs. the monthly targets the user set
// in Settings → Budget. Actuals come from GET /user/budget-vs-actual, computed
// deterministically on the backend from the tenant's records (same income/
// expense/cash flow mapping the rest of the app uses).

import { formatMoney } from "@/lib/format";
import type { AppLanguage } from "@/lib/i18n";

export type BudgetCategory = {
  category: string;
  budget: number;
  actual: number;
  is_spend: boolean;
};

// Friendlier bilingual labels than the raw stored category keys.
const LABELS: Record<string, { en: string; si: string }> = {
  "Revenue":       { en: "Revenue",      si: "ආදායම" },
  "Budgeted Cost": { en: "Costs",        si: "වියදම්" },
  "Cash Inflow":   { en: "Cash Inflow",  si: "මුදල් ලැබීම්" },
  "Cash Outflow":  { en: "Cash Outflow", si: "මුදල් ගෙවීම්" },
};

function Row({ c, currency, lang }: { c: BudgetCategory; currency: string; lang: AppLanguage }) {
  const label = LABELS[c.category] ?? { en: c.category, si: c.category };
  const pct = c.budget > 0 ? (c.actual / c.budget) * 100 : 0;
  const over = c.actual > c.budget;

  // Spend categories (costs, cash out): staying under target is good (green),
  // exceeding it is bad (red). Income categories (revenue, cash in): progress
  // toward the target — hitting/beating it is good (green), still short is
  // neutral brand blue.
  const color = c.is_spend
    ? (over ? "#dc2626" : "#16a34a")
    : (pct >= 100 ? "#16a34a" : "var(--brand-mid)");

  const barWidth = Math.min(100, Math.max(2, pct));

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <p className="text-[13px] font-semibold text-[var(--text-1)]">
          {lang === "si" ? label.si : label.en}
        </p>
        <p className="text-[12px] font-bold" style={{ color }}>
          {formatMoney(c.actual, currency)}
          <span className="font-medium text-[var(--text-3)]">
            {" / "}{formatMoney(c.budget, currency)}
          </span>
        </p>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full" style={{ background: "var(--surface-2)" }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${barWidth}%`, background: color }} />
      </div>
      <p className="mt-1 text-[11px] font-medium" style={{ color: "var(--text-3)" }}>
        {c.is_spend && over
          ? (lang === "si"
              ? `අයවැය ඉක්මවා ${formatMoney(c.actual - c.budget, currency)}`
              : `${formatMoney(c.actual - c.budget, currency)} over budget`)
          : (lang === "si"
              ? `අයවැයෙන් ${Math.round(pct)}%`
              : `${Math.round(pct)}% of target`)}
      </p>
    </div>
  );
}

export default function BudgetVsActual({
  categories, currency, lang,
}: {
  categories: BudgetCategory[];
  currency: string;
  lang: AppLanguage;
}) {
  if (!categories || categories.length === 0) return null;

  return (
    <div className="rounded-2xl p-5" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="mb-4 flex items-center gap-2">
        <span className="material-symbols-outlined text-[18px]" style={{ color: "var(--brand-mid)" }}>savings</span>
        <p className="text-[14px] font-extrabold text-[var(--text-1)]">
          {lang === "si" ? "අයවැය එදිරිව සත්‍ය (මේ මාසය)" : "Budget vs Actual (this month)"}
        </p>
      </div>
      <div className="space-y-4">
        {categories.map((c) => (
          <Row key={c.category} c={c} currency={currency} lang={lang} />
        ))}
      </div>
    </div>
  );
}
