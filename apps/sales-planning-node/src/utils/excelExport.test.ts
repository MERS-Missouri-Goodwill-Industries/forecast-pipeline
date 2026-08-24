import { STORES } from '../data/storesData';
import type { PlanningSession } from '../types';
import {
  DOW_PRESETS,
  buildDayFactors,
  buildStorePlan,
  defaultHolidays,
  expandClosuresToHolidays,
} from './dowEngine';
import { buildWorkbook } from './excelExport';

interface CriterionResult {
  criterion: string;
  passed: boolean;
  detail: string;
}

function makeTestSession(): PlanningSession {
  return {
    id: 'session-test',
    year: 2027,
    name: 'Acceptance Test Scenario',
    description: 'Automated acceptance test fixture',
    author: 'test-suite',
    lastUpdated: new Date(2026, 0, 1).toISOString(),
    forecastRunTimestamp: 'test',
    totalPlannedSales: 149570609,
    selectedStoreId: 'ALL',
    dowWeights: { ...DOW_PRESETS.recommended },
    dowPreset: 'recommended',
    peakDays: ['Saturday', 'Friday'],
    monthlyManualDeltas: {},
    storeOverrides: {},
    isCommitted: false,
    tags: ['test'],
    recommendedPlan: 150000000,
  };
}

function near(a: number, b: number, epsilon = 1e-6): boolean {
  return Math.abs(a - b) < epsilon;
}

export async function runExcelExportAcceptanceTests(): Promise<CriterionResult[]> {
  const results: CriterionResult[] = [];
  const session = makeTestSession();
  const stores = STORES;
  const holidays = defaultHolidays(session.year);
  const dayFactors = buildDayFactors(session.year, session.dowWeights, holidays);

  // Criterion 1 + 2 use a real store plan computed via the same math the workbook formulas encode.
  // A continuing store's plan base is its own forecast — no network reallocation factor.
  const continuingStores = stores.filter((s) => s.status === 'Continuing');
  const plans = continuingStores.slice(0, 5).map((store) =>
    buildStorePlan(store, store.fy2026Actual, dayFactors),
  );

  const allZeroVariance = plans.every((p) => near(p.fullYearTotal, p.annualPlanBase, 0.01));
  results.push({
    criterion: 'Criterion 1 (Zero Difference)',
    passed: allZeroVariance,
    detail: `Checked ${plans.length} store plans; max diff = ${Math.max(...plans.map((p) => Math.abs(p.fullYearTotal - p.annualPlanBase))).toFixed(4)}`,
  });

  const totals = plans.map((p) => p.fullYearTotal);
  const uniqueTotals = new Set(totals.map((t) => t.toFixed(2)));
  const basesDiffer = new Set(plans.map((p) => p.annualPlanBase.toFixed(2))).size === plans.length;
  results.push({
    criterion: 'Criterion 2 (Store Isolation)',
    passed: !basesDiffer || uniqueTotals.size === totals.length,
    detail: `${uniqueTotals.size} distinct full-year totals across ${plans.length} stores with distinct bases`,
  });

  // Criterion 3: closed-day formula logic — LEN>0 style check reproduced against dayFactors directly.
  const holidayDates = new Set(holidays.map((h) => h.date));
  const closedDaysCounted = dayFactors.filter((d) => holidayDates.has(d.date) && d.holidayLabel !== '').length;
  results.push({
    criterion: 'Criterion 3 (Closure Integrity)',
    passed: closedDaysCounted === holidays.length,
    detail: `${closedDaysCounted} closed days matched against ${holidays.length} seeded holidays`,
  });

  // Criterion 4: percentage normalization invariants.
  const totalAnnualPct = dayFactors.reduce((s, d) => s + d.dayPctOfAnnual, 0);
  const monthPctSums = new Array(13).fill(0);
  for (const d of dayFactors) monthPctSums[d.month] += d.dayPctOfMonth;
  const allMonthsSumToOne = monthPctSums.slice(1).every((v) => near(v, 1, 1e-6));
  const totalMonthPct = monthPctSums.slice(1).reduce((s, v) => s + v, 0);
  results.push({
    criterion: 'Criterion 4 (Percentage Normalization)',
    passed: near(totalAnnualPct, 1, 1e-6) && allMonthsSumToOne && near(totalMonthPct, 12, 1e-6),
    detail: `Annual %=${totalAnnualPct.toFixed(6)}, sum of month %=${totalMonthPct.toFixed(6)} (expect 1.000000 / 12.000000)`,
  });

  // Criterion 5: Matrix reconciliation — network total across sampled stores equals sum of their bases * (no growth double count).
  const networkTotalFromPlans = plans.reduce((s, p) => s + p.fullYearTotal, 0);
  const networkTotalFromBases = plans.reduce((s, p) => s + p.annualPlanBase, 0);
  results.push({
    criterion: 'Criterion 5 (Matrix Reconciliation)',
    passed: near(networkTotalFromPlans, networkTotalFromBases, 0.05),
    detail: `Sum of store totals ${networkTotalFromPlans.toFixed(2)} vs sum of plan bases ${networkTotalFromBases.toFixed(2)}`,
  });

  // Criterion 6: zeroing a weekday zeroes that weekday's rows, monthly totals unchanged (redistributed to other days).
  const zeroedWeights = { ...session.dowWeights, Monday: 0 };
  const dayFactorsZeroed = buildDayFactors(session.year, zeroedWeights, holidays);
  const mondayRowsZero = dayFactorsZeroed.filter((d) => d.weekday === 'Monday').every((d) => d.dayPctOfAnnual === 0);
  const monthTotalsUnchanged = (() => {
    const before = new Array(13).fill(0);
    const after = new Array(13).fill(0);
    for (const d of dayFactors) before[d.month] += d.dayPctOfMonth;
    for (const d of dayFactorsZeroed) after[d.month] += d.dayPctOfMonth;
    return before.slice(1).every((v, i) => near(v, after[i + 1], 1e-6));
  })();
  results.push({
    criterion: 'Criterion 6 (DOW Sensitivity)',
    passed: mondayRowsZero && monthTotalsUnchanged,
    detail: `Monday rows zeroed: ${mondayRowsZero}; month % totals unchanged: ${monthTotalsUnchanged}`,
  });

  // Criterion 7: the seven weekday shares are constant all year and identical for every store.
  // An open Friday in January must be worth exactly the same share of the year as one in July.
  const testStore = continuingStores[0];
  const testBase = testStore.fy2026Actual;
  const openByWeekday = new Map<string, number[]>();
  for (const d of dayFactors) {
    if (d.holidayLabel) continue;
    const list = openByWeekday.get(d.weekday) ?? [];
    list.push(d.dayPctOfAnnual);
    openByWeekday.set(d.weekday, list);
  }
  let widestSpread = 0;
  for (const shares of openByWeekday.values()) {
    widestSpread = Math.max(widestSpread, Math.max(...shares) - Math.min(...shares));
  }
  const holidaysAreZero = dayFactors.filter((d) => d.holidayLabel).every((d) => d.dayPctOfAnnual === 0);
  results.push({
    criterion: 'Criterion 7 (Weekday Share Is Flat All Year)',
    passed: widestSpread < 1e-12 && holidaysAreZero && openByWeekday.size === 7,
    detail: `widest within-weekday spread across the year = ${widestSpread.toExponential(2)}; closed days at 0%: ${holidaysAreZero}`,
  });

  // Criterion 8: closing days removes exactly those days from their own month and leaves the
  // annual commitment intact — the plan is redistributed across the year, not lost. Zeroing a
  // whole month must take that month to $0 without changing the store's full-year total.
  const fullMonthClosure = expandClosuresToHolidays([{ start: `${session.year}-06-01`, end: `${session.year}-06-30`, label: 'Renovation' }]);
  const dfJuneClosed = buildDayFactors(session.year, session.dowWeights, [...holidays, ...fullMonthClosure]);
  const planJuneClosed = buildStorePlan(testStore, testBase, dfJuneClosed);
  const juneAfterClose = planJuneClosed.monthlyPlanned[5];
  const annualAfterClose = planJuneClosed.fullYearTotal;

  const partialClosure = expandClosuresToHolidays([{ start: `${session.year}-06-10`, end: `${session.year}-06-12`, label: 'Renovation' }]);
  const dfPartial = buildDayFactors(session.year, session.dowWeights, [...holidays, ...partialClosure]);
  const planPartial = buildStorePlan(testStore, testBase, dfPartial);
  const baseline = buildStorePlan(testStore, testBase, dayFactors);
  const closedDayValue = [9, 10, 11]
    .map((i) => baseline.dailyPlanned.findIndex((d) => d.date === `${session.year}-06-${String(i + 1).padStart(2, '0')}`))
    .reduce((s, idx) => s + baseline.dailyPlanned[idx].amount, 0);
  const junePartialDrop = baseline.monthlyPlanned[5] - planPartial.monthlyPlanned[5];

  results.push({
    criterion: 'Criterion 8 (Store Closure Handling)',
    passed: near(juneAfterClose, 0, 0.01)
      && near(annualAfterClose, testBase, 0.05)
      && near(planPartial.fullYearTotal, testBase, 0.05)
      && junePartialDrop > 0,
    detail: `full-month close: June $${juneAfterClose.toFixed(2)}, annual still $${annualAfterClose.toFixed(2)} (base $${testBase.toFixed(2)}); `
      + `3-day close: June drops $${junePartialDrop.toFixed(2)} (those days were worth $${closedDayValue.toFixed(2)}), annual held at $${planPartial.fullYearTotal.toFixed(2)}`,
  });

  // Structural: each monthly $ header cell must be the plan base times the SUM of that month's
  // Day % of Annual rows, so the month is literally its own days added up and goes to $0 when they
  // are all closed - keeping Diff (T2) a pure error signal.
  const closureSessionForStructTest: PlanningSession = {
    ...session,
    storeOverrides: { [continuingStores[0].code]: { closures: [{ start: `${session.year}-06-01`, end: `${session.year}-06-30`, label: 'Renovation' }] } },
  };
  const closureWb = buildWorkbook(closureSessionForStructTest, [continuingStores[0]]);
  const closedStoreSheet = closureWb.worksheets.find((s) => s.name === continuingStores[0].sheetName.slice(0, 31));
  const juneHeaderFormula = closedStoreSheet?.getCell('J2').value;
  const juneHeaderHasScaling =
    typeof juneHeaderFormula === 'object' && juneHeaderFormula !== null && 'formula' in juneHeaderFormula &&
    (juneHeaderFormula as { formula: string }).formula.includes('SUM(E');
  // Structural: the month's last day (January ends on row 39) must carry BOTH month totals — H
  // adding up Recommended (column C) and I adding up COO Adjusted (column D) — and row 3 must add
  // up the daily COO column so clearing days moves the month.
  const fx = (c?: string) => {
    const v = closedStoreSheet?.getCell(c ?? '').value;
    return typeof v === 'object' && v !== null && 'formula' in v ? (v as { formula: string }).formula : '';
  };
  const twoMonthTotals = /^SUM\(C\d+:C\d+\)$/.test(fx('H39')) && /^SUM\(D\d+:D\d+\)$/.test(fx('I39'));
  const row3SumsDailyCoo = /^SUM\(D\d+:D\d+\)$/.test(fx('E3'));
  results.push({
    criterion: 'Structural (Dual Month Totals + COO Row Sums Daily)',
    passed: twoMonthTotals && row3SumsDailyCoo,
    detail: `H39=${fx('H39')} I39=${fx('I39')} E3=${fx('E3')}`,
  });

  results.push({
    criterion: 'Structural (Monthly Header Sums Its Own Days)',
    passed: juneHeaderHasScaling,
    detail: juneHeaderHasScaling
      ? 'June header cell (J2) sums its own Day %% of Annual rows.'
      : `June header cell (J2) formula missing scaling factor: ${JSON.stringify(juneHeaderFormula)}`,
  });

  // Structural smoke test: the workbook actually builds with the expected sheet set and per-store Diff formula.
  const wb = buildWorkbook(session, stores.slice(0, 8));
  const sheetNames = wb.worksheets.map((s) => s.name);
  const requiredSheets = ['How_To_Use', 'Exec_Summary', 'Calendar_Inputs', 'Day_Factors', 'Plan_Inputs', 'All_Stores_Summary', 'Monthly_Matrix', 'Daily_Disaggregated_Plan'];
  const hasAllSheets = requiredSheets.every((n) => sheetNames.includes(n));
  const firstStoreSheet = wb.worksheets.find((s) => s.name === stores[0].sheetName.slice(0, 31));
  const diffFormula = firstStoreSheet?.getCell('Q6').value;
  const diffFormulaIsCorrect = typeof diffFormula === 'object' && diffFormula !== null && 'formula' in diffFormula && (diffFormula as { formula: string }).formula === 'Q3-Q2';
  results.push({
    criterion: 'Structural (Workbook Shape)',
    passed: hasAllSheets && diffFormulaIsCorrect,
    detail: `Sheets present: ${hasAllSheets}; Q6 Diff formula check: ${diffFormulaIsCorrect}`,
  });

  // Regression guard: a sheet-qualified reference (e.g. 'Day_Factors'!...) must never be followed
  // directly by a function call — that produces invalid Excel syntax like 'Day_Factors'!COUNTIF(...)
  // instead of COUNTIF('Day_Factors'!...), which Excel silently strips on open ("file repair").
  const badRefPattern = /![A-Za-z]{2,}\(/;
  const badFormulas: string[] = [];
  wb.worksheets.forEach((s) => {
    s.eachRow((r) => {
      r.eachCell((c) => {
        const v = c.value;
        if (v && typeof v === 'object' && 'formula' in v) {
          const f = (v as { formula: string }).formula;
          if (badRefPattern.test(f)) badFormulas.push(`${s.name}!${c.address}: ${f}`);
        }
      });
    });
  });
  results.push({
    criterion: 'Structural (Formula Syntax Guard)',
    passed: badFormulas.length === 0,
    detail: badFormulas.length === 0 ? 'No malformed sheet-qualified function calls found.' : `Found: ${badFormulas.slice(0, 5).join('; ')}`,
  });

  return results;
}
