import { useState } from 'react';
import type { Store, StoreOverrides } from '../types';

interface StoreForecastAdjustmentTableProps {
  stores: Store[];
  forecastedBases: Map<string, number>;
  storeOverrides: StoreOverrides;
  onOverrideChange: (code: string, annualPlanBase: number | undefined) => void;
}

function formatCurrency(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}

export default function StoreForecastAdjustmentTable({
  stores,
  forecastedBases,
  storeOverrides,
  onOverrideChange,
}: StoreForecastAdjustmentTableProps) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const commitDraft = (code: string, raw: string) => {
    if (raw.trim() === '') {
      onOverrideChange(code, undefined);
      return;
    }
    const parsed = Number(raw);
    if (!Number.isNaN(parsed)) onOverrideChange(code, parsed);
  };

  return (
    <div className="mers-card overflow-hidden">
      <div className="mers-scrollbar max-h-[28rem] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0">
            <tr className="bg-[var(--mers-navy)] text-white">
              <th className="px-3 py-2 text-left font-bold">Store</th>
              <th className="px-3 py-2 text-right font-bold">Forecasted Planned Sales ($)</th>
              <th className="px-3 py-2 text-right font-bold">COO Adjusted Planned Sales</th>
              <th className="px-3 py-2 text-right font-bold">Variance</th>
            </tr>
          </thead>
          <tbody>
            {stores.map((store) => {
              const forecasted = forecastedBases.get(store.code) ?? 0;
              const override = storeOverrides[store.code]?.annualPlanBase;
              const draft = drafts[store.code];
              const displayValue = draft !== undefined ? draft : override !== undefined ? String(Math.round(override)) : '';
              const effective = override ?? forecasted;
              const variance = effective - forecasted;
              return (
                <tr key={store.code} className="border-b border-[var(--mers-ice)] hover:bg-[var(--mers-canvas)]">
                  <td className="px-3 py-1.5">
                    <span className="font-mono text-[var(--mers-slate)]">{store.code}</span> — {store.name}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono">{formatCurrency(forecasted)}</td>
                  <td className="px-3 py-1.5 text-right">
                    <input
                      type="number"
                      placeholder={String(Math.round(forecasted))}
                      value={displayValue}
                      onChange={(e) => setDrafts((d) => ({ ...d, [store.code]: e.target.value }))}
                      onBlur={(e) => {
                        commitDraft(store.code, e.target.value);
                        setDrafts((d) => {
                          const next = { ...d };
                          delete next[store.code];
                          return next;
                        });
                      }}
                      className="w-32 rounded border px-2 py-1 text-right font-mono"
                      style={{
                        borderColor: 'var(--mers-ice)',
                        color: '#0000FF',
                        backgroundColor: '#FFFF99',
                      }}
                    />
                  </td>
                  <td
                    className="px-3 py-1.5 text-right font-mono font-semibold"
                    style={{ color: variance === 0 ? 'var(--mers-slate)' : variance > 0 ? 'var(--mers-good)' : 'var(--mers-crimson)' }}
                  >
                    {variance === 0 ? '—' : formatCurrency(variance)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
