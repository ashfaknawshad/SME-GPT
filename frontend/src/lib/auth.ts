export type SessionUser = {
  id: string;
  email: string;
  fullName: string;
  companyName?: string;
  token?: string;
};

export async function loginUser(email: string, password: string) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email, password }),
  });

  const data = await res.json().catch(() => ({}));

  if (res.ok && data?.token) {
    localStorage.setItem("token", data.token);
  }

  return {
    ok: res.ok,
    data,
  };
}

export async function signupUser(data: {
  fullName: string;
  companyName: string;
  email: string;
  password: string;
}) {
  const res = await fetch("/api/auth/signup", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });

  return res.ok;
}

export async function getSession(): Promise<SessionUser | null> {
  const res = await fetch("/api/auth/me", {
    method: "GET",
    cache: "no-store",
  });

  if (!res.ok) return null;

  const data = await res.json();

  // The httpOnly cookie (checked server-side by /api/auth/me) is the source
  // of truth for whether you're logged in. localStorage is a separate copy
  // used for direct Bearer-token calls to the FastAPI backend, and can be
  // empty even with a valid cookie session (e.g. the login page redirects
  // straight to /dashboard when the cookie is still valid, skipping the
  // login form that would normally set it). Re-sync it here whenever the
  // server hands back a token, so it never falls out of sync with the
  // cookie that's actually authenticating you.
  if (typeof window !== "undefined" && data.token) {
    localStorage.setItem("token", data.token);
  }

  const token =
    typeof window !== "undefined"
      ? localStorage.getItem("token") || sessionStorage.getItem("token") || ""
      : "";

  return {
    ...data.user,
    token,
  };
}

export async function logoutUser() {
  localStorage.removeItem("token");
  sessionStorage.removeItem("token");

  await fetch("/api/auth/logout", {
    method: "POST",
  });

  // Leave via a real document navigation rather than router.push(), for two
  // reasons: replace() drops the page we're leaving from the history stack so
  // Back can't return to it, and a full load discards the client router cache
  // holding the rendered dashboard (and the React state with the previous
  // user's figures in it). Callers should not navigate after awaiting this.
  if (typeof window !== "undefined") {
    window.location.replace("/login");
  }
}

export function clearAllDummyAuth() {
  localStorage.removeItem("dummyUser");
  localStorage.removeItem("token");
  localStorage.removeItem("isLoggedIn");
  sessionStorage.removeItem("token");
}

export function getStoredToken() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}