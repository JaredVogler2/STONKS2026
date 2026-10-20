"use client";

import { useEffect, useState } from "react";

const FMT = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit", weekday: "short", hour12: true });

/** Live ET clock in live modes; in DEMO the replay clock is fixed and labelled as such. */
export function EtClock({ initialIso, frozen }: { initialIso: string; frozen: boolean }) {
  const [now, setNow] = useState(() => new Date(initialIso));
  useEffect(() => {
    if (frozen) return;
    const id = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, [frozen]);
  return (
    <time dateTime={now.toISOString()} className="num whitespace-nowrap" suppressHydrationWarning>
      {FMT.format(now)} ET{frozen ? " (replay clock)" : ""}
    </time>
  );
}
