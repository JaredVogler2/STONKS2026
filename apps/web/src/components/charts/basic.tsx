/* Small server-rendered SVG charts. Every chart carries an aria-label summary and a visible unit; colour is paired
 * with position/labels so no reading depends on hue alone. */

export interface BarDatum { label: string; value: number | null; note?: string }

/** Horizontal bars around a zero line (handles negative values). */
export function HBarChart({ data, format = (v) => v.toFixed(2), title, unit }: {
  data: BarDatum[]; format?: (v: number) => string; title: string; unit?: string;
}) {
  const vals = data.map((d) => d.value).filter((v): v is number => v !== null && Number.isFinite(v));
  if (!vals.length) return <p className="text-[12px] text-muted">No data.</p>;
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const zero = ((0 - lo) / span) * 100;
  return (
    <figure className="m-0" aria-label={`${title}${unit ? ` (${unit})` : ""}: ${data.map((d) => `${d.label} ${d.value === null ? "n/a" : format(d.value)}`).join(", ")}`}>
      <ul className="space-y-1.5">
        {data.map((d) => {
          const v = d.value;
          const left = v === null ? zero : v >= 0 ? zero : ((v - lo) / span) * 100;
          const width = v === null ? 0 : (Math.abs(v) / span) * 100;
          return (
            <li key={d.label} className="grid grid-cols-[minmax(84px,30%)_minmax(0,1fr)_64px] items-center gap-2 text-xs">
              <span className="truncate text-muted" title={d.note ?? d.label}>{d.label}</span>
              <span className="relative h-2.5 rounded-sm bg-surface-3/60" aria-hidden="true">
                <span className="absolute inset-y-0 w-px bg-line-strong" style={{ left: `${zero}%` }} />
                {v !== null ? <span className="absolute inset-y-0 rounded-sm" style={{ left: `${left}%`, width: `${Math.max(0.5, width)}%`,
                  background: v >= 0 ? "var(--pos)" : "var(--neg)", opacity: 0.75 }} /> : null}
              </span>
              <span className="num text-right">{v === null ? "n/a" : format(v)}</span>
            </li>
          );
        })}
      </ul>
      {unit ? <figcaption className="mt-1 text-[11px] text-faint">{unit}</figcaption> : null}
    </figure>
  );
}

/** Column histogram for counts. */
export function Histogram({ series, title }: { series: { name: string; color: string; bins: { bucket: string; n: number }[] }[]; title: string }) {
  const buckets = series[0]?.bins.map((b) => b.bucket) ?? [];
  const max = Math.max(1, ...series.flatMap((s) => s.bins.map((b) => b.n)));
  const h = 110;
  return (
    <figure className="m-0" aria-label={`${title}: ${series.map((s) => `${s.name} ${s.bins.map((b) => `${b.bucket}:${b.n}`).join(" ")}`).join("; ")}`}>
      <div className="flex items-end gap-2" style={{ height: h }}>
        {buckets.map((bk, i) => (
          <div key={bk} className="flex h-full min-w-0 flex-1 items-end justify-center gap-0.5">
            {series.map((s) => {
              const n = s.bins[i]?.n ?? 0;
              return <span key={s.name} title={`${s.name} ${bk} sessions: ${n}`} className="w-full max-w-4 rounded-t-sm"
                style={{ height: `${(n / max) * 100}%`, minHeight: n ? 2 : 0, background: s.color, opacity: 0.8 }} />;
            })}
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-2 text-[10px] text-faint">{buckets.map((b) => <span key={b} className="flex-1 text-center">{b}</span>)}</div>
      <figcaption className="mt-1 flex flex-wrap gap-3 text-[11px] text-muted">
        {series.map((s) => <span key={s.name} className="inline-flex items-center gap-1"><span className="inline-block size-2 rounded-sm" style={{ background: s.color }} />{s.name}</span>)}
        <span className="text-faint">sessions from fill to exit</span>
      </figcaption>
    </figure>
  );
}

/** Area chart of an equity curve with its drawdown underneath (fixed viewBox, scales to width). */
export function EquityDrawdown({ points, title }: { points: { date: string; equity: number; drawdown: number }[]; title: string }) {
  if (points.length < 2) return <p className="text-[12px] text-muted">Not enough trades for a curve.</p>;
  const W = 600;
  const H = 150;
  const D = 60;
  const eqLo = Math.min(...points.map((p) => p.equity), 1);
  const eqHi = Math.max(...points.map((p) => p.equity), 1);
  const ddLo = Math.min(...points.map((p) => p.drawdown), -0.01);
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => 6 + (1 - (v - eqLo) / (eqHi - eqLo || 1)) * (H - 12);
  const yd = (v: number) => H + 8 + (v / ddLo) * (D - 10);
  const eqPath = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const ddPath = `M0,${H + 8} ${points.map((p, i) => `L${x(i).toFixed(1)},${yd(p.drawdown).toFixed(1)}`).join(" ")} L${W},${H + 8} Z`;
  const last = points[points.length - 1]!;
  const worst = Math.min(...points.map((p) => p.drawdown));
  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${W} ${H + D}`} width="100%" preserveAspectRatio="none" role="img" className="h-[180px]"
        aria-label={`${title}: from ${points[0]!.date} to ${last.date}, ending at ${last.equity.toFixed(3)}x, worst drawdown ${(worst * 100).toFixed(1)}%`}>
        <line x1={0} x2={W} y1={y(1)} y2={y(1)} stroke="var(--line-strong)" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
        <path d={eqPath} fill="none" stroke="var(--info)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        <path d={ddPath} fill="var(--neg)" opacity={0.35} />
      </svg>
      <figcaption className="num mt-1 flex flex-wrap justify-between gap-2 text-[11px] text-faint">
        <span>{points[0]!.date}</span>
        <span>end {last.equity.toFixed(3)}× · worst drawdown {(worst * 100).toFixed(1)}%</span>
        <span>{last.date}</span>
      </figcaption>
    </figure>
  );
}
