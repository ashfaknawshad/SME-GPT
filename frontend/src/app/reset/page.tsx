"use client";

import { useRouter } from "next/navigation";
import MobileShell from "@/components/layout/MobileShell";
import { clearAllDummyAuth } from "@/lib/auth";

export default function ResetPage() {
  const router = useRouter();

  const handleReset = () => {
    clearAllDummyAuth();
    sessionStorage.removeItem("query_result");
    sessionStorage.removeItem("selected_query_history");
    router.push("/dashboard");
  };

  return (
    <MobileShell>
      <div className="mx-auto flex min-h-svh w-full max-w-[700px] items-center justify-center px-4 py-8">
        <div
          className="w-full rounded-[20px] p-6 shadow-sm"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
        >
          <button
            onClick={() => router.push("/profile")}
            className="mb-4 text-[14px] font-medium text-[var(--brand-mid)]"
          >
            ← Back
          </button>

          <h1 className="text-[22px] font-bold text-[var(--text-1)]">
            Reset Dummy Auth
          </h1>

          <p className="mt-3 text-[14px] leading-7 text-[var(--text-2)]">
            This will remove the temporary signup user, login session, and local temporary query state from the browser.
          </p>

          <button
            onClick={handleReset}
            className="mt-6 h-11 w-full rounded-[16px] text-[14px] font-semibold"
            style={{ background: "var(--danger)", color: "var(--surface)" }}
          >
            Clear Dummy Data
          </button>
        </div>
      </div>
    </MobileShell>
  );
}