import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

interface ModelResult {
  key: string;
  name: string;
  wape: number;
  accuracy: number;
  nScored: number;
}

const MODEL_NAMES: Record<string, string> = {
  lgbm: 'LightGBM',
  prophet: 'Prophet',
  xgb: 'XGBoost',
  mixed: 'Mixed Effects',
  naive: 'Naive Baseline',
  ols: 'OLS',
};

const RESULTS: ModelResult[] = [
  { key: 'lgbm', name: MODEL_NAMES.lgbm, wape: 0.1041332609577764, accuracy: 0.8958667390422236, nScored: 1783 },
  { key: 'prophet', name: MODEL_NAMES.prophet, wape: 0.12454130253559935, accuracy: 0.8754586974644006, nScored: 1755 },
  { key: 'xgb', name: MODEL_NAMES.xgb, wape: 0.1256280719817802, accuracy: 0.8743719280182198, nScored: 1783 },
  { key: 'mixed', name: MODEL_NAMES.mixed, wape: 0.13838948734381598, accuracy: 0.861610512656184, nScored: 1603 },
  { key: 'naive', name: MODEL_NAMES.naive, wape: 0.1427426480882767, accuracy: 0.8572573519117233, nScored: 1692 },
  { key: 'ols', name: MODEL_NAMES.ols, wape: 0.15803461260247295, accuracy: 0.841965387397527, nScored: 1692 },
].sort((a, b) => a.wape - b.wape);

export default function ModelComparisonTable() {
  const [open, setOpen] = useState(false);
  const bestKey = RESULTS[0].key;

  return (
    <div className="mers-card overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div>
          <h3 className="text-sm font-bold text-[var(--mers-navy)]">Model Comparison</h3>
          <p className="mt-0.5 text-[11px] text-[var(--mers-slate)]">
            Candidate models evaluated on the same holdout, ranked by WAPE (lower is better).
          </p>
        </div>
        {open ? (
          <ChevronDown size={18} className="text-[var(--mers-navy)]" />
        ) : (
          <ChevronRight size={18} className="text-[var(--mers-navy)]" />
        )}
      </button>

      {open && (
        <div className="mers-scrollbar overflow-x-auto border-t border-[var(--mers-ice)]">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-[var(--mers-navy)] text-white">
                <th className="px-3 py-2 text-left font-bold">Model</th>
                <th className="px-3 py-2 text-right font-bold">WAPE</th>
                <th className="px-3 py-2 text-right font-bold">Accuracy</th>
                <th className="px-3 py-2 text-right font-bold">N Scored</th>
              </tr>
            </thead>
            <tbody>
              {RESULTS.map((r) => (
                <tr
                  key={r.key}
                  className="border-b border-[var(--mers-ice)] hover:bg-[var(--mers-canvas)]"
                  style={{ backgroundColor: r.key === bestKey ? '#E6F4EA' : undefined }}
                >
                  <td className="px-3 py-1.5 font-semibold text-[var(--mers-navy)]">
                    {r.name}
                    {r.key === bestKey && (
                      <span
                        className="ml-2 rounded-full px-2 py-0.5 text-[10px] font-bold text-white"
                        style={{ backgroundColor: 'var(--mers-good)' }}
                      >
                        Best
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono">{(r.wape * 100).toFixed(2)}%</td>
                  <td className="px-3 py-1.5 text-right font-mono">{(r.accuracy * 100).toFixed(2)}%</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.nScored.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
