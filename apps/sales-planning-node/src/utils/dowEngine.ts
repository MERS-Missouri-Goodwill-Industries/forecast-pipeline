import { WEEKDAYS } from '../types';
import type { DayFactor, DOWWeights, Holiday, MonthSummary, Store, StoreClosureRange, StorePlan, Weekday } from '../types';

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

// Presets are stored already normalized — each set totals ~100% — so the weight you see is the
// weight you edit, with no separate "entered vs normalized" pair to reconcile. (The two measured
// sets total 100.01% because they are rounded to 2dp at source; normalizeDowWeights rescales them,
// so the rounding has no effect on any planned dollar figure.)
export const DOW_PRESETS: Record<string, DOWWeights> = {
  // Each day's share of a typical week, derived from actual sales — the recommended weighting.
  recommended: {
    Monday: 0.1341, Tuesday: 0.1298, Wednesday: 0.1302, Thursday: 0.1301,
    Friday: 0.1561, Saturday: 0.1898, Sunday: 0.1300,
  },
  // The weighting baked into the current Excel planning workbook, kept for side-by-side comparison.
  excel_plan: {
    Monday: 0.1392, Tuesday: 0.1289, Wednesday: 0.1237, Thursday: 0.1392,
    Friday: 0.1495, Saturday: 0.1959, Sunday: 0.1237,
  },
  even: {
    Monday: 1 / 7, Tuesday: 1 / 7, Wednesday: 1 / 7, Thursday: 1 / 7,
    Friday: 1 / 7, Saturday: 1 / 7, Sunday: 1 / 7,
  },
};

/**
 * Rescales the weekday weights so they sum to exactly 1.0. Presets already do, but this keeps the
 * math correct if the COO edits a weight and the column no longer totals 100%.
 */
export function normalizeDowWeights(weights: DOWWeights): DOWWeights {
  const total = WEEKDAYS.reduce((sum, d) => sum + (weights[d] ?? 0), 0);
  const normalized = {} as DOWWeights;
  for (const d of WEEKDAYS) {
    normalized[d] = total > 0 ? (weights[d] ?? 0) / total : 1 / 7;
  }
  return normalized;
}

function easterDate(year: number): Date {
  // Anonymous Gregorian algorithm.
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(year, month - 1, day);
}

function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function defaultHolidays(year: number): Holiday[] {
  return [
    { date: toIsoDate(new Date(year, 0, 1)), label: "New Year's Day" },
    { date: toIsoDate(easterDate(year)), label: 'Easter' },
    { date: toIsoDate(new Date(year, 6, 4)), label: 'July 4th' },
    { date: toIsoDate(fourthThursdayOfNovember(year)), label: 'Thanksgiving' },
    { date: toIsoDate(new Date(year, 11, 24)), label: 'Christmas Eve' },
    { date: toIsoDate(new Date(year, 11, 25)), label: 'Christmas Day' },
  ];
}

/**
 * Expands per-store closure date ranges (e.g. a renovation) into individual Holiday entries so
 * they can be merged with the network-wide holiday list and fed straight into buildDayFactors —
 * reusing the exact same intra-month redistribution math, just with a store-specific closed-day
 * set instead of (or in addition to) the network's.
 */
export function expandClosuresToHolidays(closures: StoreClosureRange[]): Holiday[] {
  const expanded: Holiday[] = [];
  for (const closure of closures) {
    const start = new Date(`${closure.start}T00:00:00`);
    const end = new Date(`${closure.end}T00:00:00`);
    const label = closure.label?.trim() || 'Store Closure';
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      expanded.push({ date: toIsoDate(d), label });
    }
  }
  return expanded;
}

function fourthThursdayOfNovember(year: number): Date {
  const d = new Date(year, 10, 1);
  const firstThursdayOffset = (4 - d.getDay() + 7) % 7;
  d.setDate(1 + firstThursdayOffset + 21);
  return d;
}

const WEEKDAY_BY_JS_DAY: Weekday[] = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function isLeapYear(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

function daysInMonth(year: number, monthIndex0: number): number {
  return new Date(year, monthIndex0 + 1, 0).getDate();
}

/** How many of each weekday (Monday..Sunday) fall in a given calendar year — 52 for most, one
 * weekday gets 53 in a 365-day year, two get 53 in a 366-day (leap) year. */
export function countWeekdaysInYear(year: number): Record<Weekday, number> {
  const counts: Record<Weekday, number> = {
    Monday: 0, Tuesday: 0, Wednesday: 0, Thursday: 0, Friday: 0, Saturday: 0, Sunday: 0,
  };
  const totalDays = isLeapYear(year) ? 366 : 365;
  const start = new Date(year, 0, 1);
  for (let i = 0; i < totalDays; i += 1) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    counts[WEEKDAY_BY_JS_DAY[d.getDay()]] += 1;
  }
  return counts;
}

/**
 * Builds the 365/366-day factor table.
 *
 * `dayPctOfAnnual` is the day's share of the WHOLE YEAR: its normalized weekday weight over the
 * combined weight of every open day. Closed days are 0. Because the denominator is annual rather
 * than monthly, the seven weekday values are identical in every month and identical for every
 * store — an open Friday is the same slice of the year in January as in July — and the column
 * sums to exactly 1.0000. Daily dollars are simply annualPlanBase * dayPctOfAnnual.
 *
 * Closing a day therefore lowers that day's month and lifts the rest of the year slightly (the
 * annual total is preserved). This is annual redistribution, not the older within-the-month rule:
 * a month is just the sum of the days left in it.
 *
 * `dayPctOfMonth` is reference only — the same day expressed against its own month, so it still
 * sums to 1.0000 per month. Nothing is priced off it.
 */
export function buildDayFactors(year: number, dowWeights: DOWWeights, holidays: Holiday[]): DayFactor[] {
  const normalized = normalizeDowWeights(dowWeights);
  const holidaySet = new Map(holidays.map((h) => [h.date, h.label]));

  const raw: Omit<DayFactor, 'dayPctOfAnnual' | 'dayPctOfMonth'>[] = [];
  const totalDays = isLeapYear(year) ? 366 : 365;
  const start = new Date(year, 0, 1);
  for (let i = 0; i < totalDays; i += 1) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const iso = toIsoDate(d);
    const weekday = WEEKDAY_BY_JS_DAY[d.getDay()];
    raw.push({
      date: iso,
      isoDate: iso,
      month: d.getMonth() + 1,
      monthName: MONTH_NAMES[d.getMonth()],
      weekday,
      holidayLabel: holidaySet.get(iso) ?? '',
    });
  }

  // One denominator for the whole year: the combined weight of every OPEN day. So an open Friday
  // carries the same share in January as in July — the seven weekday values are the same all year
  // and the same for every store — and the column totals exactly 1.0.
  const totalOpenWeight = raw.reduce((sum, d) => sum + (d.holidayLabel ? 0 : normalized[d.weekday]), 0);

  const withAnnualPct = raw.map((day) => ({
    ...day,
    dayPctOfAnnual:
      day.holidayLabel || totalOpenWeight === 0 ? 0 : normalized[day.weekday] / totalOpenWeight,
  }));

  // Reference only — the same day expressed against its own month. Nothing is priced off this.
  const monthTotal = new Array(13).fill(0);
  for (const day of withAnnualPct) monthTotal[day.month] += day.dayPctOfAnnual;

  return withAnnualPct.map((day) => ({
    ...day,
    dayPctOfMonth: monthTotal[day.month] === 0 ? 0 : day.dayPctOfAnnual / monthTotal[day.month],
  }));
}

export function buildMonthSummaries(year: number, dayFactors: DayFactor[], annualPlanBase: number): MonthSummary[] {
  const summaries: MonthSummary[] = [];
  for (let m = 1; m <= 12; m += 1) {
    const daysInM = dayFactors.filter((d) => d.month === m);
    const closedDays = daysInM.filter((d) => d.holidayLabel !== '').length;
    const pctOfAnnual = daysInM.reduce((sum, d) => sum + d.dayPctOfAnnual, 0);
    summaries.push({
      month: m,
      monthName: MONTH_NAMES[m - 1],
      days: daysInMonth(year, m - 1),
      closedDays,
      sellingDays: daysInMonth(year, m - 1) - closedDays,
      pctOfAnnual,
      plannedSales: annualPlanBase * pctOfAnnual,
    });
  }
  return summaries;
}

/** New Store Plan Base_j = AVERAGE(comp bases) — a new store has no history of its own. */
export function computeNewStorePlanBase(compBases: number[]): number {
  if (compBases.length === 0) return 0;
  return compBases.reduce((s, v) => s + v, 0) / compBases.length;
}

/** Daily Planned Sales_t = AnnualPlanBase * Day%Annual_t; zero-variance by construction. */
export function buildStorePlan(store: Store, annualPlanBase: number, dayFactors: DayFactor[]): StorePlan {
  const monthlyPlanned = new Array(12).fill(0);
  const dailyPlanned = dayFactors.map((day) => {
    const amount = annualPlanBase * day.dayPctOfAnnual;
    monthlyPlanned[day.month - 1] += amount;
    return { date: day.date, amount };
  });

  const fullYearTotal = dailyPlanned.reduce((s, d) => s + d.amount, 0);

  return { store, annualPlanBase, monthlyPlanned, dailyPlanned, fullYearTotal };
}
