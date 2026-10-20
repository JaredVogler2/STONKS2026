"use client";

import { Eye, EyeOff, Moon, Printer, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { buttonClass } from "./action-button";

function setCookie(name: string, value: string) {
  document.cookie = `${name}=${value}; Path=/; Max-Age=31536000; SameSite=Strict${location.protocol === "https:" ? "; Secure" : ""}`;
}

export function ThemeToggle({ initial }: { initial: "dark" | "light" }) {
  const [theme, setTheme] = useState(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button type="button" className={buttonClass("ghost", "min-h-8 px-2")} aria-label={`Switch to ${next} theme`}
      onClick={() => { setCookie("apex_theme", next); setTheme(next); }}>
      {theme === "dark" ? <Sun aria-hidden="true" className="size-4" /> : <Moon aria-hidden="true" className="size-4" />}
    </button>
  );
}

/** Privacy-safe screenshot mode: blurs every element marked `.sensitive` (quantities, costs, P&L). */
export function PrivacyToggle({ initial }: { initial: boolean }) {
  const [on, setOn] = useState(initial);
  useEffect(() => {
    document.documentElement.dataset.privacy = on ? "on" : "off";
  }, [on]);
  return (
    <button type="button" aria-pressed={on} className={buttonClass("ghost", "min-h-8 px-2")}
      aria-label={on ? "Show personal values" : "Hide personal values (screenshot mode)"} title={on ? "Screenshot mode on" : "Screenshot mode"}
      onClick={() => { setCookie("apex_privacy", on ? "off" : "on"); setOn(!on); }}>
      {on ? <EyeOff aria-hidden="true" className="size-4" /> : <Eye aria-hidden="true" className="size-4" />}
    </button>
  );
}

export function PrintButton({ label = "Print / PDF" }: { label?: string }) {
  return (
    <button type="button" className={buttonClass("secondary")} onClick={() => window.print()}>
      <Printer aria-hidden="true" className="size-4" />
      {label}
    </button>
  );
}
