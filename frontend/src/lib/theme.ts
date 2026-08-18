"use client";

import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

// Every useTheme() consumer keeps its own React state, so a change made by one
// (e.g. the Settings night-mode toggle) has to be broadcast to the others
// (e.g. the header ThemeToggle) or they render a stale icon until reload. All
// instances listen for this event and re-read the shared source of truth.
const THEME_EVENT = "app-theme-changed";

function readTheme(): Theme {
  const stored = localStorage.getItem("theme") as Theme | null;
  return (
    stored ||
    (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
  );
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("light");

  useEffect(() => {
    const initial = readTheme();
    setThemeState(initial);
    document.documentElement.setAttribute("data-theme", initial);

    // Sync when ANY other useTheme() instance (or another tab) changes it.
    const sync = () => setThemeState(readTheme());
    window.addEventListener(THEME_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(THEME_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    localStorage.setItem("theme", next);
    document.documentElement.setAttribute("data-theme", next);
    window.dispatchEvent(new Event(THEME_EVENT)); // notify every other instance
  }, []);

  const toggle = useCallback(() => {
    // Read the live value from the DOM rather than this instance's (possibly
    // stale) `theme` closure, so two toggles never disagree about "current".
    const current =
      (document.documentElement.getAttribute("data-theme") as Theme) || readTheme();
    setTheme(current === "dark" ? "light" : "dark");
  }, [setTheme]);

  return { theme, toggle };
}
