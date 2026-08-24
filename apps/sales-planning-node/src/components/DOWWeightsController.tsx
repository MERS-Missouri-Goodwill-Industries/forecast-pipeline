import { WEEKDAYS } from '../types';
import type { DOWWeights } from '../types';
import { DOW_PRESETS } from '../utils/dowEngine';

interface DOWWeightsControllerProps {
  weights: DOWWeights;
  onChange: (weights: DOWWeights) => void;
  preset: string;
  onPresetChange: (preset: string) => void;
}

export default function DOWWeightsController({ weights, onChange, preset, onPresetChange }: DOWWeightsControllerProps) {
  const enteredTotal = WEEKDAYS.reduce((s, d) => s + (weights[d] ?? 0), 0);

  const handleSlider = (day: (typeof WEEKDAYS)[number], value: number) => {
    onChange({ ...weights, [day]: value });
  };

  const handlePreset = (name: string) => {
    onPresetChange(name);
    if (DOW_PRESETS[name]) onChange(DOW_PRESETS[name]);
  };

  return (
    <div className="mers-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-bold text-[var(--mers-navy)]">Day-of-Week Weighting</h3>
        <select
          className="rounded border border-[var(--mers-ice)] px-2 py-1 text-xs"
          value={preset}
          onChange={(e) => handlePreset(e.target.value)}
        >
          <option value="recommended">Recommended (from Actuals)</option>
          <option value="excel_plan">Current Excel Plan</option>
          <option value="even">Even Split</option>
          <option value="custom">Custom</option>
        </select>
      </div>

      <div className="space-y-2">
        {WEEKDAYS.map((day) => (
          <div key={day} className="flex items-center gap-3">
            <span className="w-24 text-xs text-[var(--mers-slate)]">{day}</span>
            <input
              type="range"
              min={0}
              max={0.4}
              step={0.0001}
              value={weights[day] ?? 0}
              onChange={(e) => handleSlider(day, Number(e.target.value))}
              className="flex-1 accent-[var(--mers-blue)]"
            />
            <div className="flex w-20 items-center rounded border border-[var(--mers-ice)]" style={{ backgroundColor: '#FFFF99' }}>
              <input
                type="number"
                min={0}
                max={100}
                step={0.01}
                value={Math.round((weights[day] ?? 0) * 10000) / 100}
                onChange={(e) => {
                  const pct = Number(e.target.value);
                  if (!Number.isNaN(pct)) handleSlider(day, pct / 100);
                }}
                className="w-full min-w-0 bg-transparent px-1.5 py-1 text-right text-xs font-mono"
                style={{ color: '#0000FF' }}
              />
              <span className="pr-1.5 text-xs text-[var(--mers-slate)]">%</span>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 flex justify-between border-t border-[var(--mers-ice)] pt-2 text-xs">
        <span className="text-[var(--mers-slate)]">Total</span>
        <span
          className="font-mono font-bold"
          style={{ color: Math.abs(enteredTotal - 1) < 0.0005 ? 'var(--mers-good)' : 'var(--mers-amber)' }}
        >
          {(enteredTotal * 100).toFixed(2)}%
        </span>
      </div>
    </div>
  );
}
