import type { MonthSummary } from '../types';

interface MonthlyAllocationTableProps {
  months: MonthSummary[];
  priorYearMonthly?: number[];
}

function formatCurrency(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}

export default function MonthlyAllocationTable({ months, priorYearMonthly }: MonthlyAllocationTableProps) {
  const totalPct = months.reduce((s, m) => s + m.pctOfAnnual, 0);
  const totalPlanned = months.reduce((s, m) => s + m.plannedSales, 0);

  return (
    <div className="mers-card overflow-hidden">
      <div className="mers-scrollbar overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-[var(--mers-navy)] text-white">
              <th className="px-3 py-2 text-left">Month</th>
              <th className="px-3 py-2 text-right">Days</th>
              <th className="px-3 py-2 text-right">Closed</th>
              <th className="px-3 py-2 text-right">Selling</th>
              <th className="px-3 py-2 text-right">% of Annual</th>
              <th className="px-3 py-2 text-right">Planned Sales</th>
              {priorYearMonthly && <th className="px-3 py-2 text-right">Δ vs Prior Yr</th>}
            </tr>
          </thead>
          <tbody>
            {months.map((m, i) => {
              const prior = priorYearMonthly?.[i];
              const delta = prior && prior > 0 ? m.plannedSales / prior - 1 : undefined;
              return (
                <tr key={m.month} className="border-b border-[var(--mers-ice)] hover:bg-[var(--mers-canvas)]">
                  <td className="px-3 py-1.5 font-semibold text-[var(--mers-navy)]">{m.monthName}</td>
                  <td className="px-3 py-1.5 text-right">{m.days}</td>
                  <td className="px-3 py-1.5 text-right" style={{ color: m.closedDays > 0 ? 'var(--mers-crimson)' : undefined }}>
                    {m.closedDays}
                  </td>
                  <td className="px-3 py-1.5 text-right">{m.sellingDays}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{(m.pctOfAnnual * 100).toFixed(4)}%</td>
                  <td className="px-3 py-1.5 text-right font-mono">{formatCurrency(m.plannedSales)}</td>
                  {priorYearMonthly && (
                    <td
                      className="px-3 py-1.5 text-right font-mono"
                      style={{ color: delta !== undefined && delta >= 0 ? 'var(--mers-good)' : 'var(--mers-crimson)' }}
                    >
                      {delta !== undefined ? `${(delta * 100).toFixed(1)}%` : '—'}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="bg-[var(--mers-canvas)] font-bold text-[var(--mers-navy)]">
              <td className="px-3 py-2">Total</td>
              <td colSpan={2} />
              <td />
              <td className="px-3 py-2 text-right font-mono">{(totalPct * 100).toFixed(2)}%</td>
              <td className="px-3 py-2 text-right font-mono">{formatCurrency(totalPlanned)}</td>
              {priorYearMonthly && <td />}
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
