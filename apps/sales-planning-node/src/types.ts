export type Weekday =
  | 'Monday'
  | 'Tuesday'
  | 'Wednesday'
  | 'Thursday'
  | 'Friday'
  | 'Saturday'
  | 'Sunday';

export const WEEKDAYS: Weekday[] = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];

export type Region = 'East' | 'West';

export type StoreStatus = 'Continuing' | 'New store' | 'Closed';

export interface Store {
  code: string;
  name: string;
  sheetName: string;
  region: Region;
  status: StoreStatus;
  fy2025Actual: number;
  fy2026Actual: number;
  transactionCount2026: number;
  avgTransaction2026: number;
  itemsSold2026: number;
  avgItemValue2026: number;
  donationCount2026: number;
  compStoreCodes?: string[];
}

export type DOWWeights = Record<Weekday, number>;

export interface Holiday {
  date: string;
  label: string;
}

export type MonthlyManualDeltas = Record<string, number>;

export interface StoreClosureRange {
  start: string; // ISO date, inclusive
  end: string; // ISO date, inclusive
  label?: string;
}

export type StoreOverrides = Record<string, { annualPlanBase?: number; closures?: StoreClosureRange[] }>;

export interface PlanningSession {
  id: string;
  year: number;
  name: string;
  description: string;
  author: string;
  lastUpdated: string;
  forecastRunTimestamp: string;
  totalPlannedSales: number;
  eastPlannedSales?: number;
  westPlannedSales?: number;
  selectedStoreId: string;
  dowWeights: DOWWeights;
  dowPreset: string;
  peakDays: Weekday[];
  monthlyManualDeltas: MonthlyManualDeltas;
  storeOverrides: StoreOverrides;
  isCommitted: boolean;
  tags: string[];
  recommendedPlan?: number;
  holidays?: Holiday[];
}

export interface DayFactor {
  date: string;
  isoDate: string;
  month: number;
  monthName: string;
  weekday: Weekday;
  holidayLabel: string;
  dayPctOfAnnual: number;
  dayPctOfMonth: number;
}

export interface MonthSummary {
  month: number;
  monthName: string;
  days: number;
  closedDays: number;
  sellingDays: number;
  pctOfAnnual: number;
  plannedSales: number;
}

export interface StorePlan {
  store: Store;
  annualPlanBase: number;
  monthlyPlanned: number[];
  dailyPlanned: { date: string; amount: number }[];
  fullYearTotal: number;
}

export interface DatabricksQueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
  runTimestamp: string;
  source: 'live' | 'mock';
}
