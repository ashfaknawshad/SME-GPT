// Server-side route guard. (Next 16 renamed `middleware.ts` to `proxy.ts`;
// same functionality, and it defaults to the Node.js runtime.)
//
// Every protected page also guards itself client-side by calling getSession()
// on mount, but that alone leaves two holes:
//
//   1. The page renders before the effect runs, so a logged-out visitor sees a
//      flash of the dashboard shell.
//   2. Back-navigation after logout restores the previous render from the
//      client router cache without remounting, so the effect never re-runs and
//      the *previous user's* figures stay on screen until something is clicked.
//
// This runs before the page is served and redirects unauthenticated requests,
// closing both. Per the Next docs this is deliberately an *optimistic* check —
// cookie signature and expiry only, no database round-trip, because proxy runs
// on every request including prefetches. Real authorization stays where it was:
// getAuthenticatedUser() in /api/auth/me (which also checks sessionVersion
// against the DB) and JWT verification in the FastAPI backend.

import { NextResponse, type NextRequest } from "next/server";
import * as jwt from "jsonwebtoken";

// Everything not listed here requires a session. Prefix match, so
// "/login" also covers "/login/verify".
const PUBLIC_ROUTES = [
  "/login",
  "/signup",
  "/forgot-password",
  "/reset-password",
  "/shared", // IT-45 public read-only document view
];

function isPublic(pathname: string): boolean {
  // "/" is a server-side redirect to /login, so let it through.
  if (pathname === "/") return true;
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );
}

function hasValidSession(req: NextRequest): boolean {
  const token = req.cookies.get("token")?.value;
  if (!token) return false;

  const secret = process.env.JWT_SECRET;
  // Without a secret we can't verify anything. Fall back to presence so a
  // misconfigured env locks nobody out — the API layer still rejects the call.
  if (!secret) return true;

  try {
    jwt.verify(token, secret); // throws on bad signature or expiry
    return true;
  } catch {
    return false;
  }
}

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (isPublic(pathname)) return NextResponse.next();

  if (!hasValidSession(req)) {
    const loginUrl = new URL("/login", req.nextUrl);
    // So the login page can send them back where they were headed.
    loginUrl.searchParams.set("next", pathname + req.nextUrl.search);
    return NextResponse.redirect(loginUrl);
  }

  const res = NextResponse.next();
  // Keeps authenticated pages out of the browser's back/forward cache. Without
  // this, Back restores a frozen copy of the page from memory with no network
  // request at all, so none of the above would run on that navigation.
  res.headers.set("Cache-Control", "no-store, must-revalidate");
  return res;
}

export const config = {
  // Skip API routes (they do their own auth and must be able to return 401
  // rather than a redirect), Next internals, and static assets.
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|gif|svg|webp|ico|woff2?)$).*)",
  ],
};
