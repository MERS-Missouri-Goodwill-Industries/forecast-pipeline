import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MonthSummary } from '../types';

interface ScenarioComparisonChartProps {
  months: MonthSummary[];
  priorYearMonthly?: number[];
  currentYearLabel: string;
  priorYearLabel?: string;
}

const MERS_COLORS = ['#0065A4', '#FD9D0D', '#82C341', '#929CD0', '#C1272D', '#00263E'];

export default function ScenarioComparisonChart({
  months,
  priorYearMonthly,
  currentYearLabel,
  priorYearLabel,
}: ScenarioComparisonChartProps) {
  const data = months.map((m, i) => ({
    month: m.monthName.slice(0, 3),
    [currentYearLabel]: Math.round(m.plannedSales),
    ...(priorYearMonthly ? { [priorYearLabel ?? 'Prior Year']: Math.round(priorYearMonthly[i] ?? 0) } : {}),
  }));

  return (
    <div className="mers-card p-4">
      <h3 className="mb-3 text-sm font-bold text-[var(--mers-navy)]">Planned Sales Comparison</h3>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <CartesianGrid stroke="#E6F4EA" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 11 }} />
          <YAxis
            tick={{ fontSize: 11 }}
            tickFormatter={(v) => `$${(Number(v) / 1000).toFixed(0)}k`}
            width={64}
          />
          <Tooltip
            formatter={(v) => Number(v).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {priorYearMonthly && (
            <Bar dataKey={priorYearLabel ?? 'Prior Year'} fill={MERS_COLORS[3]} radius={[4, 4, 0, 0]} />
          )}
          <Bar dataKey={currentYearLabel} fill={MERS_COLORS[0]} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
