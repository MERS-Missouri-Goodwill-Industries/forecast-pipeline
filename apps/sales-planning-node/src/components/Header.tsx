import { Play, Radio, Sheet } from 'lucide-react';
import DashboardInfoPanel from './DashboardInfoPanel';

interface HeaderProps {
  year: number;
  years: number[];
  onYearChange: (year: number) => void;
  isLive: boolean;
  onExport: () => void;
  exporting: boolean;
  onRunForecast: () => void;
  runningForecast: boolean;
}

export default function Header({
  year,
  years,
  onYearChange,
  isLive,
  onExport,
  exporting,
  onRunForecast,
  runningForecast,
}: HeaderProps) {
  return (
    <header
      className="flex items-center justify-between px-6 py-4 text-white"
      style={{
        background: 'linear-gradient(90deg, #00263e 0%, #0065a4 100%)',
      }}
    >
      <div className="flex items-center gap-3">
        <div className="text-lg font-bold tracking-tight">MERS Goodwill</div>
        <div className="hidden sm:block text-sm text-white/80">
          Retail Store Sales Forecast &amp; DOW Planning Portal
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1">
          <DashboardInfoPanel />
        </div>

        <span
          className="flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold"
          style={{ backgroundColor: isLive ? '#82c341' : 'rgba(255,255,255,0.15)' }}
        >
          <Radio size={12} />
          {isLive ? 'Databricks Live' : 'Local / Mock Data'}
        </span>

        <select
          className="rounded-md bg-white/10 px-3 py-1.5 text-sm text-white outline-none"
          value={year}
          onChange={(e) => onYearChange(Number(e.target.value))}
        >
          {years.map((y) => (
            <option key={y} value={y} className="text-slate-900">
              FY{y}
            </option>
          ))}
        </select>

        <button
          onClick={onRunForecast}
          disabled={runningForecast}
          className="flex items-center gap-2 rounded-md px-5 py-2.5 text-base font-bold text-white shadow disabled:opacity-60"
          style={{ backgroundColor: '#82c341' }}
        >
          <Play size={18} />
          {runningForecast ? 'Running forecast…' : 'Run Forecast'}
        </button>

        <button
          onClick={onExport}
          disabled={exporting}
          className="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-60"
          style={{ backgroundColor: '#fd9d0d' }}
        >
          <Sheet size={16} />
          {exporting ? 'Building workbook…' : 'Export Excel'}
        </button>
      </div>
    </header>
  );
}
