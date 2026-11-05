"use client";

/**
 * Text size preference (Settings → Preferences → Text size).
 *
 * Scales the whole interface proportionally through the --ui-scale CSS
 * variable (see globals.css). Stored per device, like the theme, and applied
 * before first paint by the inline script in app/layout.tsx — keep the scale
 * values there in sync with TEXT_SIZES below.
 */

export type TextSize = "compact" | "default" | "large" | "xlarge";

export const TEXT_SIZES: { key: TextSize; scale: number; en: string; si: string }[] = [
  { key: "compact", scale: 0.9, en: "Compact",     si: "සංක්ෂිප්ත" },
  { key: "default", scale: 1,   en: "Default",     si: "සාමාන්‍ය" },
  { key: "large",   scale: 1.1, en: "Large",       si: "විශාල" },
  { key: "xlarge",  scale: 1.2, en: "Extra Large", si: "ඉතා විශාල" },
];

const STORAGE_KEY = "text-size";

export function getStoredTextSize(): TextSize {
  try {
    const v = localStorage.getItem(STORAGE_KEY) as TextSize | null;
    if (v && TEXT_SIZES.some((s) => s.key === v)) return v;
  } catch { /* private mode / blocked storage */ }
  return "default";
}

export function applyTextSize(size: TextSize) {
  const scale = TEXT_SIZES.find((s) => s.key === size)?.scale ?? 1;
  const root = document.documentElement;
  if (scale === 1) root.style.removeProperty("--ui-scale");
  else root.style.setProperty("--ui-scale", String(scale));
}

export function setStoredTextSize(size: TextSize) {
  try {
    if (size === "default") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, size);
  } catch { /* still apply for this session */ }
  applyTextSize(size);
}
