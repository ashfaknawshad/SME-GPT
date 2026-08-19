"use client";

/**
 * ViewportManager — the single place that knows how much screen the app has.
 *
 * Mounted once in the root layout. Renders nothing and holds no React state:
 * every measurement is written straight to CSS custom properties on <html>, so
 * a keyboard animation repaints CSS but never re-renders a single component.
 *
 * ── Why a probe instead of window.innerHeight ────────────────────────────────
 * The naive keyboard check is `innerHeight - visualViewport.height`. It is
 * wrong on iOS: Safari's layout viewport stays at the *large* height while the
 * URL bar and toolbar are visible, so that difference is already ~110px with no
 * keyboard anywhere. Anything built on it either fires constantly or needs a
 * hardcoded "keyboards are taller than N px" threshold.
 *
 * Instead we measure a hidden element sized `100svh` — the exact height the app
 * shell is laid out at — and compare the visible viewport against that:
 *
 *     keyboardInset = svhPx - (visualViewport.height + visualViewport.offsetTop)
 *
 *   • iOS, toolbars shown .... visual ≈ svh          → 0
 *   • iOS, toolbars collapsed  visual > svh          → negative, clamped to 0
 *   • iOS, keyboard up ....... visual ≪ svh          → real overlap, in px
 *   • Android (resizes-content) svh itself shrank    → ~0, shell already resized
 *   • Android w/o that support visual ≪ svh          → real overlap, in px
 *
 * Same formula everywhere. No user-agent sniffing, no assumed keyboard height,
 * no assumed device size — it reports whatever the engine actually did.
 */

import { useEffect } from "react";

/** Below this, a delta is browser-chrome jitter or sub-pixel rounding, not a
 *  keyboard. It is a noise floor, not a keyboard height — the actual inset is
 *  always the measured value. */
const NOISE_FLOOR_PX = 40;

export default function ViewportManager() {
  useEffect(() => {
    const root = document.documentElement;

    // The svh probe lives in the DOM rather than in the React tree so it can
    // never be caught in a re-render or unmounted by a route transition.
    const probe = document.createElement("div");
    probe.id = "__svh_probe";
    probe.setAttribute("aria-hidden", "true");
    document.body.appendChild(probe);

    const vv = window.visualViewport ?? null;

    let frame = 0;
    let lastInset = -1;
    let keyboardOpen = false;

    const measure = () => {
      frame = 0;

      // Without VisualViewport we simply never report an inset: --keyboard-inset
      // stays 0px and the layout falls back to pure CSS (100svh, plus whatever
      // the engine does to the layout viewport). Degraded, never broken.
      if (!vv) return;

      const svhPx = probe.offsetHeight;
      if (svhPx <= 0) return; // not laid out yet (or in a hidden tab)

      const visible = vv.height + vv.offsetTop;
      const raw = svhPx - visible;
      const inset = raw > NOISE_FLOOR_PX ? Math.round(raw) : 0;

      // Only touch the DOM when the value actually moved. Keyboard animations
      // fire this handler every frame with a changing height; skipping equal
      // writes keeps the style recalc to the frames that matter.
      if (inset !== lastInset) {
        lastInset = inset;
        root.style.setProperty("--keyboard-inset", `${inset}px`);
      }

      const open = inset > 0;
      if (open !== keyboardOpen) {
        keyboardOpen = open;
        root.dataset.keyboard = open ? "open" : "closed";

        // iOS sometimes leaves the *document* scrolled after dismissing the
        // keyboard, which reads as a stale offset / blank strip at the top.
        // Only correct it when the document has nothing to scroll anyway, so a
        // genuinely scrolled page is never yanked back to the top.
        if (!open && root.scrollHeight <= root.clientHeight + 1) {
          window.scrollTo(0, 0);
        }
      }
    };

    // Frame-synchronised: coalesces the burst of resize/scroll events an
    // opening keyboard produces into at most one measurement per paint.
    const schedule = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(measure);
    };

    // Orientation changes report the *old* dimensions if read immediately —
    // remeasure on the next frame and once more after the rotation settles, so
    // no stale portrait height survives into landscape.
    const onOrientation = () => {
      schedule();
      window.setTimeout(schedule, 300);
    };

    vv?.addEventListener("resize", schedule);
    vv?.addEventListener("scroll", schedule);
    window.addEventListener("resize", schedule);
    window.addEventListener("orientationchange", onOrientation);
    screen.orientation?.addEventListener?.("change", onOrientation);

    root.dataset.keyboard = "closed";
    measure();

    return () => {
      if (frame) cancelAnimationFrame(frame);
      vv?.removeEventListener("resize", schedule);
      vv?.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      window.removeEventListener("orientationchange", onOrientation);
      screen.orientation?.removeEventListener?.("change", onOrientation);
      probe.remove();
      root.style.removeProperty("--keyboard-inset");
      delete root.dataset.keyboard;
    };
  }, []);

  return null;
}
