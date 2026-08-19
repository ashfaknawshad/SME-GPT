"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import BottomNav from "@/components/layout/BottomNav";
import LanguageSwitcher from "@/components/layout/LanguageSwitcher";
import { getSession } from "@/lib/auth";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";

type AdminUser = {
  id: string;
  email: string;
  fullName: string | null;
  role: string;
  createdAt: string;
};

type AuditLog = {
  id: string;
  type: string;
  content: string;
  createdAt: string;
  user: { email: string } | null;
};

const ROLES = ["owner", "accountant", "admin", "auditor"];

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="overflow-hidden rounded-2xl shadow-sm"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      {children}
    </div>
  );
}

export default function AdminPage() {
  const router = useRouter();
  const [lang, setLang] = useState<AppLanguage>("en");
  const [authorized, setAuthorized] = useState(false);
  const [checking, setChecking] = useState(true);
  const [tab, setTab] = useState<"users" | "logs">("users");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => { setLang(getStoredLanguage()); }, []);
  const t = ui[lang];

  useEffect(() => {
    const check = async () => {
      const session = await getSession();
      if (!session) {
        router.push("/login");
        return;
      }
      const res = await fetch("/api/auth/me", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (data?.user?.role !== "admin") {
        router.push("/dashboard");
        return;
      }
      setAuthorized(true);
      setChecking(false);
    };
    check();
  }, [router]);

  const loadUsers = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/users", { cache: "no-store" });
      const data = await res.json();
      if (res.ok) setUsers(data.users || []);
    } finally {
      setLoading(false);
    }
  };

  const loadLogs = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/audit-logs", { cache: "no-store" });
      const data = await res.json();
      if (res.ok) setLogs(data.logs || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!authorized) return;
    if (tab === "users") loadUsers();
    else loadLogs();
  }, [authorized, tab]);

  const handleRoleChange = async (userId: string, role: string) => {
    setMessage("");
    const res = await fetch("/api/admin/users", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ userId, role }),
    });
    const data = await res.json();
    if (!res.ok) {
      setMessage(data.error || t.adminRoleFailed);
      return;
    }
    setUsers((prev) => prev.map((u) => (u.id === userId ? { ...u, role: data.user.role } : u)));
  };

  if (checking || !authorized) return null;

  return (
    <MobileShell>
      <div className="pad-nav" style={{ background: "var(--bg)" }}>
        <main className="mx-auto w-full max-w-[900px] px-4 py-6 sm:px-6">
          <div className="mb-5 flex items-center justify-between gap-3">
            <h1 className="text-[22px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[26px]">
              {t.adminPanel}
            </h1>
            <LanguageSwitcher />
          </div>

          <div className="mb-5 flex gap-2">
            <button
              type="button"
              onClick={() => setTab("users")}
              className="rounded-xl px-4 py-2 text-[13px] font-bold transition"
              style={{
                background: tab === "users" ? "var(--brand)" : "var(--surface)",
                color: tab === "users" ? "#fff" : "var(--text-2)",
                border: "1px solid var(--border)",
              }}
            >
              {t.adminUsers}
            </button>
            <button
              type="button"
              onClick={() => setTab("logs")}
              className="rounded-xl px-4 py-2 text-[13px] font-bold transition"
              style={{
                background: tab === "logs" ? "var(--brand)" : "var(--surface)",
                color: tab === "logs" ? "#fff" : "var(--text-2)",
                border: "1px solid var(--border)",
              }}
            >
              {t.auditLogs}
            </button>
          </div>

          {message && (
            <p
              className="mb-4 rounded-xl px-4 py-2.5 text-center text-[13px] font-medium"
              style={{ background: "rgba(220,38,38,0.08)", color: "#dc2626" }}
            >
              {message}
            </p>
          )}

          {tab === "users" && (
            <Card>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-[13px]">
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      <th className="px-5 py-3 text-[var(--text-3)]">{t.thName}</th>
                      <th className="px-5 py-3 text-[var(--text-3)]">{t.thEmail}</th>
                      <th className="px-5 py-3 text-[var(--text-3)]">{t.thRole}</th>
                      <th className="px-5 py-3 text-[var(--text-3)]">{t.thJoined}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td className="px-5 py-3 font-medium text-[var(--text-1)]">{u.fullName || "—"}</td>
                        <td className="px-5 py-3 text-[var(--text-2)]">{u.email}</td>
                        <td className="px-5 py-3">
                          <select
                            value={u.role}
                            onChange={(e) => handleRoleChange(u.id, e.target.value)}
                            className="field-input h-9 rounded-xl border px-2 text-[13px]"
                          >
                            {ROLES.map((r) => (
                              <option key={r} value={r}>{r}</option>
                            ))}
                          </select>
                        </td>
                        <td className="px-5 py-3 text-[var(--text-3)]">
                          {new Date(u.createdAt).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                    {!loading && users.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-5 py-6 text-center text-[var(--text-3)]">
                          {t.noUsersFound}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {tab === "logs" && (
            <Card>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-[13px]">
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      <th className="px-5 py-3 text-[var(--text-3)]">{t.thTime}</th>
                      <th className="px-5 py-3 text-[var(--text-3)]">{t.thUser}</th>
                      <th className="px-5 py-3 text-[var(--text-3)]">{t.thEvent}</th>
                      <th className="px-5 py-3 text-[var(--text-3)]">{t.thDetail}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {logs.map((log) => (
                      <tr key={log.id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td className="px-5 py-3 whitespace-nowrap text-[var(--text-3)]">
                          {new Date(log.createdAt).toLocaleString()}
                        </td>
                        <td className="px-5 py-3 text-[var(--text-2)]">{log.user?.email || "—"}</td>
                        <td className="px-5 py-3 font-medium text-[var(--text-1)]">{log.type}</td>
                        <td className="px-5 py-3 text-[var(--text-3)]">{log.content}</td>
                      </tr>
                    ))}
                    {!loading && logs.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-5 py-6 text-center text-[var(--text-3)]">
                          {t.noAuditLogs}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </main>

        <BottomNav />
      </div>
    </MobileShell>
  );
}
