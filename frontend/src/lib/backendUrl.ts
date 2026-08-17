// Where the frontend should send API calls.
//
// NEXT_PUBLIC_* env vars are inlined at *build* time, so a deployment is pinned
// to whatever backend URL was set when it was built. That's fine now that the
// backend has a permanent home — set NEXT_PUBLIC_BACKEND_URL in the deployment
// environment (and in .env.local for local dev) and redeploy to change it.
//
// This used to also support a localStorage override, settable from Settings →
// Preferences, because the backend ran on a laptop behind an ephemeral
// cloudflared tunnel whose URL changed every restart. That's gone.

const ENV_DEFAULT = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8000";

/** Trim whitespace and any trailing slashes so `${base}/path` never doubles up. */
function normalize(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

/** The backend base URL to call. */
export function resolveBackendUrl(): string {
  return normalize(ENV_DEFAULT);
}
