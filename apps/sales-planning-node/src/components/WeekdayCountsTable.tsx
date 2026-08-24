import { WEEKDAYS } from '../types';
import { countWeekdaysInYear } from '../utils/dowEngine';

interface WeekdayCountsTableProps {
  years: number[];
  currentYear: number;
}

export default function WeekdayCountsTable({ years, currentYear }: WeekdayCountsTableProps) {
  const countsByYear = years.map((year) => ({ year, counts: countWeekdaysInYear(year) }));

  return (
    <div className="mers-card overflow-hidden">
      <div className="mers-scrollbar overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-[var(--mers-navy)] text-white">
              <th className="px-3 py-2 text-left font-bold">Weekday</th>
              {countsByYear.map(({ year }) => (
                <th
                  key={year}
                  className="px-3 py-2 text-right font-bold"
                  style={{ backgroundColor: year === currentYear ? 'var(--mers-blue)' : undefined }}
                >
                  {year}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {WEEKDAYS.map((day) => (
              <tr key={day} className="border-b border-[var(--mers-ice)] hover:bg-[var(--mers-canvas)]">
                <td className="px-3 py-1.5 font-semibold text-[var(--mers-navy)]">{day}</td>
                {countsByYear.map(({ year, counts }) => (
                  <td
                    key={year}
                    className="px-3 py-1.5 text-right font-mono"
                    style={{
                      fontWeight: year === currentYear ? 700 : 400,
                      color: counts[day] === 53 ? 'var(--mers-crimson)' : undefined,
                    }}
                  >
                    {counts[day]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="bg-[var(--mers-canvas)] font-bold text-[var(--mers-navy)]">
              <td className="px-3 py-2">Total Days</td>
              {countsByYear.map(({ year, counts }) => (
                <td key={year} className="px-3 py-2 text-right font-mono">
                  {WEEKDAYS.reduce((sum, d) => sum + counts[d], 0)}
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="px-3 py-2 text-[11px] text-[var(--mers-slate)]">
        Most weekdays occur 52 times a year — a weekday shown in red occurs 53 times (extra selling/closed day to
        account for when weighting that day).
      </p>
    </div>
  );
}
