"use client";

import { use, useEffect, useState } from "react";
import MobileShell from "@/components/layout/MobileShell";
import { AppLanguage, getStoredLanguage, ui } from "@/lib/i18n";

export default function LoginVerifyClient({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const params = use(searchParams);
  const verificationToken = params.token || "";

  const [lang, setLang] = useState<AppLanguage>("en");
  const [message, setMessage] = useState("");

  useEffect(() => { setLang(getStoredLanguage()); }, []);
  const t = ui[lang];

  useEffect(() => {
    if (!verificationToken) {
      setMessage(t.lvInvalidLink);
      return;
    }

    setMessage(t.lvWaiting);

    const interval = setInterval(async () => {
      try {
        const statusRes = await fetch("/api/auth/verification-status", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ verificationToken }),
        });

        const statusData = await statusRes.json();

        if (statusRes.ok && statusData.approved) {
          setMessage(t.lvApproved);

          const completeRes = await fetch("/api/auth/complete-login", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ verificationToken }),
          });

          if (completeRes.ok) {
            clearInterval(interval);
            window.location.href = "/dashboard";
          }
        }
      } catch (error) {
        console.error("VERIFY PAGE ERROR:", error);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [verificationToken, t]);

  return (
    <MobileShell>
      <div className="px-4 py-8" style={{ background: "var(--bg)" }}>
        <div
          className="mx-auto max-w-[520px] rounded-[30px] p-8 shadow-sm"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
        >
          <h1 className="text-center text-[28px] font-extrabold text-[var(--text-1)]">
            {t.lvTitle}
          </h1>

          <p className="mt-4 text-center text-[14px] text-[var(--text-2)]">
            {message}
          </p>

          <p className="mt-6 text-center text-[13px] text-[var(--brand-mid)]">
            {t.lvCheckEmail}
          </p>
        </div>
      </div>
    </MobileShell>
  );
}
