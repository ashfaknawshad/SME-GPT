"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { recordNavigation } from "@/lib/navHistory";

/** Mounted once in the root layout; feeds lib/navHistory. Renders nothing. */
export default function NavTracker() {
  const pathname = usePathname();
  useEffect(() => {
    if (pathname) recordNavigation(pathname);
  }, [pathname]);
  return null;
}
