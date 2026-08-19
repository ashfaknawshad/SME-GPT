"use client";

/**
 * useTabSwipe — horizontal swipe navigation between the bottom-nav tabs.
 *
 * ── What this can and cannot do on iOS ──────────────────────────────────────
 * The edge swipe back/forward gesture cannot be disabled from a web page.
 * There is no API for it, in Safari or in standalone mode, and preventDefault()
 * does not suppress it. So rather than fight it, this deliberately ignores any
 * gesture that *starts* within EDGE_GUARD_PX of either side: those keep going
 * to iOS as history back/forward, and everything further in belongs to us.
 * Native apps that offer both gestures split them the same way.
 *
 * ── Not fighting the browser ────────────────────────────────────────────────
 * All listeners are passive and nothing is ever preventDefault()ed. The gesture
 * is only *recognised* on touchend, so vertical scrolling keeps the browser's
 * fast path and a mid-scroll finger is never stolen. A direction lock decides
 * on the first ~10px of movement whether a gesture is ours (horizontal) or the
 * page's (vertical), and once it has gone vertical it can never become a swipe.
 *
 * The one native behaviour this would otherwise trample is a horizontally
 * scrollable region — a wide table, a code block, a zoomed image. Those are
 * detected structurally by walking the ancestor chain, so they keep working
 * without anyone having to remember to annotate them.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Gestures starting this close to a side belong to the OS back/forward swipe. */
const EDGE_GUARD_PX = 28;
/** Movement needed before a gesture commits to being horizontal or vertical. */
const DIRECTION_LOCK_PX = 10;
/** Minimum travel for a deliberate tab change (or less, if flicked quickly). */
const COMMIT_PX = 64;
/** A fast flick counts even when short: px per ms. */
const FLICK_VELOCITY = 0.5;
/** Long presses and slow drags aren't swipes. */
const MAX_DURATION_MS = 800;

/** True if the element or any ancestor can actually scroll sideways. */
function hasHorizontalScrollAncestor(start: Element | null, stopAt: Element): boolean {
  let el: Element | null = start;
  while (el && el !== stopAt) {
    if (el.scrollWidth > el.clientWidth + 1) {
      const overflowX = getComputedStyle(el).overflowX;
      if (overflowX === "auto" || overflowX === "scroll") return true;
    }
    el = el.parentElement;
  }
  return false;
}

function isExcluded(target: Element | null): boolean {
  if (!target) return false;
  // Overlays, sheets and dialogs opt out: a swipe there is aimed at the overlay,
  // not at the page behind it.
  return Boolean(
    target.closest(
      '[data-no-swipe], input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="dialog"], [aria-modal="true"]'
    )
  );
}

/**
 * @param hrefs  Tab routes in on-screen order — the same array the nav renders,
 *               so the swipe order can never drift from the visible order.
 * @param enabled Pass false to leave the gesture off entirely.
 */
export function useTabSwipe(hrefs: string[], enabled = true) {
  const router = useRouter();
  // The nav rebuilds its item array every render, so depending on the array
  // itself would tear down and re-register the listeners on each one. Depending
  // on its contents instead means that happens only when the tabs really change
  // (language switch, admin role resolving).
  const tabKey = hrefs.join("|");

  useEffect(() => {
    const tabs = tabKey.split("|");
    if (!enabled || tabs.length < 2) return;
    // A pointer that can hover is a mouse or trackpad: no swipe navigation on
    // desktop, where it would misfire on text selection and two-finger scrolls.
    if (typeof window === "undefined" || !window.matchMedia("(pointer: coarse)").matches) return;

    const root = document.documentElement;

    let startX = 0;
    let startY = 0;
    let startAt = 0;
    let axis: "none" | "x" | "y" = "none";
    let tracking = false;

    const onTouchStart = (e: TouchEvent) => {
      tracking = false;
      axis = "none";

      if (e.touches.length !== 1) return;             // pinch/zoom is not a swipe
      if (root.dataset.keyboard === "open") return;   // typing, not navigating

      const touch = e.touches[0];
      const fromLeftEdge = touch.clientX <= EDGE_GUARD_PX;
      const fromRightEdge = touch.clientX >= window.innerWidth - EDGE_GUARD_PX;
      if (fromLeftEdge || fromRightEdge) return;      // leave the OS gesture alone

      const target = e.target instanceof Element ? e.target : null;
      if (isExcluded(target)) return;
      if (hasHorizontalScrollAncestor(target, root)) return;

      startX = touch.clientX;
      startY = touch.clientY;
      startAt = e.timeStamp;
      tracking = true;
    };

    const onTouchMove = (e: TouchEvent) => {
      if (!tracking || axis !== "none") return;
      if (e.touches.length !== 1) { tracking = false; return; }

      const dx = e.touches[0].clientX - startX;
      const dy = e.touches[0].clientY - startY;

      // First meaningful movement decides who owns the gesture, and that
      // decision is final — so a diagonal drift mid-scroll can't turn a scroll
      // into a navigation.
      if (Math.abs(dx) > DIRECTION_LOCK_PX && Math.abs(dx) > Math.abs(dy)) axis = "x";
      else if (Math.abs(dy) > DIRECTION_LOCK_PX) { axis = "y"; tracking = false; }
    };

    const onTouchEnd = (e: TouchEvent) => {
      if (!tracking || axis !== "x") { tracking = false; return; }
      tracking = false;

      const touch = e.changedTouches[0];
      if (!touch) return;

      const dx = touch.clientX - startX;
      const elapsed = Math.max(e.timeStamp - startAt, 1);
      if (elapsed > MAX_DURATION_MS) return;

      const far = Math.abs(dx) >= COMMIT_PX;
      const flicked = Math.abs(dx) / elapsed >= FLICK_VELOCITY && Math.abs(dx) > DIRECTION_LOCK_PX * 2;
      if (!far && !flicked) return;

      // Only navigate from a tab root. On /upload or /analysis/x the nav is
      // still on screen, but there is no "current tab" to move away from.
      const index = tabs.indexOf(window.location.pathname);
      if (index === -1) return;

      // Standard pager convention: dragging the page leftward brings in the
      // next tab from the right.
      const next = dx < 0 ? index + 1 : index - 1;
      if (next < 0 || next >= tabs.length) return; // no wraparound at the ends

      // Tells the CSS which way the incoming page should slide in from. Cleared
      // on a timer rather than in effect cleanup, because this component
      // unmounts as part of the very navigation being animated.
      root.dataset.navDir = dx < 0 ? "forward" : "back";
      window.setTimeout(() => {
        if (root.dataset.navDir) delete root.dataset.navDir;
      }, 400);

      router.push(tabs[next]);
    };

    const onTouchCancel = () => { tracking = false; axis = "none"; };

    const opts = { passive: true } as const;
    document.addEventListener("touchstart", onTouchStart, opts);
    document.addEventListener("touchmove", onTouchMove, opts);
    document.addEventListener("touchend", onTouchEnd, opts);
    document.addEventListener("touchcancel", onTouchCancel, opts);

    return () => {
      document.removeEventListener("touchstart", onTouchStart);
      document.removeEventListener("touchmove", onTouchMove);
      document.removeEventListener("touchend", onTouchEnd);
      document.removeEventListener("touchcancel", onTouchCancel);
    };
  }, [tabKey, enabled, router]);
}
