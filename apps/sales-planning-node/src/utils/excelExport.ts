import ExcelJS from 'exceljs';
import * as fileSaverModule from 'file-saver';
import { WEEKDAYS } from '../types';
import type { PlanningSession, Store } from '../types';
import { escapeSheetNameForFormula } from '../data/storesData';
import {
  buildDayFactors,
  buildStorePlan,
  defaultHolidays,
  expandClosuresToHolidays,
  normalizeDowWeights,
} from './dowEngine';

// file-saver ships as a CJS default export in some bundles and a named `saveAs` in others.
const fileSaver: unknown = fileSaverModule as unknown;
const saveAs: (blob: Blob, name: string) => void =
  typeof fileSaver === 'function'
    ? (fileSaver as (blob: Blob, name: string) => void)
    : ((fileSaver as { saveAs?: typeof saveAs; default?: typeof saveAs }).saveAs ??
      (fileSaver as { default?: typeof saveAs }).default) as typeof saveAs;

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const COLORS = {
  input: { font: 'FF0000FF', fill: 'FFFFFF99' },
  formula: { font: 'FF000000' },
  crossSheet: { font: 'FF008000' },
  holidayFill: 'FFFFFF00',
  newStoreFill: 'FFE6F4EA',
  note: { font: 'FFCC0000' },
};

function styleInput(cell: ExcelJS.Cell, numFmt?: string): void {
  cell.font = { name: 'Arial', size: 10, color: { argb: COLORS.input.font } };
  cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLORS.input.fill } };
  if (numFmt) cell.numFmt = numFmt;
}

function styleFormula(cell: ExcelJS.Cell, numFmt?: string): void {
  cell.font = { name: 'Arial', size: 10, color: { argb: COLORS.formula.font } };
  if (numFmt) cell.numFmt = numFmt;
}

function styleCrossSheet(cell: ExcelJS.Cell, numFmt?: string): void {
  cell.font = { name: 'Arial', size: 10, color: { argb: COLORS.crossSheet.font } };
  if (numFmt) cell.numFmt = numFmt;
}

function styleNote(cell: ExcelJS.Cell): void {
  cell.font = { name: 'Arial', size: 10, italic: true, color: { argb: COLORS.note.font } };
}

function styleHeader(cell: ExcelJS.Cell, size = 10): void {
  cell.font = { name: 'Arial', size, bold: true };
}

function ref(sheetName: string, cellRef: string): string {
  return `'${escapeSheetNameForFormula(sheetName)}'!${cellRef}`;
}

interface BuildContext {
  session: PlanningSession;
  stores: Store[];
  totalDays: number;
  dayFactorsLastRow: number; // header row (1) + totalDays
  planInputsLastRow: number; // 13 + stores.length
}

function addHowToUseSheet(wb: ExcelJS.Workbook, ctx: BuildContext): void {
  const sheet = wb.addWorksheet('How_To_Use', { properties: { tabColor: { argb: 'FFFD9D0D' } } });
  sheet.getColumn(1).width = 26;
  sheet.getColumn(2).width = 90;

  sheet.getCell('A1').value = `${ctx.session.name} — How to Use This Workbook`;
  styleHeader(sheet.getCell('A1'), 14);

  sheet.getCell('A3').value = 'Purpose';
  styleHeader(sheet.getCell('A3'));
  sheet.getCell('A4').value =
    'Turns each store’s forecast plus the COO’s day-of-week seasonality assumptions into a day-by-day, ' +
    'store-by-store sales plan. Every cell below Calendar_Inputs / Plan_Inputs is a live formula — change an ' +
    'input and the rest of the workbook recalculates. The Recommended Plan is split across stores in ' +
    'proportion to each store’s Base Sales, computed inline on every row — there is no separate ' +
    'reallocation factor to maintain.';
  sheet.mergeCells('A4:B4');
  sheet.getCell('A4').alignment = { wrapText: true };

  sheet.getCell('A6').value = 'Forecasting Methodology';
  styleHeader(sheet.getCell('A6'));
  sheet.getCell('A7').value =
    'The store-level forecast is trained and scored in Databricks against Unity Catalog data (gold.' +
    'retail_data_science), not in this workbook — this workbook takes that forecast as its starting ' +
    '"Forecasted Plan Base" and layers the DOW weighting and closure logic on top. Model accuracy is graded on ' +
    'WAPE (Weighted Absolute Percentage Error) rather than plain MAPE, since daily store sales include many ' +
    'low/zero-sales days that would distort a simple average percentage error; WAPE divides total absolute ' +
    'error by total actual sales so accuracy reflects the dollars that matter. Validation uses a rolling ' +
    '(walk-forward) window: train on a historical period, test on the period right after it, then slide both ' +
    'forward and repeat.';
  sheet.mergeCells('A7:B7');
  sheet.getCell('A7').alignment = { wrapText: true };
  sheet.getRow(7).height = 90;

  sheet.getCell('A9').value = 'Color Legend';
  styleHeader(sheet.getCell('A9'));
  const legendRows: [string, string, () => void][] = [
    ['Blue on Yellow', 'Editable input — safe to change (DOW weights, holidays, COO overrides, closure dates).', () => styleInput(sheet.getCell('A10'))],
    ['Black', 'Formula calculating within the same worksheet.', () => styleFormula(sheet.getCell('A11'))],
    ['Green', 'Cross-sheet formula reference (e.g. pulling from Plan_Inputs or Calendar_Inputs).', () => styleCrossSheet(sheet.getCell('A12'))],
    ['Red Italic', 'Assumption / audit note — explains an invariant, not something to edit.', () => styleNote(sheet.getCell('A13'))],
  ];
  legendRows.forEach(([label, desc, applyStyle], i) => {
    const row = 10 + i;
    sheet.getCell(`A${row}`).value = label;
    applyStyle();
    sheet.getCell(`B${row}`).value = desc;
  });

  sheet.getCell('A16').value = 'Sheet Index';
  styleHeader(sheet.getCell('A16'));
  const sheetIndex: [string, string][] = [
    ['Exec_Summary', 'Top-level KPIs, monthly rollup, and the master store index.'],
    ['All_Stores_Summary', 'One row per store: actuals, forecast, COO adjustment, and variance side by side.'],
    ['Monthly_Matrix', 'Every store’s 12-month totals in one grid, rolling up to the network total.'],
    ['Daily_Disaggregated_Plan', 'Flat 23k+ row feed (store × day) formatted for Power BI ingestion.'],
    ['Calendar_Inputs', 'COO control center — one editable Normalized Weight per weekday, holiday dates, and the derived monthly % of annual.'],
    ['Day_Factors', 'The 365-day engine: each weekday’s share of a week, each day’s share of the whole year, and the weekday counts behind them.'],
    ['Plan_Inputs', 'Per-store Base Sales, Forecasted Plan Base, and the COO Adjusted override, totalling to the Recommended Plan.'],
    ['[Store Name] tabs', 'One 365-day daily schedule per store. Each store’s own closure dates (columns V:X) live on that store’s sheet directly, driven entirely by Plan_Inputs and Calendar_Inputs.'],
  ];
  sheetIndex.forEach(([name, desc], i) => {
    const row = 17 + i;
    sheet.getCell(`A${row}`).value = name;
    styleFormula(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = desc;
  });

  sheet.getCell('A27').value = 'Reading a Store Tab';
  styleHeader(sheet.getCell('A27'));
  sheet.getCell('A28').value = 'Every store tab is laid out the same way. Two money columns sit side by side: '
    + 'one keeps the forecast, one is yours to change.';
  sheet.mergeCells('A28:B28');
  sheet.getCell('A28').alignment = { wrapText: true };

  const tabGuide: [string, string][] = [
    ['Column E — Day % of Annual', 'This day\'s slice of the whole year. The same seven values for every store, '
      + 'all year. A closed day reads 0%. See the Weekday Mix table on Calendar_Inputs.'],
    ['Column C — Planned Sales', 'What the forecast says: Forecasted Plan Base (B6) x Day % of Annual.'],
    ['Column D — COO Adjusted', 'What you are committing to. Starts out matching column C. '
      + 'This is the only money column you change.'],
    ['Column H — Month Total', 'That month\'s COO Adjusted days added up. Sits on the last day of each month.'],
    ['Column G — Month', 'The month name, so you can filter or scan by month.'],
    ['Cell T2 — Diff', 'Should always read $0.00. Anything else means a formula has been broken.'],
    ['B6 vs U2', 'Compare these two to see what your adjustments did to the store for the year.'],
  ];
  tabGuide.forEach(([label, desc], i) => {
    const row = 29 + i;
    sheet.getCell(`A${row}`).value = label;
    styleHeader(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = desc;
    sheet.getCell(`B${row}`).alignment = { wrapText: true };
  });

  sheet.getCell('A38').value = 'Zeroing days';
  styleHeader(sheet.getCell('A38'));
  sheet.getCell('A39').value = 'Set a day\'s COO Adjusted cell to 0 (or enter a closure date range in columns X to Z) '
    + 'and that day drops out. The month total and the year total both fall by exactly that day, on their own — '
    + 'nothing else needs touching.';
  sheet.mergeCells('A39:B39');
  sheet.getCell('A39').alignment = { wrapText: true };

  sheet.getCell('A41').value = 'Contacts';
  styleHeader(sheet.getCell('A41'));
  sheet.getCell('A42').value = 'Product Owner';
  sheet.getCell('B42').value = `${ctx.session.author || 'Victor Yamaykin'} — vyamaykin@mersgoodwill.org`;

  sheet.views = [{ state: 'frozen', ySplit: 1 }];
}

function addCalendarInputsSheet(wb: ExcelJS.Workbook, ctx: BuildContext): void {
  const sheet = wb.addWorksheet('Calendar_Inputs');
  sheet.getColumn(1).width = 22;
  for (let c = 2; c <= 6; c += 1) sheet.getColumn(c).width = 16;

  sheet.getCell('A1').value = 'Calendar Inputs — COO Control Center';
  styleHeader(sheet.getCell('A1'), 14);

  // Block A: DOW weights, rows 5-13. One editable column — the weights are stored already
  // normalized (they total 100%), and everything downstream divides by $B$13, so edits stay
  // mathematically correct even if the column no longer sums to exactly 100%.
  sheet.getCell('A5').value = 'Weekday';
  sheet.getCell('B5').value = 'Normalized Weight';
  ['A5', 'B5'].forEach((c) => styleHeader(sheet.getCell(c)));

  WEEKDAYS.forEach((day, i) => {
    const row = 6 + i;
    sheet.getCell(`A${row}`).value = day;
    styleFormula(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = ctx.session.dowWeights[day] ?? 0;
    styleInput(sheet.getCell(`B${row}`), '0.0000%');
  });
  sheet.getCell('A13').value = 'Total';
  styleHeader(sheet.getCell('A13'));
  sheet.getCell('B13').value = { formula: 'SUM(B6:B12)' };
  styleFormula(sheet.getCell('B13'), '0.0000%');

  // Block B: Holidays, rows 15-26 (6 seeded + 4 blank editable rows)
  sheet.getCell('A15').value = 'Holiday Date';
  sheet.getCell('B15').value = 'Holiday Label';
  sheet.getCell('C15').value = 'Day of Week';
  ['A15', 'B15', 'C15'].forEach((c) => styleHeader(sheet.getCell(c)));

  const holidays = ctx.session.holidays ?? defaultHolidays(ctx.session.year);
  for (let i = 0; i < 10; i += 1) {
    const row = 16 + i;
    const holiday = holidays[i];
    if (holiday) {
      sheet.getCell(`A${row}`).value = new Date(holiday.date);
      sheet.getCell(`A${row}`).numFmt = 'yyyy-mm-dd';
      styleInput(sheet.getCell(`A${row}`));
      sheet.getCell(`B${row}`).value = holiday.label;
      styleInput(sheet.getCell(`B${row}`));
    } else {
      styleInput(sheet.getCell(`A${row}`));
      styleInput(sheet.getCell(`B${row}`));
    }
    sheet.getCell(`C${row}`).value = { formula: `IF(A${row}="","",TEXT(A${row},"dddd"))` };
    styleFormula(sheet.getCell(`C${row}`));
    sheet.getRow(row).eachCell((cell) => {
      if (!cell.fill) cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLORS.holidayFill } };
    });
  }

  // Block C: Month summary, rows 28-41
  sheet.getCell('A28').value = 'Month';
  sheet.getCell('B28').value = 'Days';
  sheet.getCell('C28').value = 'Closed Days';
  sheet.getCell('D28').value = 'Selling Days';
  sheet.getCell('E28').value = '% of Annual';
  sheet.getCell('F28').value = 'Month Name';
  ['A28', 'B28', 'C28', 'D28', 'E28', 'F28'].forEach((c) => styleHeader(sheet.getCell(c)));

  const dfLast = ctx.dayFactorsLastRow;
  for (let m = 1; m <= 12; m += 1) {
    const row = 29 + m;
    sheet.getCell(`A${row}`).value = m;
    styleFormula(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = {
      formula: `COUNTIF(${ref('Day_Factors', `$B$2:$B$${dfLast}`)}, A${row})`,
    };
    styleCrossSheet(sheet.getCell(`B${row}`));
    sheet.getCell(`C${row}`).value = {
      formula: `SUMPRODUCT((${ref('Day_Factors', `$B$2:$B$${dfLast}`)}=A${row})*(LEN(${ref('Day_Factors', `$D$2:$D$${dfLast}`)})>0))`,
    };
    styleCrossSheet(sheet.getCell(`C${row}`));
    sheet.getCell(`D${row}`).value = { formula: `B${row}-C${row}` };
    styleFormula(sheet.getCell(`D${row}`));
    sheet.getCell(`E${row}`).value = {
      formula: `SUMIF(${ref('Day_Factors', `$B$2:$B$${dfLast}`)}, A${row}, ${ref('Day_Factors', `$F$2:$F$${dfLast}`)})`,
    };
    styleCrossSheet(sheet.getCell(`E${row}`), '0.0000%');
    sheet.getCell(`F${row}`).value = { formula: `TEXT(DATE(2000,A${row},1),"mmmm")` };
    styleFormula(sheet.getCell(`F${row}`));
  }

  sheet.getCell('A44').value =
    'Note: Closed-day counts use SUMPRODUCT+LEN>0 (not COUNTIFS "<>") because Excel treats formula-blank strings ("") as non-blank.';
  styleNote(sheet.getCell('A44'));

  // Block D: Weekday Mix — the seven day-shares every store runs on, in one place.
  sheet.getCell('A46').value = 'Weekday Mix — Normalized % of Week';
  styleHeader(sheet.getCell('A46'), 12);
  ['Day', 'Wkdy #', 'Day % of Annual', '% of Week'].forEach((label, i) => {
    const cell = sheet.getCell(47, i + 1);
    cell.value = label;
    styleHeader(cell);
  });
  // Excel WEEKDAY() numbering, Sunday = 1.
  const WKDY_NUM: Record<string, number> = {
    Sunday: 1, Monday: 2, Tuesday: 3, Wednesday: 4, Thursday: 5, Friday: 6, Saturday: 7,
  };
  WEEKDAYS.forEach((day, i) => {
    const row = 48 + i;
    sheet.getCell(`A${row}`).value = day;
    styleFormula(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = WKDY_NUM[day];
    styleFormula(sheet.getCell(`B${row}`));
    // One open day of this weekday, as a share of the whole year — the number the store tabs use.
    sheet.getCell(`C${row}`).value = {
      formula: `IFERROR(SUMIF(${ref('Day_Factors', `$C$2:$C$${dfLast}`)}, A${row}, ${ref('Day_Factors', `$F$2:$F$${dfLast}`)})`
        + ` / COUNTIFS(${ref('Day_Factors', `$C$2:$C$${dfLast}`)}, A${row}, ${ref('Day_Factors', `$D$2:$D$${dfLast}`)}, ""), 0)`,
    };
    styleCrossSheet(sheet.getCell(`C${row}`), '0.0000%');
    sheet.getCell(`D${row}`).value = { formula: `IFERROR(C${row}/$C$55, 0)` };
    styleFormula(sheet.getCell(`D${row}`), '0.0%');
  });
  sheet.getCell('A55').value = 'Total';
  styleHeader(sheet.getCell('A55'));
  sheet.getCell('C55').value = { formula: 'SUM(C48:C54)' };
  styleFormula(sheet.getCell('C55'), '0.0000%');
  sheet.getCell('D55').value = { formula: 'SUM(D48:D54)' };
  styleFormula(sheet.getCell('D55'), '0.0%');

  sheet.getCell('A56').value = 'Weekend (Sat+Sun)';
  styleHeader(sheet.getCell('A56'));
  sheet.getCell('D56').value = { formula: 'D53+D54' };
  styleFormula(sheet.getCell('D56'), '0.0%');
  sheet.getCell('A57').value = 'Weekday (Mon-Fri)';
  styleHeader(sheet.getCell('A57'));
  sheet.getCell('D57').value = { formula: 'SUM(D48:D52)' };
  styleFormula(sheet.getCell('D57'), '0.0%');

  sheet.getCell('A59').value =
    'These seven day-shares are the same for every store and the same all year: an open Friday is worth the '
    + 'same slice of the year in January as in July. "Day % of Annual" totals 100% across all 365 days. '
    + '"% of Week" is the same seven numbers rescaled to show a normal trading week.';
  styleNote(sheet.getCell('A59'));
}

function addDayFactorsSheet(wb: ExcelJS.Workbook, ctx: BuildContext): void {
  const sheet = wb.addWorksheet('Day_Factors');
  sheet.columns = [
    { header: 'Date', width: 14 },
    { header: 'Month', width: 10 },
    { header: 'Day of Week', width: 14 },
    { header: 'Holiday', width: 20 },
    { header: 'Day Weight (% of Week)', width: 20 },
    { header: 'Day % of Annual', width: 16 },
    { header: 'Day % of Month', width: 16 },
    { header: 'Month Name', width: 14 },
  ];
  sheet.getRow(1).eachCell((cell) => styleHeader(cell));

  const holidays = ctx.session.holidays ?? defaultHolidays(ctx.session.year);
  const dayFactors = buildDayFactors(ctx.session.year, ctx.session.dowWeights, holidays);
  const lastRow = ctx.dayFactorsLastRow;

  dayFactors.forEach((_day, i) => {
    const row = 2 + i;
    sheet.getCell(`A${row}`).value = new Date(dayFactors[i].date);
    sheet.getCell(`A${row}`).numFmt = 'yyyy-mm-dd';
    styleInput(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = { formula: `MONTH(A${row})` };
    styleFormula(sheet.getCell(`B${row}`));
    sheet.getCell(`C${row}`).value = { formula: `TEXT(A${row},"dddd")` };
    styleFormula(sheet.getCell(`C${row}`));
    sheet.getCell(`D${row}`).value = {
      formula: `IFERROR(INDEX(${ref('Calendar_Inputs', '$B$16:$B$25')}, MATCH(A${row}, ${ref('Calendar_Inputs', '$A$16:$A$25')}, 0)), "")`,
    };
    styleCrossSheet(sheet.getCell(`D${row}`));
    // Column E is this weekday's plain share of a normal week — the same seven values all year,
    // straight off Calendar_Inputs, identical for every store.
    sheet.getCell(`E${row}`).value = {
      formula: `INDEX(${ref('Calendar_Inputs', '$B$6:$B$12')}, MATCH(C${row}, ${ref('Calendar_Inputs', '$A$6:$A$12')}, 0)) / ${ref('Calendar_Inputs', '$B$13')}`,
    };
    styleCrossSheet(sheet.getCell(`E${row}`), '0.0000%');
    // Column F is the day's share of the WHOLE YEAR: its week weight over the combined weight of
    // every open day. Closed days are 0, so an open Friday reads the same in January as in July and
    // the column totals exactly 100%.
    sheet.getCell(`F${row}`).value = {
      formula: `IF(D${row}<>"", 0, E${row} / SUMPRODUCT(($D$2:$D$${lastRow}="")*($E$2:$E$${lastRow})))`,
    };
    styleFormula(sheet.getCell(`F${row}`), '0.0000%');
    // Column G is reference only — the same day expressed against its own month.
    sheet.getCell(`G${row}`).value = {
      formula: `IF(F${row}=0, 0, F${row}/SUMIF($B$2:$B$${lastRow}, B${row}, $F$2:$F$${lastRow}))`,
    };
    styleFormula(sheet.getCell(`G${row}`), '0.0000%');
    sheet.getCell(`H${row}`).value = { formula: `TEXT(A${row},"mmmm")` };
    styleFormula(sheet.getCell(`H${row}`));

    if (dayFactors[i].holidayLabel) {
      sheet.getRow(row).eachCell((cell) => {
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLORS.holidayFill } };
      });
    }
  });

  const totalRow = lastRow + 1;
  sheet.getCell(`A${totalRow}`).value = 'TOTAL';
  styleHeader(sheet.getCell(`A${totalRow}`));
  sheet.getCell(`E${totalRow}`).value = { formula: `SUM(E2:E${lastRow})` };
  styleFormula(sheet.getCell(`E${totalRow}`), '0.0000%');
  // Day % of Annual must total exactly 100% — that is the whole-year invariant.
  sheet.getCell(`F${totalRow}`).value = { formula: `SUM(F2:F${lastRow})` };
  styleFormula(sheet.getCell(`F${totalRow}`), '0.0000%');

  sheet.getColumn('J').width = 12;
  sheet.getColumn('K').width = 10;
  sheet.getCell('J1').value = 'Weekday';
  sheet.getCell('K1').value = 'Count';
  styleHeader(sheet.getCell('J1'));
  styleHeader(sheet.getCell('K1'));
  WEEKDAYS.forEach((day, i) => {
    const row = 2 + i;
    sheet.getCell(`J${row}`).value = day;
    styleFormula(sheet.getCell(`J${row}`));
    sheet.getCell(`K${row}`).value = { formula: `COUNTIF($C$2:$C$${lastRow}, J${row})` };
    styleFormula(sheet.getCell(`K${row}`));
  });
  sheet.getCell('J9').value = 'Total Days';
  styleHeader(sheet.getCell('J9'));
  sheet.getCell('K9').value = { formula: `SUM(K2:K${1 + WEEKDAYS.length})` };
  styleFormula(sheet.getCell('K9'));

  sheet.views = [{ state: 'frozen', ySplit: 1 }];
}

interface StoreRowIndex {
  code: string;
  row: number;
}

function addPlanInputsSheet(wb: ExcelJS.Workbook, ctx: BuildContext): StoreRowIndex[] {
  const sheet = wb.addWorksheet('Plan_Inputs');
  sheet.getColumn(1).width = 10;
  sheet.getColumn(2).width = 34;
  for (let c = 3; c <= 10; c += 1) sheet.getColumn(c).width = 20;

  sheet.getCell('A1').value = 'Plan Inputs — Store Baselines';
  styleHeader(sheet.getCell('A1'), 14);

  const storeStartRow = 14;
  const storeEndRow = storeStartRow + ctx.stores.length - 1;

  sheet.getCell('A4').value = 'Recommended Plan';
  sheet.getCell('B4').value = ctx.session.recommendedPlan ?? ctx.session.totalPlannedSales;
  styleInput(sheet.getCell('B4'), '$#,##0');

  sheet.getCell('A5').value = 'COO Adjusted Plan';
  sheet.getCell('B5').value = { formula: `SUM($H$${storeStartRow}:$H$${storeEndRow})` };
  styleFormula(sheet.getCell('B5'), '$#,##0');

  sheet.getCell('A6').value = 'Variance';
  sheet.getCell('B6').value = { formula: 'B5-B4' };
  styleFormula(sheet.getCell('B6'), '$#,##0');

  const headerRow = storeStartRow - 1;
  [
    'Code', 'Name', 'Region', 'Status', 'Base Sales',
    'Forecasted Plan Base', 'COO Adjusted Plan Base', 'Effective Plan Base', 'Variance',
  ].forEach((label, i) => {
    const cell = sheet.getCell(headerRow, i + 1);
    cell.value = label;
    styleHeader(cell);
  });

  const rowIndex: StoreRowIndex[] = [];
  const continuingRowsByRegion = new Map<string, number[]>();
  ctx.stores.forEach((store, i) => {
    const row = storeStartRow + i;
    rowIndex.push({ code: store.code, row });
    sheet.getCell(`A${row}`).value = store.code;
    styleFormula(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = store.name;
    styleFormula(sheet.getCell(`B${row}`));
    sheet.getCell(`C${row}`).value = store.region;
    styleFormula(sheet.getCell(`C${row}`));
    sheet.getCell(`D${row}`).value = store.status;
    styleInput(sheet.getCell(`D${row}`));
    sheet.getCell(`E${row}`).value = store.fy2026Actual;
    styleInput(sheet.getCell(`E${row}`), '$#,##0');

    // Each store's share of the Recommended Plan, in proportion to its Base Sales. The division is
    // written inline on every row — there is no separate reallocation-factor cell to maintain — and
    // column F therefore sums to the Recommended Plan (B4) exactly.
    sheet.getCell(`F${row}`).value = {
      formula: `E${row}/SUM($E$${storeStartRow}:$E$${storeEndRow})*$B$4`,
    };
    styleFormula(sheet.getCell(`F${row}`), '$#,##0');

    if (store.status === 'Continuing') {
      const list = continuingRowsByRegion.get(store.region) ?? [];
      list.push(row);
      continuingRowsByRegion.set(store.region, list);
    } else {
      sheet.getRow(row).eachCell((cell) => {
        if (!cell.fill) cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLORS.newStoreFill } };
      });
    }

    const override = ctx.session.storeOverrides[store.code]?.annualPlanBase;
    if (override !== undefined) {
      sheet.getCell(`G${row}`).value = override;
    }
    styleInput(sheet.getCell(`G${row}`), '$#,##0');
    sheet.getCell(`H${row}`).value = { formula: `IF(G${row}="",F${row},G${row})` };
    styleFormula(sheet.getCell(`H${row}`), '$#,##0');
    sheet.getCell(`I${row}`).value = { formula: `H${row}-F${row}` };
    styleFormula(sheet.getCell(`I${row}`), '$#,##0');
  });

  // Second pass: a new store has no sales history of its own, so it inherits the AVERAGE Base Sales
  // of up to 3 comp stores in the same region. That imputed base then flows through the same
  // proportional-share formula in column F as every other row. A closed store keeps a Base Sales of
  // 0, so its share works out to 0 with no special case needed.
  ctx.stores.forEach((store, i) => {
    if (store.status !== 'New store') return;
    const row = storeStartRow + i;
    const comps = (continuingRowsByRegion.get(store.region) ?? []).slice(0, 3);
    if (comps.length === 0) return;
    const compRefs = comps.map((r) => `E${r}`).join(',');
    sheet.getCell(`E${row}`).value = { formula: `AVERAGE(${compRefs})` };
    styleFormula(sheet.getCell(`E${row}`), '$#,##0');
  });

  return rowIndex;
}


function addStoreSheets(
  wb: ExcelJS.Workbook,
  ctx: BuildContext,
  planInputsRowIndex: StoreRowIndex[],
): void {
  const holidays = ctx.session.holidays ?? defaultHolidays(ctx.session.year);
  const dayFactors = buildDayFactors(ctx.session.year, ctx.session.dowWeights, holidays);
  const monthColLetters = ['E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P'];

  const dailyTotalRow = 9 + dayFactors.length;
  const dfLast = ctx.dayFactorsLastRow;

  // Row range for each calendar month within the daily block (shared across all stores, since the
  // calendar itself doesn't vary by store — only which days within it get zeroed by closures does).
  const monthRowRanges: { startRow: number; endRow: number }[] = [];
  {
    let rowCursor = 9;
    for (let m = 1; m <= 12; m += 1) {
      const count = dayFactors.filter((d) => d.month === m).length;
      monthRowRanges.push({ startRow: rowCursor, endRow: rowCursor + count - 1 });
      rowCursor += count;
    }
  }

  ctx.stores.forEach((store) => {
    const planRow = planInputsRowIndex.find((r) => r.code === store.code)?.row;
    if (!planRow) return;
    const storeClosures = ctx.session.storeOverrides[store.code]?.closures ?? [];
    const hasClosures = storeClosures.length > 0;
    const storeClosedDates = new Set(expandClosuresToHolidays(storeClosures).map((h) => h.date));
    const sheet = wb.addWorksheet(store.sheetName.slice(0, 31));
    sheet.getColumn(1).width = 14;
    sheet.getColumn(2).width = 14;
    sheet.getColumn(3).width = 16;
    sheet.getColumn(4).width = 16;
    sheet.getColumn(5).width = 20;
    sheet.getColumn(6).width = 12;
    // Columns E-P double as both the 12 monthly $ header cells (rows 1-3) and the daily-block
    // columns (Day % of Annual, Holiday, Month, Month Total — rows 8+), so size them for the larger of
    // the two: dollar amounts up to ~7 figures.
    monthColLetters.forEach((col) => {
      sheet.getColumn(col).width = 14;
    });
    sheet.getColumn('Q').width = 14;
    sheet.getColumn('S').width = 14;
    sheet.getColumn('T').width = 14;

    sheet.getCell('A1').value = 'Store Code';
    sheet.getCell('B1').value = 'Store Name';
    sheet.getCell('C1').value = 'POS';
    sheet.getCell('D1').value = '2027 Planned Sales';
    MONTH_NAMES.forEach((name, m) => {
      sheet.getCell(`${monthColLetters[m]}1`).value = name.slice(0, 3);
    });
    sheet.getCell('Q1').value = 'Total';
    sheet.getCell('S1').value = 'COO Adjusted Planned Sales';
    sheet.getCell('T1').value = 'Variance';
    sheet.getColumn('S').width = 22;
    sheet.getColumn('T').width = 16;
    sheet.getRow(1).eachCell((cell) => styleHeader(cell));

    sheet.getCell('A2').value = store.code;
    styleFormula(sheet.getCell('A2'));
    sheet.getCell('B2').value = store.name;
    styleFormula(sheet.getCell('B2'));
    sheet.getCell('C2').value = 'N/A';
    styleFormula(sheet.getCell('C2'));
    sheet.getCell('D2').value = 'Recommended';
    styleHeader(sheet.getCell('D2'));
    sheet.getCell('D3').value = 'COO Adjusted';
    styleHeader(sheet.getCell('D3'));
    sheet.getCell('A6').value = 'Forecasted Plan Base';
    sheet.getCell('B6').value = { formula: ref('Plan_Inputs', `$F$${planRow}`) };
    styleCrossSheet(sheet.getCell('B6'), '$#,##0');

    // Store-specific closures (renovation, full closure) live locally on this store's own sheet —
    // distinct from Calendar_Inputs' network-wide holidays. A closure here zeroes those exact days
    // for this store only via the Holiday / Day % of Annual formulas below.
    sheet.getColumn('V').width = 14;
    sheet.getColumn('W').width = 14;
    sheet.getColumn('X').width = 16;
    sheet.getCell('V1').value = 'Closure Start';
    sheet.getCell('W1').value = 'Closure End';
    sheet.getCell('X1').value = 'Closure Label';
    styleHeader(sheet.getCell('V1'));
    styleHeader(sheet.getCell('W1'));
    styleHeader(sheet.getCell('X1'));
    const closuresRowCount = Math.max(1, storeClosures.length);
    const closuresEndRow = 1 + closuresRowCount;
    for (let i = 0; i < closuresRowCount; i += 1) {
      const row = 2 + i;
      const closure = storeClosures[i];
      if (closure) {
        sheet.getCell(`V${row}`).value = new Date(`${closure.start}T00:00:00`);
        sheet.getCell(`V${row}`).numFmt = 'yyyy-mm-dd';
        sheet.getCell(`W${row}`).value = new Date(`${closure.end}T00:00:00`);
        sheet.getCell(`W${row}`).numFmt = 'yyyy-mm-dd';
        sheet.getCell(`X${row}`).value = closure.label ?? 'Store Closure';
      }
      styleInput(sheet.getCell(`V${row}`));
      styleInput(sheet.getCell(`W${row}`));
      styleInput(sheet.getCell(`X${row}`));
    }

    sheet.getCell('D2').value = 'Recommended';
    styleHeader(sheet.getCell('D2'));
    sheet.getCell('D3').value = 'COO Adjusted Plan';
    styleHeader(sheet.getCell('D3'));

    MONTH_NAMES.forEach((_name, m) => {
      const col = monthColLetters[m];
      const { startRow, endRow } = monthRowRanges[m];
      // Row 2 is the forecast: the plan base times that month's Day % of Annual.
      sheet.getCell(`${col}2`).value = { formula: `$B$6 * SUM(E${startRow}:E${endRow})` };
      styleCrossSheet(sheet.getCell(`${col}2`), '$#,##0');
      // Row 3 is the commitment, and it ADDS UP that month's COO Adjusted days rather than
      // recomputing from the base. That is what lets the COO clear whole months or a handful of
      // days in the daily block and see the month, the year and the Diff move on their own.
      sheet.getCell(`${col}3`).value = { formula: `SUM(D${startRow}:D${endRow})` };
      styleCrossSheet(sheet.getCell(`${col}3`), '$#,##0');
    });

    sheet.getCell('Q2').value = { formula: 'SUM(E2:P2)' };
    styleFormula(sheet.getCell('Q2'), '$#,##0');
    sheet.getCell('Q3').value = { formula: `D${dailyTotalRow}` };
    styleFormula(sheet.getCell('Q3'), '$#,##0');
    sheet.getCell('S2').value = { formula: ref('Plan_Inputs', `$H$${planRow}`) };
    styleCrossSheet(sheet.getCell('S2'), '$#,##0');
    sheet.getCell('T2').value = { formula: ref('Plan_Inputs', `$I$${planRow}`) };
    styleCrossSheet(sheet.getCell('T2'), '$#,##0');

    sheet.getCell('Q5').value = 'Diff';
    styleHeader(sheet.getCell('Q5'));
    sheet.getCell('Q6').value = { formula: 'Q3-Q2' };
    styleFormula(sheet.getCell('Q6'), '$#,##0.00');

    sheet.getCell('A7').value = 'Daily Schedule';
    styleHeader(sheet.getCell('A7'));
    ['Date', 'Day of Week', 'Recommended Sales', 'COO Adjusted Plan', 'Day % of Annual', 'Holiday', 'Month',
      'Recommended Month Total', 'COO Month Total'].forEach(
      (label, i) => {
        const cell = sheet.getCell(8, i + 1);
        cell.value = label;
        styleHeader(cell);
      },
    );
    sheet.getRow(7).height = 4;
    sheet.getColumn(4).width = 14;

    let monthStartRow = 9;
    dayFactors.forEach((day, i) => {
      const row = 9 + i;
      const dfRow = 2 + i;
      sheet.getCell(`A${row}`).value = { formula: ref('Day_Factors', `A${dfRow}`) };
      sheet.getCell(`A${row}`).numFmt = 'yyyy-mm-dd';
      styleCrossSheet(sheet.getCell(`A${row}`));
      sheet.getCell(`B${row}`).value = { formula: ref('Day_Factors', `C${dfRow}`) };
      styleCrossSheet(sheet.getCell(`B${row}`));
      // Daily dollars are the annual base times this day's share of the year — no month-level
      // middle step, so the day figure never depends on which month it happens to fall in.
      sheet.getCell(`C${row}`).value = { formula: `$B$6 * E${row}` };
      styleFormula(sheet.getCell(`C${row}`), '$#,##0.00');
      sheet.getCell(`D${row}`).value = { formula: `$S$2 * E${row}` };
      styleFormula(sheet.getCell(`D${row}`), '$#,##0.00');

      if (hasClosures) {
        sheet.getCell(`F${row}`).value = {
          formula: `IF(${ref('Day_Factors', `D${dfRow}`)}<>"", ${ref('Day_Factors', `D${dfRow}`)}, IF(SUMPRODUCT(($X$2:$X$${closuresEndRow}<=A${row})*($Y$2:$Y$${closuresEndRow}>=A${row}))>0, "Store Closure", ""))`,
        };
        styleCrossSheet(sheet.getCell(`F${row}`));
        // This store closes on days the network does not, so its open-day total differs from
        // Day_Factors. Re-derive the share against this store's own open days.
        sheet.getCell(`E${row}`).value = {
          formula: `IF(F${row}<>"", 0, ${ref('Day_Factors', `E${dfRow}`)} / SUMPRODUCT(($F$9:$F$${dailyTotalRow - 1}="")*(${ref('Day_Factors', `$E$2:$E$${dfLast}`)})))`,
        };
        styleFormula(sheet.getCell(`E${row}`), '0.0000%');
      } else {
        sheet.getCell(`E${row}`).value = { formula: ref('Day_Factors', `F${dfRow}`) };
        styleCrossSheet(sheet.getCell(`E${row}`), '0.0000%');
        sheet.getCell(`F${row}`).value = { formula: ref('Day_Factors', `D${dfRow}`) };
        styleCrossSheet(sheet.getCell(`F${row}`));
      }

      sheet.getCell(`G${row}`).value = { formula: `TEXT(A${row},"mmmm")` };
      styleFormula(sheet.getCell(`G${row}`));

      const isLastDayOfMonth = i === dayFactors.length - 1 || dayFactors[i + 1].month !== day.month;
      if (isLastDayOfMonth) {
        // Both month totals sit on the month's last day: H adds up the forecast column, I adds up
        // the COO Adjusted column. Clearing days in D drops I (and the month in row 3) but leaves
        // H alone, so the two columns show the before and after side by side.
        sheet.getCell(`H${row}`).value = { formula: `SUM(C${monthStartRow}:C${row})` };
        styleFormula(sheet.getCell(`H${row}`), '$#,##0');
        sheet.getCell(`I${row}`).value = { formula: `SUM(D${monthStartRow}:D${row})` };
        styleFormula(sheet.getCell(`I${row}`), '$#,##0');
        monthStartRow = row + 1;
      }

      if (day.holidayLabel || storeClosedDates.has(day.date)) {
        ['A', 'B', 'C', 'D', 'E', 'F'].forEach((col) => {
          sheet.getCell(`${col}${row}`).fill = {
            type: 'pattern', pattern: 'solid', fgColor: { argb: COLORS.holidayFill },
          };
        });
      }
    });

    sheet.getCell(`A${dailyTotalRow}`).value = 'FULL YEAR';
    styleHeader(sheet.getCell(`A${dailyTotalRow}`));
    sheet.getCell(`C${dailyTotalRow}`).value = { formula: `SUM(C9:C${dailyTotalRow - 1})` };
    styleFormula(sheet.getCell(`C${dailyTotalRow}`), '$#,##0.00');
    sheet.getCell(`D${dailyTotalRow}`).value = { formula: `SUM(D9:D${dailyTotalRow - 1})` };
    styleFormula(sheet.getCell(`D${dailyTotalRow}`), '$#,##0.00');
    sheet.getCell(`E${dailyTotalRow}`).value = { formula: `SUM(E9:E${dailyTotalRow - 1})` };
    styleFormula(sheet.getCell(`E${dailyTotalRow}`), '0.00');

    sheet.views = [{ state: 'frozen', ySplit: 8 }];
  });
}

function addAllStoresSummarySheet(wb: ExcelJS.Workbook, ctx: BuildContext): void {
  const sheet = wb.addWorksheet('All_Stores_Summary');
  const headers = [
    'Store Code', 'Store Name', '2026 Sales', 'Transaction Count', 'Avg Transaction',
    'Items Sold', 'Avg Item Value', 'Donations', 'Planned Sales', 'Plan vs 2026',
    'Forecasted Planned Sales ($)', 'COO Adjusted Planned Sales', 'Variance',
  ];
  headers.forEach((label, i) => {
    const cell = sheet.getCell(1, i + 1);
    cell.value = label;
    styleHeader(cell);
  });
  sheet.getColumn(1).width = 12;
  sheet.getColumn(2).width = 30;
  for (let c = 3; c <= headers.length; c += 1) sheet.getColumn(c).width = 16;

  ctx.stores.forEach((store, i) => {
    const row = 2 + i;
    sheet.getCell(`A${row}`).value = store.code;
    styleFormula(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = store.name;
    styleFormula(sheet.getCell(`B${row}`));
    sheet.getCell(`C${row}`).value = store.fy2026Actual;
    styleInput(sheet.getCell(`C${row}`), '$#,##0');
    sheet.getCell(`D${row}`).value = store.transactionCount2026;
    styleInput(sheet.getCell(`D${row}`));
    sheet.getCell(`E${row}`).value = { formula: `IF(D${row}=0,0,C${row}/D${row})` };
    styleFormula(sheet.getCell(`E${row}`), '$0.00');
    sheet.getCell(`F${row}`).value = store.itemsSold2026;
    styleInput(sheet.getCell(`F${row}`));
    sheet.getCell(`G${row}`).value = { formula: `IF(F${row}=0,0,C${row}/F${row})` };
    styleFormula(sheet.getCell(`G${row}`), '$0.00');
    sheet.getCell(`H${row}`).value = store.donationCount2026;
    styleInput(sheet.getCell(`H${row}`));
    sheet.getCell(`I${row}`).value = { formula: ref(store.sheetName.slice(0, 31), '$Q$2') };
    styleCrossSheet(sheet.getCell(`I${row}`), '$#,##0');
    sheet.getCell(`J${row}`).value = { formula: `IF(C${row}=0,"new",I${row}/C${row}-1)` };
    styleFormula(sheet.getCell(`J${row}`), '0.00%');

    const planRow = 14 + i;
    sheet.getCell(`K${row}`).value = { formula: ref('Plan_Inputs', `$F$${planRow}`) };
    styleCrossSheet(sheet.getCell(`K${row}`), '$#,##0');
    sheet.getCell(`L${row}`).value = { formula: ref('Plan_Inputs', `$H$${planRow}`) };
    styleCrossSheet(sheet.getCell(`L${row}`), '$#,##0');
    sheet.getCell(`M${row}`).value = { formula: ref('Plan_Inputs', `$I$${planRow}`) };
    styleCrossSheet(sheet.getCell(`M${row}`), '$#,##0');
  });

  const totalRow = 2 + ctx.stores.length;
  sheet.getCell(`A${totalRow}`).value = 'NETWORK TOTAL';
  styleHeader(sheet.getCell(`A${totalRow}`));
  sheet.getCell(`C${totalRow}`).value = { formula: `SUM(C2:C${totalRow - 1})` };
  styleFormula(sheet.getCell(`C${totalRow}`), '$#,##0');
  sheet.getCell(`I${totalRow}`).value = { formula: `SUM(I2:I${totalRow - 1})` };
  styleFormula(sheet.getCell(`I${totalRow}`), '$#,##0');
  sheet.getCell(`K${totalRow}`).value = { formula: `SUM(K2:K${totalRow - 1})` };
  styleFormula(sheet.getCell(`K${totalRow}`), '$#,##0');
  sheet.getCell(`L${totalRow}`).value = { formula: `SUM(L2:L${totalRow - 1})` };
  styleFormula(sheet.getCell(`L${totalRow}`), '$#,##0');
  sheet.getCell(`M${totalRow}`).value = { formula: `SUM(M2:M${totalRow - 1})` };
  styleFormula(sheet.getCell(`M${totalRow}`), '$#,##0');
}

function addMonthlyMatrixSheet(wb: ExcelJS.Workbook, ctx: BuildContext): void {
  const sheet = wb.addWorksheet('Monthly_Matrix');
  const monthColLetters = ['C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N'];
  const sourceCols = ['E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P'];

  sheet.getCell('A2').value = 'Code';
  sheet.getCell('B2').value = 'Store Name';
  MONTH_NAMES.forEach((name, i) => {
    sheet.getCell(`${monthColLetters[i]}2`).value = name;
  });
  sheet.getCell('O2').value = 'Total';
  sheet.getRow(2).eachCell((cell) => styleHeader(cell));
  sheet.getColumn(1).width = 12;
  sheet.getColumn(2).width = 30;
  [...monthColLetters, 'O'].forEach((col) => {
    sheet.getColumn(col).width = 14;
  });

  ctx.stores.forEach((store, i) => {
    const row = 3 + i;
    sheet.getCell(`A${row}`).value = store.code;
    styleFormula(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = store.name;
    styleFormula(sheet.getCell(`B${row}`));
    monthColLetters.forEach((col, m) => {
      // Row 3 on the store sheet = the COO-adjusted track, matching Q2's total below.
      sheet.getCell(`${col}${row}`).value = {
        formula: ref(store.sheetName.slice(0, 31), `$${sourceCols[m]}$3`),
      };
      styleCrossSheet(sheet.getCell(`${col}${row}`), '$#,##0');
    });
    sheet.getCell(`O${row}`).value = { formula: ref(store.sheetName.slice(0, 31), '$Q$2') };
    styleCrossSheet(sheet.getCell(`O${row}`), '$#,##0');
  });

  const totalRow = 3 + ctx.stores.length;
  sheet.getCell(`A${totalRow}`).value = 'NETWORK TOTAL';
  styleHeader(sheet.getCell(`A${totalRow}`));
  [...monthColLetters, 'O'].forEach((col) => {
    sheet.getCell(`${col}${totalRow}`).value = { formula: `SUM(${col}3:${col}${totalRow - 1})` };
    styleFormula(sheet.getCell(`${col}${totalRow}`), '$#,##0');
  });
}

function addDailyDisaggregatedSheet(wb: ExcelJS.Workbook, ctx: BuildContext): void {
  const sheet = wb.addWorksheet('Daily_Disaggregated_Plan');
  const headers = ['Store Code', 'Store Name', 'Date', 'Month', 'Day of Week', 'Day % of Annual', 'Planned Sales', 'COO Adjusted'];
  headers.forEach((label, i) => {
    const cell = sheet.getCell(1, i + 1);
    cell.value = label;
    styleHeader(cell);
  });
  sheet.getColumn(1).width = 12;
  sheet.getColumn(2).width = 30;
  sheet.getColumn(3).width = 14;
  sheet.getColumn(4).width = 12;
  sheet.getColumn(5).width = 14;
  sheet.getColumn(6).width = 12;
  sheet.getColumn(7).width = 16;
  sheet.getColumn(8).width = 16;

  const totalDays = ctx.totalDays;
  let row = 2;
  ctx.stores.forEach((store) => {
    const sheetName = store.sheetName.slice(0, 31);
    for (let d = 0; d < totalDays; d += 1) {
      const storeRow = 9 + d;
      sheet.getCell(`A${row}`).value = store.code;
      styleFormula(sheet.getCell(`A${row}`));
      sheet.getCell(`B${row}`).value = store.name;
      styleFormula(sheet.getCell(`B${row}`));
      sheet.getCell(`C${row}`).value = { formula: ref(sheetName, `A${storeRow}`) };
      sheet.getCell(`C${row}`).numFmt = 'yyyy-mm-dd';
      styleCrossSheet(sheet.getCell(`C${row}`));
      sheet.getCell(`D${row}`).value = { formula: `TEXT(C${row},"mmmm")` };
      styleFormula(sheet.getCell(`D${row}`));
      // Store-sheet source columns: B=Day of Week, C=Planned Sales, D=COO Adjusted, E=Day % of Annual.
      sheet.getCell(`E${row}`).value = { formula: ref(sheetName, `B${storeRow}`) };
      styleCrossSheet(sheet.getCell(`E${row}`));
      sheet.getCell(`F${row}`).value = { formula: ref(sheetName, `E${storeRow}`) };
      styleCrossSheet(sheet.getCell(`F${row}`), '0.0000%');
      sheet.getCell(`G${row}`).value = { formula: ref(sheetName, `C${storeRow}`) };
      styleCrossSheet(sheet.getCell(`G${row}`), '$#,##0.00');
      sheet.getCell(`H${row}`).value = { formula: ref(sheetName, `D${storeRow}`) };
      styleCrossSheet(sheet.getCell(`H${row}`), '$#,##0.00');
      row += 1;
    }
  });
}

function addExecSummarySheet(wb: ExcelJS.Workbook, ctx: BuildContext): void {
  const sheet = wb.addWorksheet('Exec_Summary', { properties: { tabColor: { argb: 'FF0065A4' } } });
  sheet.getColumn(1).width = 26;
  sheet.getColumn(2).width = 20;
  for (let c = 3; c <= 6; c += 1) sheet.getColumn(c).width = 16;

  sheet.getCell('A1').value = `${ctx.session.name} — Executive Summary`;
  styleHeader(sheet.getCell('A1'), 14);

  sheet.getCell('A4').value = 'Plan Year';
  sheet.getCell('B4').value = ctx.session.year;
  styleFormula(sheet.getCell('B4'));
  sheet.getCell('A5').value = 'Committed Plan';
  sheet.getCell('B5').value = { formula: ref('Plan_Inputs', '$B$5') };
  styleCrossSheet(sheet.getCell('B5'), '$#,##0');
  sheet.getCell('A6').value = 'Continuing Stores';
  sheet.getCell('B6').value = ctx.stores.filter((s) => s.status === 'Continuing').length;
  styleFormula(sheet.getCell('B6'));
  sheet.getCell('A7').value = 'New Stores';
  sheet.getCell('B7').value = ctx.stores.filter((s) => s.status === 'New store').length;
  styleFormula(sheet.getCell('B7'));
  sheet.getCell('A8').value = 'Total Stores';
  sheet.getCell('B8').value = ctx.stores.length;
  styleFormula(sheet.getCell('B8'));

  sheet.getCell('A13').value = 'Month';
  sheet.getCell('B13').value = '% of Annual';
  sheet.getCell('C13').value = 'Planned Sales';
  sheet.getCell('D13').value = 'Days';
  sheet.getCell('E13').value = 'Closed Days';
  sheet.getCell('F13').value = 'Selling Days';
  ['A13', 'B13', 'C13', 'D13', 'E13', 'F13'].forEach((c) => styleHeader(sheet.getCell(c)));

  const monthColLetters = ['C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N'];
  for (let m = 1; m <= 12; m += 1) {
    const row = 13 + m;
    const calRow = 29 + m;
    sheet.getCell(`A${row}`).value = MONTH_NAMES[m - 1];
    styleFormula(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = { formula: ref('Calendar_Inputs', `$E$${calRow}`) };
    styleCrossSheet(sheet.getCell(`B${row}`), '0.0000%');
    sheet.getCell(`C${row}`).value = { formula: ref('Monthly_Matrix', `$${monthColLetters[m - 1]}$${3 + ctx.stores.length}`) };
    styleCrossSheet(sheet.getCell(`C${row}`), '$#,##0');
    sheet.getCell(`D${row}`).value = { formula: ref('Calendar_Inputs', `$B$${calRow}`) };
    styleCrossSheet(sheet.getCell(`D${row}`));
    sheet.getCell(`E${row}`).value = { formula: ref('Calendar_Inputs', `$C$${calRow}`) };
    styleCrossSheet(sheet.getCell(`E${row}`));
    sheet.getCell(`F${row}`).value = { formula: ref('Calendar_Inputs', `$D$${calRow}`) };
    styleCrossSheet(sheet.getCell(`F${row}`));
  }

  sheet.getCell('A27').value =
    'Zero-Variance Reconciliation: SUM(Daily Planned Sales) = SUM(Month Planned Sales) = Annual Plan Base for every store (see each store tab, cell T2).';
  styleNote(sheet.getCell('A27'));
  sheet.getCell('A28').value =
    'Flat Weekday Shares: Day % of Annual is one denominator for the whole year, so the seven weekday values are the same in every month and the same for every store. An open Friday is worth the same slice of the year in January as in July, and the column totals exactly 100%.';
  styleNote(sheet.getCell('A28'));
  sheet.getCell('A29').value =
    'Store Closures: entered on each store’s own sheet (columns X:Z — Closure Start / Closure End / Closure Label). Closed days read 0% and drop out of their own month, so that month falls by exactly the days removed. The store’s annual commitment is held — the freed volume lifts the rest of the year slightly. To lower the store’s year as well, reduce its COO Adjusted Plan Base on Plan_Inputs.';
  styleNote(sheet.getCell('A29'));

  sheet.getCell('A31').value = 'Code';
  sheet.getCell('B31').value = 'Name';
  sheet.getCell('C31').value = 'Region';
  sheet.getCell('D31').value = 'Status';
  sheet.getCell('E31').value = 'FY2027 Plan';
  ['A31', 'B31', 'C31', 'D31', 'E31'].forEach((c) => styleHeader(sheet.getCell(c)));
  ctx.stores.forEach((store, i) => {
    const row = 32 + i;
    const planRow = 14 + i;
    sheet.getCell(`A${row}`).value = { formula: ref('Plan_Inputs', `A${planRow}`) };
    styleCrossSheet(sheet.getCell(`A${row}`));
    sheet.getCell(`B${row}`).value = { formula: ref('Plan_Inputs', `B${planRow}`) };
    styleCrossSheet(sheet.getCell(`B${row}`));
    sheet.getCell(`C${row}`).value = store.region;
    styleFormula(sheet.getCell(`C${row}`));
    sheet.getCell(`D${row}`).value = store.status;
    styleFormula(sheet.getCell(`D${row}`));
    sheet.getCell(`E${row}`).value = { formula: ref(store.sheetName.slice(0, 31), '$Q$2') };
    styleCrossSheet(sheet.getCell(`E${row}`), '$#,##0');
  });
}

export function buildWorkbook(session: PlanningSession, stores: Store[]): ExcelJS.Workbook {
  const wb = new ExcelJS.Workbook();
  wb.creator = session.author || 'VIC Forecast App';
  wb.created = new Date();

  const totalDays = ((session.year % 4 === 0 && session.year % 100 !== 0) || session.year % 400 === 0) ? 366 : 365;
  const ctx: BuildContext = {
    session,
    stores,
    totalDays,
    dayFactorsLastRow: 1 + totalDays,
    planInputsLastRow: 13 + stores.length,
  };

  addHowToUseSheet(wb, ctx);
  addExecSummarySheet(wb, ctx);
  addAllStoresSummarySheet(wb, ctx);
  addMonthlyMatrixSheet(wb, ctx);
  addDailyDisaggregatedSheet(wb, ctx);
  addCalendarInputsSheet(wb, ctx);
  addDayFactorsSheet(wb, ctx);
  const planRowIndex = addPlanInputsSheet(wb, ctx);
  addStoreSheets(wb, ctx, planRowIndex);

  wb.eachSheet((sheet, id) => {
    if (sheet.name === 'Exec_Summary') {
      wb.views = [
        { x: 0, y: 0, width: 10000, height: 10000, firstSheet: 0, visibility: 'visible', activeTab: id - 1 },
      ];
    }
  });

  return wb;
}

export async function exportWorkbookToFile(session: PlanningSession, stores: Store[]): Promise<void> {
  const wb = buildWorkbook(session, stores);
  const buffer = await wb.xlsx.writeBuffer();
  const blob = new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  saveAs(blob, `${session.name.replace(/[^a-z0-9]+/gi, '_')}_${session.year}.xlsx`);
}

export { normalizeDowWeights, buildStorePlan };
