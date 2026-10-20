"use client";

import { useEffect, useMemo, useRef, useState } from "react";

/* Daily candlestick chart drawn from APEX's own stored bars (never a third-party finance embed).
 * Bars after `cutoff` are dropped, so a chart can never show data the system did not have at that time.
 * Up candles are hollow and down candles filled, so direction does not rely on colour alone. */

export interface Bar { date: string; open: number; high: number; low: number; close: number; volume: number }
export interface ChartEvent { date: string; label: string; tone?: "pos" | "neg" | "info" | "warn" | "neutral" }
export interface ChartLevels {
  entryMin?: number; entryMax?: number; stop?: number; target?: number;
  asofDate?: string; entryExpiresDate?: string; horizonSessions?: number;
}

const TONE_VAR: Record<string, string> = { pos: "var(--pos)", neg: "var(--neg)", info: "var(--info)", warn: "var(--warn)", neutral: "var(--muted)" };

export function CandleChart({ bars, levels, events = [], cutoff, height = 320, title }: {
  bars: Bar[]; levels?: ChartLevels; events?: ChartEvent[]; cutoff?: string; height?: number; title: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setWidth(Math.max(280, Math.floor(entries[0]!.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const model = useMemo(() => {
    const visibleBars = (cutoff ? bars.filter((b) => b.date <= cutoff) : bars).slice();
    const maxBars = Math.max(30, Math.min(visibleBars.length, Math.floor(width / 7)));
    const shown = visibleBars.slice(-maxBars);
    const asofIdx = levels?.asofDate ? shown.findIndex((b) => b.date >= levels.asofDate!) : -1;
    const horizon = levels?.horizonSessions ?? 0;
    const futureSlots = asofIdx >= 0 ? Math.max(0, asofIdx + horizon - (shown.length - 1)) : 0;
    const slots = shown.length + Math.min(futureSlots, 40);
    const prices = shown.flatMap((b) => [b.high, b.low]);
    for (const v of [levels?.entryMin, levels?.entryMax, levels?.stop, levels?.target]) if (typeof v === "number" && Number.isFinite(v)) prices.push(v);
    const lo = Math.min(...prices);
    const hi = Math.max(...prices);
    const pad = (hi - lo || hi * 0.05 || 1) * 0.06;
    return { shown, asofIdx, slots, lo: lo - pad, hi: hi + pad, maxVol: Math.max(1, ...shown.map((b) => b.volume)) };
  }, [bars, cutoff, levels, width]);

  if (!model.shown.length) {
    return <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-line-strong text-xs text-muted">No stored bars for this symbol yet</div>;
  }

  const left = 6;
  const right = 74;
  const top = 18;
  const volH = Math.round(height * 0.16);
  const bottom = 22;
  const plotW = width - left - right;
  const priceH = height - top - bottom - volH - 6;
  const step = plotW / model.slots;
  const bodyW = Math.max(1.5, Math.min(9, step * 0.62));
  const x = (i: number) => left + step * (i + 0.5);
  const y = (p: number) => top + ((model.hi - p) / (model.hi - model.lo)) * priceH;
  const volTop = top + priceH + 6;
  const ticks = Array.from({ length: 5 }, (_, i) => model.lo + ((model.hi - model.lo) * (i + 0.5)) / 5);
  const monthTicks = model.shown.map((b, i) => ({ b, i })).filter(({ b, i }) => i > 0 && b.date.slice(5, 7) !== model.shown[i - 1]!.date.slice(5, 7));
  const idxOf = (date: string) => {
    const i = model.shown.findIndex((b) => b.date >= date);
    return i < 0 ? null : i;
  };
  const a = model.asofIdx;
  const expIdx = levels?.entryExpiresDate ? (idxOf(levels.entryExpiresDate) ?? model.slots - 1) : a >= 0 ? Math.min(model.slots - 1, a + 2) : -1;
  const horizonEnd = a >= 0 && levels?.horizonSessions ? Math.min(model.slots - 1, a + levels.horizonSessions) : model.slots - 1;
  const hb = hover !== null ? model.shown[hover] : model.shown[model.shown.length - 1];
  const last = model.shown[model.shown.length - 1]!;
  const summary = `${title}: ${model.shown.length} daily bars from ${model.shown[0]!.date} to ${last.date}, last close ${last.close.toFixed(2)}` +
    (levels?.entryMin !== undefined ? `; entry zone ${levels.entryMin.toFixed(2)}-${levels.entryMax?.toFixed(2)}, stop ${levels.stop?.toFixed(2)}, target ${levels.target?.toFixed(2)}` : "");

  const levelLine = (p: number | undefined, color: string, label: string, dash = "4 3") =>
    typeof p === "number" && Number.isFinite(p) ? (
      <g>
        <line x1={a >= 0 ? x(a) - step / 2 : left} x2={x(horizonEnd) + step / 2} y1={y(p)} y2={y(p)} stroke={color} strokeWidth={1.2} strokeDasharray={dash} />
        <rect x={width - right + 2} y={y(p) - 8} width={right - 4} height={16} rx={3} fill={color} opacity={0.18} />
        <text x={width - right + 6} y={y(p) + 4} fontSize={10.5} fill={color} className="num">{label} {p.toFixed(2)}</text>
      </g>
    ) : null;

  return (
    <figure className="m-0">
      <div className="num mb-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted" aria-hidden="true">
        <span className="text-fg">{hb?.date}</span>
        <span>O {hb?.open.toFixed(2)}</span><span>H {hb?.high.toFixed(2)}</span><span>L {hb?.low.toFixed(2)}</span><span>C {hb?.close.toFixed(2)}</span>
        <span>Vol {hb ? Intl.NumberFormat("en-US", { notation: "compact" }).format(hb.volume) : ""}</span>
      </div>
      <div ref={ref} className="w-full">
        <svg width={width} height={height} role="img" aria-label={summary} className="block select-none"
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const i = Math.floor((e.clientX - r.left - left) / step);
            setHover(i >= 0 && i < model.shown.length ? i : null);
          }}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={width - right} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
              <text x={width - right + 6} y={y(t) + 3.5} fontSize={10} fill="var(--faint)" className="num">{t.toFixed(2)}</text>
            </g>
          ))}
          {monthTicks.map(({ b, i }) => (
            <g key={b.date}>
              <line x1={x(i) - step / 2} x2={x(i) - step / 2} y1={top} y2={volTop + volH} stroke="var(--grid)" />
              <text x={x(i) - step / 2 + 3} y={height - 6} fontSize={10} fill="var(--faint)">
                {new Date(`${b.date}T12:00:00Z`).toLocaleString("en-US", { month: "short", timeZone: "UTC" })}
              </text>
            </g>
          ))}
          {a >= 0 && levels?.horizonSessions ? (
            <g>
              <rect x={x(a) - step / 2} y={top} width={x(horizonEnd) - x(a) + step} height={priceH} fill="var(--info)" opacity={0.05} />
              {model.slots > model.shown.length ? (
                <text x={x(model.shown.length) + 2} y={top + 12} fontSize={10} fill="var(--faint)">projection horizon (no future bars shown)</text>
              ) : null}
            </g>
          ) : null}
          {a >= 0 && typeof levels?.entryMin === "number" && typeof levels.entryMax === "number" ? (
            <g>
              <rect x={x(a) - step / 2} y={y(levels.entryMax)} width={Math.max(step, x(expIdx) - x(a) + step)} height={Math.max(1, y(levels.entryMin) - y(levels.entryMax))}
                fill="var(--info)" opacity={0.18} stroke="var(--info)" strokeOpacity={0.5} />
              <text x={x(a) - step / 2 + 3} y={y(levels.entryMax) - 4} fontSize={10} fill="var(--info)">entry zone</text>
            </g>
          ) : null}
          {levelLine(levels?.stop, "var(--neg)", "Stop")}
          {levelLine(levels?.target, "var(--pos)", "Tgt")}
          {model.shown.map((b, i) => {
            const up = b.close >= b.open;
            const color = up ? "var(--pos)" : "var(--neg)";
            const yo = y(b.open);
            const yc = y(b.close);
            return (
              <g key={b.date}>
                <line x1={x(i)} x2={x(i)} y1={y(b.high)} y2={y(b.low)} stroke={color} strokeWidth={1} />
                <rect x={x(i) - bodyW / 2} y={Math.min(yo, yc)} width={bodyW} height={Math.max(1, Math.abs(yc - yo))}
                  fill={up ? "var(--surface)" : color} stroke={color} strokeWidth={1} />
                <rect x={x(i) - bodyW / 2} y={volTop + volH - (b.volume / model.maxVol) * volH} width={bodyW} height={(b.volume / model.maxVol) * volH}
                  fill="var(--faint)" opacity={0.35} />
              </g>
            );
          })}
          {events.map((ev, k) => {
            const i = idxOf(ev.date);
            if (i === null) return null;
            const c = TONE_VAR[ev.tone ?? "neutral"];
            return (
              <g key={`${ev.date}-${k}`}>
                <title>{`${ev.date}: ${ev.label}`}</title>
                <line x1={x(i)} x2={x(i)} y1={top} y2={top + priceH} stroke={c} strokeDasharray="2 3" opacity={0.7} />
                <path d={`M ${x(i) - 4} ${top - 2} L ${x(i) + 4} ${top - 2} L ${x(i)} ${top + 5} Z`} fill={c} />
              </g>
            );
          })}
          {hover !== null ? <line x1={x(hover)} x2={x(hover)} y1={top} y2={volTop + volH} stroke="var(--line-strong)" /> : null}
        </svg>
      </div>
      <figcaption className="mt-1 text-[11px] text-faint">
        Daily bars from APEX&apos;s stored history (split-adjusted). Hollow = up day, filled = down day. Stop/target lines are reference levels, not
        guaranteed fills; gaps can move through them.
      </figcaption>
    </figure>
  );
}
