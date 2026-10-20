/* Reliability (calibration) diagram: mean predicted probability vs observed frequency per bin, with bin sizes.
 * Perfect calibration lies on the diagonal. Small bins are drawn faint because their observed rate is noisy. */

export interface ReliabilityBin { bin_lo: number; bin_hi: number; mean_predicted: number; observed_rate: number; n: number }

export function ReliabilityDiagram({ bins, title, size = 240 }: { bins: ReliabilityBin[]; title: string; size?: number }) {
  const pad = 30;
  const w = size;
  const h = size;
  const x = (p: number) => pad + p * (w - pad - 8);
  const y = (p: number) => h - pad - p * (h - pad - 8);
  const maxN = Math.max(1, ...bins.map((b) => b.n));
  const label = `${title}: ${bins.map((b) => `predicted ${(b.mean_predicted * 100).toFixed(0)}% observed ${(b.observed_rate * 100).toFixed(0)}% (n=${b.n})`).join("; ")}`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" style={{ maxWidth: w }} role="img" aria-label={label}>
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
          <line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(1)} stroke="var(--grid)" />
          <text x={x(t)} y={h - pad + 14} fontSize={9} fill="var(--faint)" textAnchor="middle">{Math.round(t * 100)}</text>
          <text x={pad - 5} y={y(t) + 3} fontSize={9} fill="var(--faint)" textAnchor="end">{Math.round(t * 100)}</text>
        </g>
      ))}
      <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--line-strong)" strokeDasharray="4 3" />
      {bins.map((b) => (
        <g key={`${b.bin_lo}-${b.bin_hi}`}>
          <title>{`${(b.bin_lo * 100).toFixed(0)}–${(b.bin_hi * 100).toFixed(0)}%: predicted ${(b.mean_predicted * 100).toFixed(1)}%, observed ${(b.observed_rate * 100).toFixed(1)}%, n=${b.n}`}</title>
          <circle cx={x(b.mean_predicted)} cy={y(b.observed_rate)} r={3 + 6 * Math.sqrt(b.n / maxN)} fill="var(--info)"
            opacity={b.n < 10 ? 0.35 : 0.8} stroke="var(--info)" />
        </g>
      ))}
      <text x={x(0.5)} y={h - 3} fontSize={9.5} fill="var(--muted)" textAnchor="middle">predicted %</text>
      <text x={9} y={y(0.5)} fontSize={9.5} fill="var(--muted)" textAnchor="middle" transform={`rotate(-90 9 ${y(0.5)})`}>observed %</text>
    </svg>
  );
}
