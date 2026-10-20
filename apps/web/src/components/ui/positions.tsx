import { Badge, type Tone } from "@/components/ui/primitives";

/* Assessment statuses in informational wording: they describe which pre-stated criteria are met, never an instruction. */
export const ASSESSMENT: Record<string, { label: string; tone: Tone }> = {
  HOLD_THESIS_INTACT: { label: "Thesis intact", tone: "pos" },
  REVIEW_INVALIDATION: { label: "Near invalidation level", tone: "warn" },
  CONSIDER_TRIM: { label: "Trim criteria met", tone: "info" },
  CONSIDER_EXIT: { label: "Exit criteria met", tone: "neg" },
  TARGET_TOUCHED_FILL_UNKNOWN: { label: "Target touched (fill unknown)", tone: "pos" },
  TIME_EXIT_REVIEW: { label: "Holding window ending", tone: "warn" },
  ADD_CRITERIA_MET: { label: "Add criteria met", tone: "info" },
  PRICE_UNCERTAIN: { label: "Price uncertain", tone: "warn" },
  STALE_DATA: { label: "Stale data", tone: "warn" },
  MANUAL_WATCH: { label: "Watching (no plan)", tone: "neutral" },
  CLOSED: { label: "Closed", tone: "neutral" },
};

export function AssessmentBadge({ status }: { status: string | undefined }) {
  if (!status) return <Badge>not assessed yet</Badge>;
  const m = ASSESSMENT[status] ?? { label: status.toLowerCase().replace(/_/g, " "), tone: "neutral" as Tone };
  return <Badge tone={m.tone}>{m.label}</Badge>;
}

export const PRICE_OK = new Set(["FRESH_VERIFIED", "FRESH_UNVERIFIED"]);
/** Unrealized P&L is shown only from a fresh price (≤ 1 hour old); otherwise the UI says the price is stale. */
export const MAX_PRICE_AGE_S = 3600;
