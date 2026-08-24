import { useEffect, useMemo, useState } from 'react';
import Header from './components/Header';
import DOWWeightsController from './components/DOWWeightsController';
import SalesRedistributor from './components/SalesRedistributor';
import MonthlyAllocationTable from './components/MonthlyAllocationTable';
import StoreSelector from './components/StoreSelector';
import DailyPreviewModal from './components/DailyPreviewModal';
import ScenarioComparisonChart from './components/ScenarioComparisonChart';
import SessionsRightDrawer from './components/SessionsRightDrawer';
import JsonImportModal from './components/JsonImportModal';
import StoreForecastAdjustmentTable from './components/StoreForecastAdjustmentTable';
import WeekdayCountsTable from './components/WeekdayCountsTable';
import ModelComparisonTable from './components/ModelComparisonTable';
import { STORES } from './data/storesData';
import type { DatabricksQueryResult, PlanningSession, Store } from './types';
import {
  DOW_PRESETS,
  buildDayFactors,
  buildMonthSummaries,
  buildStorePlan,
  computeNewStorePlanBase,
  defaultHolidays,
  expandClosuresToHolidays,
} from './utils/dowEngine';
import { exportWorkbookToFile } from './utils/excelExport';
import { deleteSession, duplicateSession, listSessions, saveSession } from './utils/sessionStorage';

const YEARS = [2025, 2026, 2027, 2028];

function makeDefaultSession(): PlanningSession {
  return {
    id: 'session-2027-databricks-baseline',
    year: 2027,
    // Drives both the in-workbook titles and the exported filename
    // (POC_Prototype_2027_Planned_Sales_Workbook_2027.xlsx).
    name: 'POC Prototype 2027 Planned Sales Workbook',
    description: 'Unity Catalog Serverless ML Forecast across East & West store network.',
    author: 'victor.yamaykin@mersgoodwill.org',
    lastUpdated: new Date().toISOString(),
    forecastRunTimestamp: 'Not yet run',
    totalPlannedSales: 157100000,
    selectedStoreId: 'ALL',
    dowWeights: { ...DOW_PRESETS.recommended },
    dowPreset: 'recommended',
    peakDays: ['Saturday', 'Friday'],
    monthlyManualDeltas: {},
    storeOverrides: {},
    isCommitted: false,
    tags: ['Forecast', 'FY2027', 'Databricks'],
    recommendedPlan: 150000000,
  };
}

/**
 * Splits the Recommended Plan across stores in proportion to each store's base sales. A continuing
 * store's base is its own history; a new store has none, so it inherits the average of up to three
 * regional comps; a closed store gets nothing. The shares are computed inline — there is no named
 * reallocation factor anywhere in the app or the workbook — and they sum to the Recommended Plan
 * exactly. Once Run Forecast supplies real per-store numbers from Databricks, those overwrite these
 * shares store by store.
 */
function computeForecastedBases(stores: Store[], recommendedPlan: number) {
  const continuing = stores.filter((s) => s.status === 'Continuing');

  const rawBases = new Map<string, number>();
  for (const store of stores) {
    if (store.status === 'Continuing') {
      rawBases.set(store.code, store.fy2026Actual);
    } else if (store.status === 'Closed') {
      rawBases.set(store.code, 0);
    } else {
      const comps = continuing.filter((s) => s.region === store.region).slice(0, 3);
      rawBases.set(store.code, computeNewStorePlanBase(comps.map((c) => c.fy2026Actual)));
    }
  }

  const rawTotal = Array.from(rawBases.values()).reduce((s, v) => s + v, 0);
  const bases = new Map<string, number>();
  for (const [code, raw] of rawBases) {
    bases.set(code, rawTotal > 0 ? (raw / rawTotal) * recommendedPlan : 0);
  }
  return bases;
}

/** Applies each store's COO dollar override (session.storeOverrides[code].annualPlanBase) on top
 * of the algorithmic forecast, so an adjustment actually flows through to the daily schedule. */
function applyStoreOverrides(forecastedBases: Map<string, number>, overrides: PlanningSession['storeOverrides']) {
  const effective = new Map(forecastedBases);
  for (const [code, override] of Object.entries(overrides)) {
    if (override.annualPlanBase !== undefined) effective.set(code, override.annualPlanBase);
  }
  return effective;
}

/**
 * Parses the gold.retail_data_science.aggregate_sales_forecast result into a store-code -> forecast map.
 * Column names are matched case-insensitively since the exact schema isn't fixed yet: looks for a
 * column containing "code" for the store code, and one containing "sales" or "forecast" for the value.
 */
function parseAggSalesForecast(result: DatabricksQueryResult): Map<string, number> {
  const codeCol = result.columns.find((c) => /code/i.test(c));
  const valueCol = result.columns.find((c) => /sales|forecast|planned/i.test(c));
  const forecasts = new Map<string, number>();
  if (!codeCol || !valueCol) return forecasts;
  for (const row of result.rows) {
    const code = row[codeCol];
    const value = Number(row[valueCol]);
    if (typeof code === 'string' && code && Number.isFinite(value)) {
      forecasts.set(code, value);
    }
  }
  return forecasts;
}

export default function App() {
  const [session, setSession] = useState<PlanningSession>(makeDefaultSession);
  const [sessions, setSessions] = useState<PlanningSession[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [isLive, setIsLive] = useState(false);
  const [runningForecast, setRunningForecast] = useState(false);
  const [forecastRunStatus, setForecastRunStatus] = useState<string | null>(null);
  const [databricksForecasts, setDatabricksForecasts] = useState<Map<string, number>>(new Map());

  useEffect(() => {
    setSessions(listSessions());
  }, []);

  useEffect(() => {
    fetch('/api/databricks/status')
      .then((r) => (r.ok ? r.json() : { connected: false }))
      .then((data) => setIsLive(Boolean(data.connected)))
      .catch(() => setIsLive(false));
  }, []);

  const holidays = useMemo(() => session.holidays ?? defaultHolidays(session.year), [session.holidays, session.year]);
  const dayFactors = useMemo(() => buildDayFactors(session.year, session.dowWeights, holidays), [session.year, session.dowWeights, holidays]);

  const algorithmicBases = useMemo(
    () => computeForecastedBases(STORES, session.recommendedPlan ?? 0),
    [session.recommendedPlan],
  );
  const forecastedBases = useMemo(() => {
    const overlaid = new Map(algorithmicBases);
    for (const [code, value] of databricksForecasts) overlaid.set(code, value);
    return overlaid;
  }, [algorithmicBases, databricksForecasts]);
  const bases = useMemo(
    () => applyStoreOverrides(forecastedBases, session.storeOverrides),
    [forecastedBases, session.storeOverrides],
  );

  const totalNetworkPlan = useMemo(() => Array.from(bases.values()).reduce((s, v) => s + v, 0), [bases]);

  const eastTotal = useMemo(
    () => STORES.filter((s) => s.region === 'East').reduce((s, st) => s + (bases.get(st.code) ?? 0), 0),
    [bases],
  );
  const westTotal = useMemo(
    () => STORES.filter((s) => s.region === 'West').reduce((s, st) => s + (bases.get(st.code) ?? 0), 0),
    [bases],
  );

  const monthSummaries = useMemo(
    () => buildMonthSummaries(session.year, dayFactors, totalNetworkPlan),
    [session.year, dayFactors, totalNetworkPlan],
  );

  const selectedStore = STORES.find((s) => s.code === session.selectedStoreId);
  const selectedStoreClosures = selectedStore ? session.storeOverrides[selectedStore.code]?.closures ?? [] : [];
  const selectedStoreDayFactors = useMemo(() => {
    if (!selectedStore || selectedStoreClosures.length === 0) return dayFactors;
    return buildDayFactors(session.year, session.dowWeights, [...holidays, ...expandClosuresToHolidays(selectedStoreClosures)]);
  }, [selectedStore, selectedStoreClosures, session.year, session.dowWeights, holidays]);

  const selectedStorePlan = useMemo(() => {
    if (!selectedStore) return null;
    const base = bases.get(selectedStore.code) ?? 0;
    return buildStorePlan(selectedStore, base, selectedStoreDayFactors);
  }, [selectedStore, bases, selectedStoreDayFactors]);

  const [previewOpen, setPreviewOpen] = useState(false);

  const handleOverrideChange = (code: string, annualPlanBase: number | undefined) => {
    setSession((s) => {
      const storeOverrides = { ...s.storeOverrides };
      if (annualPlanBase === undefined) {
        delete storeOverrides[code];
      } else {
        storeOverrides[code] = { ...storeOverrides[code], annualPlanBase };
      }
      return { ...s, storeOverrides };
    });
  };

  const handleClosuresChange = (code: string, closures: PlanningSession['storeOverrides'][string]['closures']) => {
    setSession((s) => {
      const storeOverrides = { ...s.storeOverrides };
      storeOverrides[code] = { ...storeOverrides[code], closures };
      return { ...s, storeOverrides };
    });
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      await exportWorkbookToFile(session, STORES);
    } finally {
      setExporting(false);
    }
  };

  const handleRunForecast = async () => {
    setRunningForecast(true);
    setForecastRunStatus(null);
    try {
      const res = await fetch('/api/databricks/run-forecast', { method: 'POST' });
      const result: DatabricksQueryResult & { error?: string; note?: string } = await res.json();
      if (!res.ok) {
        setForecastRunStatus(`Forecast run failed: ${result.error ?? 'unknown error'}`);
        return;
      }
      const forecasts = parseAggSalesForecast(result);
      setDatabricksForecasts(forecasts);
      if (result.source === 'mock') {
        setForecastRunStatus('Databricks not connected — running in mock mode, no live rows returned.');
      } else if (forecasts.size === 0) {
        setForecastRunStatus(`gold.retail_data_science.aggregate_sales_forecast returned ${result.rows.length} row(s), but no store code / forecast columns could be matched.`);
      } else {
        setForecastRunStatus(`Updated ${forecasts.size} store forecast(s) from gold.retail_data_science.aggregate_sales_forecast.`);
      }
    } catch (err) {
      setForecastRunStatus(`Forecast run failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setRunningForecast(false);
    }
  };

  return (
    <div className="min-h-screen">
      <Header
        year={session.year}
        years={YEARS}
        onYearChange={(year) => setSession((s) => ({ ...s, year }))}
        isLive={isLive}
        onExport={handleExport}
        exporting={exporting}
        onRunForecast={handleRunForecast}
        runningForecast={runningForecast}
      />

      {forecastRunStatus && (
        <div className="px-6 py-1.5 text-xs" style={{ backgroundColor: 'var(--mers-ice)', color: 'var(--mers-navy)' }}>
          {forecastRunStatus}
        </div>
      )}

      <div className="flex items-center justify-between px-6 py-2">
        <div className="text-xs text-[var(--mers-slate)]">
          Total network plan: {totalNetworkPlan.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setImportOpen(true)}
            className="rounded px-3 py-1 text-xs font-semibold text-[var(--mers-navy)]"
            style={{ border: '1px solid var(--mers-ice)' }}
          >
            Import JSON
          </button>
          <button
            onClick={() => setDrawerOpen(true)}
            className="rounded px-3 py-1 text-xs font-semibold text-white"
            style={{ backgroundColor: 'var(--mers-navy)' }}
          >
            Sessions ({sessions.length})
          </button>
        </div>
      </div>

      <div className="px-6">
        <ModelComparisonTable />
      </div>

      <main className="grid grid-cols-1 gap-4 p-6 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-1">
          <DOWWeightsController
            weights={session.dowWeights}
            onChange={(dowWeights) => setSession((s) => ({ ...s, dowWeights, dowPreset: 'custom' }))}
            preset={session.dowPreset}
            onPresetChange={(dowPreset) => setSession((s) => ({ ...s, dowPreset }))}
          />
          <WeekdayCountsTable years={YEARS} currentYear={session.year} />
          <SalesRedistributor
            recommendedPlan={session.recommendedPlan ?? 0}
            cooAdjustedPlan={totalNetworkPlan}
            eastTotal={eastTotal}
            westTotal={westTotal}
            onRecommendedPlanChange={(v) => setSession((s) => ({ ...s, recommendedPlan: v }))}
          />
          <StoreSelector
            stores={STORES}
            selectedStoreId={session.selectedStoreId}
            onSelect={(selectedStoreId) => setSession((s) => ({ ...s, selectedStoreId }))}
          />
          {selectedStorePlan && (
            <button
              onClick={() => setPreviewOpen(true)}
              className="w-full rounded px-3 py-2 text-xs font-semibold text-white"
              style={{ backgroundColor: 'var(--mers-blue)' }}
            >
              View Daily Schedule for {selectedStore?.name}
            </button>
          )}
        </div>

        <div className="space-y-4 lg:col-span-2">
          <ScenarioComparisonChart months={monthSummaries} currentYearLabel={`FY${session.year} Plan`} />
          <MonthlyAllocationTable months={monthSummaries} />
        </div>

        <div className="lg:col-span-3">
          <StoreForecastAdjustmentTable
            stores={STORES}
            forecastedBases={forecastedBases}
            storeOverrides={session.storeOverrides}
            onOverrideChange={handleOverrideChange}
          />
        </div>
      </main>

      {previewOpen && selectedStorePlan && selectedStore && (
        <DailyPreviewModal
          plan={selectedStorePlan}
          dayFactors={selectedStoreDayFactors}
          closures={selectedStoreClosures}
          onClosuresChange={(closures) => handleClosuresChange(selectedStore.code, closures)}
          onClose={() => setPreviewOpen(false)}
        />
      )}

      <SessionsRightDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        sessions={sessions}
        activeSessionId={session.id}
        onLoad={(s) => {
          setSession(s);
          setDrawerOpen(false);
        }}
        onSaveCurrent={(name) => {
          const toSave = { ...session, name, id: session.id || `session-${session.year}-${Date.now()}` };
          saveSession(toSave);
          setSession(toSave);
          setSessions(listSessions());
        }}
        onDuplicate={(s) => {
          const copy = duplicateSession(s, `${s.name} (Copy)`);
          saveSession(copy);
          setSessions(listSessions());
        }}
        onDelete={(id) => {
          deleteSession(id);
          setSessions(listSessions());
        }}
      />

      {importOpen && (
        <JsonImportModal
          onClose={() => setImportOpen(false)}
          onImport={(imported) => {
            setSession(imported);
            saveSession(imported);
            setSessions(listSessions());
          }}
        />
      )}
    </div>
  );
}
