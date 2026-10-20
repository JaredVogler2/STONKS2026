"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-renders server data on an interval while the tab is visible. Tuned for free tiers: 60s default, never sub-15s. */
export function AutoRefresh({ seconds = 60 }: { seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    const ms = Math.max(15, seconds) * 1000;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, ms);
    return () => window.clearInterval(id);
  }, [router, seconds]);
  return null;
}
