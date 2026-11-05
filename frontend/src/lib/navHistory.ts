"use client";

/**
 * In-app navigation history, so a "Back" button can tell whether there is an
 * earlier page *inside this app* to return to.
 *
 * window.history.length can't answer that: it also counts pages from other
 * sites, and it is 1 when the installed PWA cold-starts. A plain router.back()
 * would then either leave the app or do nothing. We keep our own stack of
 * pathnames in sessionStorage (per tab, survives reloads):
 *   • a new pathname equal to the one *before* the top  → it was a Back: pop
 *   • any other new pathname                            → push
 */

const KEY = "app-nav-stack";
const MAX = 50;

function read(): string[] {
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

function write(stack: string[]) {
  try { sessionStorage.setItem(KEY, JSON.stringify(stack.slice(-MAX))); } catch { /* storage blocked */ }
}

export function recordNavigation(pathname: string) {
  const stack = read();
  const top = stack[stack.length - 1];
  if (top === pathname) return;                    // re-render / same page
  if (stack[stack.length - 2] === pathname) stack.pop(); // went back
  else stack.push(pathname);
  write(stack);
}

/** True when there is an earlier in-app page to go back to. */
export function canGoBack(): boolean {
  return read().length > 1;
}
