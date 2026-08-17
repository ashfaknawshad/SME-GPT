// The public base URL of *this* deployment, for building links we email out
// (password reset, 2FA confirm/trust).
//
// This is derived from the incoming request rather than from an env var,
// because an env var has to be set correctly in every environment and silently
// produces broken links when it isn't. It shipped as APP_URL=http://localhost:3000
// on production once already, which sent real users a reset link pointing at
// their own machine.
//
// The request always knows where it actually arrived, so it is the primary
// source and env is only the fallback. Deriving it also means preview
// deployments — which get a fresh URL per branch — work with no configuration.

import type { NextRequest } from "next/server";

/** Trim trailing slashes so `${base}/path` never doubles up. */
function normalize(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

export function resolveAppUrl(req: Request | NextRequest): string {
  // Same-origin fetch() from our own pages always sends Origin, and it already
  // carries the scheme.
  const origin = req.headers.get("origin");
  if (origin) return normalize(origin);

  // Behind Vercel's proxy the original host lands in x-forwarded-host; the bare
  // Host header is the internal one. Prefer the forwarded pair when present.
  const forwardedHost = req.headers.get("x-forwarded-host");
  const host = forwardedHost || req.headers.get("host");
  if (host) {
    const proto =
      req.headers.get("x-forwarded-proto") ??
      (host.startsWith("localhost") || host.startsWith("127.0.0.1")
        ? "http"
        : "https");
    return normalize(`${proto}://${host}`);
  }

  // No usable headers (shouldn't happen for a real request) — fall back to
  // configuration, then to the dev default.
  return normalize(process.env.APP_URL || "http://localhost:3000");
}
