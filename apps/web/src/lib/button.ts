import { cn } from "./cn";

export type Variant = "primary" | "secondary" | "danger" | "ghost";

export const BUTTON: Record<Variant, string> = {
  primary: "border-info/50 bg-info/15 text-fg hover:bg-info/25",
  secondary: "border-line-strong bg-surface-2 text-fg hover:bg-surface-3",
  danger: "border-neg/50 bg-neg/10 text-neg hover:bg-neg/20",
  ghost: "border-transparent bg-transparent text-muted hover:bg-surface-2 hover:text-fg",
};

export const buttonClass = (variant: Variant = "secondary", extra?: string) =>
  cn("inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg border px-3 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
    BUTTON[variant], extra);
