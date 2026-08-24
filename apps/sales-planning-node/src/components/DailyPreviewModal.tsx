import { useMemo, useState } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import type { DayFactor, StoreClosureRange, StorePlan } from '../types';

interface DailyPreviewModalProps {
  plan: StorePlan;
  dayFactors: DayFactor[];
  closures: StoreClosureRange[];
  onClosuresChange: (closures: StoreClosureRange[]) => void;
  onClose: () => void;
}

function formatCurrency(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
}

function ClosureEditor({ closures, onChange }: { closures: StoreClosureRange[]; onChange: (c: StoreClosureRange[]) => void }) {
  const [draft, setDraft] = useState<StoreClosureRange>({ start: '', end: '', label: 'Renovation' });

  const addClosure = () => {
    if (!draft.start || !draft.end || draft.start > draft.end) return;
    onChange([...closures, draft]);
    setDraft({ start: '', end: '', label: 'Renovation' });
  };

  return (
    <div className="border-b border-[var(--mers-ice)] bg-[var(--mers-canvas)] px-3 py-2">
      <div className="mb-1.5 text-xs font-bold text-[var(--mers-navy)]">
        Store Closures (renovation / full closure — closed days read 0% and drop out of their month)
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs">
          Start
          <input
            type="date"
            value={draft.start}
            onChange={(e) => setDraft((d) => ({ ...d, start: e.target.value }))}
            className="mt-0.5 block rounded border border-[var(--mers-ice)] px-1.5 py-1"
          />
        </label>
        <label className="text-xs">
          End
          <input
            type="date"
            value={draft.end}
            onChange={(e) => setDraft((d) => ({ ...d, end: e.target.value }))}
            className="mt-0.5 block rounded border border-[var(--mers-ice)] px-1.5 py-1"
          />
        </label>
        <label className="text-xs">
          Label
          <input
            type="text"
            value={draft.label ?? ''}
            onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))}
            className="mt-0.5 block w-28 rounded border border-[var(--mers-ice)] px-1.5 py-1"
          />
        </label>
        <button
          onClick={addClosure}
          disabled={!draft.start || !draft.end}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs font-semibold text-white disabled:opacity-50"
          style={{ backgroundColor: 'var(--mers-blue)' }}
        >
          <Plus size={12} /> Add
        </button>
      </div>

      {closures.length > 0 && (
        <div className="mt-2 space-y-1">
          {closures.map((c, i) => (
            <div key={`${c.start}-${c.end}-${i}`} className="flex items-center justify-between rounded bg-white px-2 py-1 text-xs">
              <span>
                <span className="font-semibold" style={{ color: 'var(--mers-crimson)' }}>{c.label || 'Store Closure'}</span>: {c.start} → {c.end}
              </span>
              <button
                onClick={() => onChange(closures.filter((_, idx) => idx !== i))}
                className="rounded p-1 hover:bg-[var(--mers-canvas)]"
                style={{ color: 'var(--mers-crimson)' }}
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function DailyPreviewModal({ plan, dayFactors, closures, onClosuresChange, onClose }: DailyPreviewModalProps) {
  const [monthFilter, setMonthFilter] = useState(1);

  const rows = useMemo(() => {
    return plan.dailyPlanned
      .map((d, i) => ({ ...d, factor: dayFactors[i] }))
      .filter((d) => d.factor.month === monthFilter);
  }, [plan.dailyPlanned, dayFactors, monthFilter]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="mers-card flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden bg-white">
        <div
          className="flex items-center justify-between px-4 py-3 text-white"
          style={{ backgroundColor: 'var(--mers-navy)' }}
        >
          <div>
            <div className="text-sm font-bold">{plan.store.name}</div>
            <div className="text-xs text-white/70">
              Annual Plan Base: {formatCurrency(plan.annualPlanBase)} · Full Year Total: {formatCurrency(plan.fullYearTotal)} ·{' '}
              {closures.length > 0 ? 'Closure Impact' : 'Diff'}: {formatCurrency(plan.fullYearTotal - plan.annualPlanBase)}
            </div>
          </div>
          <button onClick={onClose} className="rounded p-1 hover:bg-white/10">
            <X size={18} />
          </button>
        </div>

        <ClosureEditor closures={closures} onChange={onClosuresChange} />

        <div className="flex gap-1 overflow-x-auto border-b border-[var(--mers-ice)] px-3 py-2">
          {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
            <button
              key={m}
              onClick={() => setMonthFilter(m)}
              className="rounded px-2 py-1 text-xs font-semibold"
              style={{
                backgroundColor: monthFilter === m ? 'var(--mers-blue)' : 'var(--mers-canvas)',
                color: monthFilter === m ? 'white' : 'var(--mers-slate)',
              }}
            >
              {new Date(2000, m - 1, 1).toLocaleString('en-US', { month: 'short' })}
            </button>
          ))}
        </div>

        <div className="mers-scrollbar flex-1 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-[var(--mers-canvas)]">
              <tr>
                <th className="px-3 py-1.5 text-left">Date</th>
                <th className="px-3 py-1.5 text-left">Weekday</th>
                <th className="px-3 py-1.5 text-left">Holiday</th>
                <th className="px-3 py-1.5 text-right">Day % of Annual</th>
                <th className="px-3 py-1.5 text-right">Planned Sales</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.date}
                  className="border-b border-[var(--mers-ice)]"
                  style={{ backgroundColor: row.factor.holidayLabel ? '#fffbe6' : undefined }}
                >
                  <td className="px-3 py-1">{row.date}</td>
                  <td className="px-3 py-1">{row.factor.weekday}</td>
                  <td className="px-3 py-1" style={{ color: 'var(--mers-crimson)' }}>
                    {row.factor.holidayLabel}
                  </td>
                  <td className="px-3 py-1 text-right font-mono">{(row.factor.dayPctOfAnnual * 100).toFixed(4)}%</td>
                  <td className="px-3 py-1 text-right font-mono">{formatCurrency(row.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
