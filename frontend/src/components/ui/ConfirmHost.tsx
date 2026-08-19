"use client";

// Renders the themed dialog for confirmDialog() / noticeDialog() (see
// lib/confirm.ts). Mounted once in the root layout.

import { useCallback, useEffect, useRef, useState } from "react";
import { _registerConfirmHost, ConfirmRequest } from "@/lib/confirm";
import { getStoredLanguage } from "@/lib/i18n";

export default function ConfirmHost() {
  const [queue, setQueue] = useState<ConfirmRequest[]>([]);
  const current = queue[0] ?? null;
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const lastFocused = useRef<HTMLElement | null>(null);
  const [lang, setLang] = useState<"en" | "si">("en");

  useEffect(() => { setLang(getStoredLanguage()); }, []);

  useEffect(() => {
    _registerConfirmHost((req) => {
      if (req) setQueue((q) => [...q, req]);
    });
    return () => _registerConfirmHost(null);
  }, []);

  const settle = useCallback((confirmed: boolean) => {
    setQueue((q) => {
      const [head, ...rest] = q;
      head?.resolve(confirmed);
      return rest;
    });
  }, []);

  // Focus management: focus the confirm button on open, restore focus on close.
  useEffect(() => {
    if (!current) return;
    lastFocused.current = document.activeElement as HTMLElement | null;
    confirmBtnRef.current?.focus();
    return () => lastFocused.current?.focus?.();
  }, [current]);

  // ESC cancels, Enter confirms (only when the dialog owns focus).
  useEffect(() => {
    if (!current) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); settle(false); }
      else if (e.key === "Enter") { e.preventDefault(); settle(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, settle]);

  if (!current) return null;

  const isDanger = current.variant === "danger";
  const confirmLabel = current.confirmLabel ?? (lang === "si" ? "හරි" : "OK");
  const cancelLabel = current.cancelLabel ?? (lang === "si" ? "අවලංගු කරන්න" : "Cancel");

  return (
    <div
      data-no-swipe
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.45)", height: "var(--app-height)" }}
      onClick={() => settle(false)}
      role="presentation"
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby={current.message ? "confirm-message" : undefined}
        className="w-full max-w-[400px] rounded-2xl p-6 shadow-xl"
        style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-title" className="text-[17px] font-extrabold text-[var(--text-1)]">
          {current.title}
        </h2>
        {current.message && (
          <p id="confirm-message" className="mt-2 text-[14px] leading-6 text-[var(--text-2)]">
            {current.message}
          </p>
        )}
        <div className="mt-6 flex flex-col-reverse gap-2.5 sm:flex-row sm:justify-end">
          {!current.noticeOnly && (
            <button
              onClick={() => settle(false)}
              className="rounded-xl px-5 py-2.5 text-[14px] font-semibold transition hover:opacity-80"
              style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}
            >
              {cancelLabel}
            </button>
          )}
          <button
            ref={confirmBtnRef}
            onClick={() => settle(true)}
            className="rounded-xl px-5 py-2.5 text-[14px] font-bold text-white transition hover:opacity-90"
            // Destructive actions use a solid red-600 rather than var(--danger):
            // the token is tuned to read *on* a tint, and its dark value is too
            // light to carry white button text. #dc2626 clears AA with white in
            // both themes, matching the existing delete buttons in the app.
            style={{ background: isDanger ? "#dc2626" : "var(--brand)" }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
