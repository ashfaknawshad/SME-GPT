"use client";

/**
 * Page layout preference: "full" (uses wide screens: dashboard in two columns,
 * list pages in a card grid) or "classic" (the original centred, single-column
 * design). Phones look the same in both.
 *
 * The choice lives on <html data-layout>, set before first paint by the inline
 * script in app/layout.tsx, and every layout rule is plain CSS keyed off it
 * (globals.css, "Page layout"). So "classic" is not an old commit to dig up: it
 * stays in the codebase as a mode and can be switched back on at any time from
 * Settings → Display, or made the default for a deployment with
 * NEXT_PUBLIC_DEFAULT_LAYOUT=classic.
 */

import { useCallback, useSyncExternalStore } from "react";

export type Layout = "full" | "classic";

const STORAGE_KEY = "layout";
const LAYOUT_EVENT = "app-layout-changed";

export const DEFAULT_LAYOUT: Layout =
  process.env.NEXT_PUBLIC_DEFAULT_LAYOUT === "classic" ? "classic" : "full";

function readLayout(): Layout {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "full" || stored === "classic") return stored;
  } catch {
    // Storage blocked (private mode): fall through to the default.
  }
  return DEFAULT_LAYOUT;
}

// Every consumer (and other tabs) stays in step through these two events.
function subscribe(onChange: () => void) {
  window.addEventListener(LAYOUT_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(LAYOUT_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function useLayout() {
  const layout = useSyncExternalStore(subscribe, readLayout, () => DEFAULT_LAYOUT);

  const setLayout = useCallback((next: Layout) => {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Still applies for this page view.
    }
    document.documentElement.setAttribute("data-layout", next);
    window.dispatchEvent(new Event(LAYOUT_EVENT));
  }, []);

  return { layout, setLayout };
}
