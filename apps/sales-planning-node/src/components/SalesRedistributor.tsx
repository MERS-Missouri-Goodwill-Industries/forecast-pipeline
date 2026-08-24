interface SalesRedistributorProps {
  recommendedPlan: number;
  cooAdjustedPlan: number;
  eastTotal: number;
  westTotal: number;
  onRecommendedPlanChange: (v: number) => void;
}

function formatCurrency(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}

export default function SalesRedistributor({
  recommendedPlan,
  cooAdjustedPlan,
  eastTotal,
  westTotal,
  onRecommendedPlanChange,
}: SalesRedistributorProps) {
  const total = eastTotal + westTotal;
  const eastPct = total > 0 ? (eastTotal / total) * 100 : 0;
  const westPct = total > 0 ? (westTotal / total) * 100 : 0;
  // Tolerance, not an exact zero test: splitting the plan across ~65 stores leaves sub-cent
  // floating-point residue, which would otherwise render as a coloured "$0" implying a real gap.
  const rawVariance = cooAdjustedPlan - recommendedPlan;
  const variance = Math.abs(rawVariance) < 0.5 ? 0 : rawVariance;

  return (
    <div className="mers-card p-4">
      <h3 className="mb-3 text-sm font-bold text-[var(--mers-navy)]">Recommended Plan &amp; Regional Split</h3>

      <label className="block text-xs text-[var(--mers-slate)]">
        Recommended Plan (Network Sales)
        <input
          type="number"
          value={recommendedPlan}
          onChange={(e) => onRecommendedPlanChange(Number(e.target.value))}
          className="mt-1 w-full rounded border border-[var(--mers-ice)] px-2 py-1 text-right font-mono text-sm"
          style={{ color: '#0000FF', backgroundColor: '#FFFF99' }}
        />
      </label>

      <div className="mt-3 space-y-1.5 text-xs">
        <div className="flex justify-between">
          <span className="text-[var(--mers-slate)]">COO Adjusted Plan</span>
          <span className="font-mono font-semibold text-[var(--mers-navy)]">{formatCurrency(cooAdjustedPlan)}</span>
        </div>
        <div className="flex justify-between border-t border-[var(--mers-ice)] pt-1.5">
          <span className="text-[var(--mers-slate)]">Variance</span>
          <span
            className="font-mono font-bold"
            style={{ color: variance === 0 ? 'var(--mers-slate)' : variance > 0 ? 'var(--mers-good)' : 'var(--mers-crimson)' }}
          >
            {variance === 0 ? '—' : formatCurrency(variance)}
          </span>
        </div>
      </div>

      <p className="mt-2 text-[11px] text-[var(--mers-slate)]">
        Split across stores in proportion to each store's base sales. Adjust individual stores in the table below.
      </p>

      <div className="mt-3 border-t border-[var(--mers-ice)] pt-3">
        <div className="mb-1 flex justify-between text-xs">
          <span style={{ color: 'var(--mers-blue)' }}>East {eastPct.toFixed(1)}%</span>
          <span style={{ color: 'var(--mers-lavender)' }}>West {westPct.toFixed(1)}%</span>
        </div>
        <div className="flex h-2 overflow-hidden rounded-full bg-[var(--mers-ice)]">
          <div style={{ width: `${eastPct}%`, backgroundColor: 'var(--mers-blue)' }} />
          <div style={{ width: `${westPct}%`, backgroundColor: 'var(--mers-lavender)' }} />
        </div>
        <div className="mt-2 flex justify-between text-xs font-mono text-[var(--mers-slate)]">
          <span>{formatCurrency(eastTotal)}</span>
          <span>{formatCurrency(westTotal)}</span>
        </div>
      </div>
    </div>
  );
}
