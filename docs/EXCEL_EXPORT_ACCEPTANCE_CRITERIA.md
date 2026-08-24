# Excel Export — Acceptance Criteria

## Context

The VIC Forecast App's "Export Excel" feature compiles a multi-tab, formula-driven workbook (`Exec_Summary`, `Calendar_Inputs`, `Day_Factors`, `Plan_Inputs`, one tab per store, `All_Stores_Summary`, `Monthly_Matrix`, `Daily_Disaggregated_Plan`) from a planning session — DOW weights, holidays, growth rate, anchor total, ramp factor — and the 65-store master list. Every cell is a native Excel formula, not a pre-computed value, so a planner can open the workbook, tweak an input (a DOW %, a holiday date, the growth rate), and watch the entire 365-day, per-store schedule recalculate live.

That live-recalculation design is fragile in a specific way: it's easy to write a formula that looks right for one store or one month but silently drifts once ~65 store tabs and 365 daily rows compound the same formula. Every criterion below encodes a numeric invariant that must hold implementation is correct, not a UI check.

Source of truth for current behavior: [`src/utils/excelExport.ts`](src/utils/excelExport.ts) (workbook generation), [`src/utils/dowEngine.ts`](src/utils/dowEngine.ts) (the underlying math), [`src/utils/excelExport.test.ts`](src/utils/excelExport.test.ts) (automated verification of every criterion below — currently all 8 pass).

Run the suite: `npm run test:excel`

---

## Criterion 1 — Zero Difference

**Statement:** Every store tab's daily schedule must sum to exactly that store's Annual Plan Base. No rounding drift, no leftover or missing dollars.

**Where it lives:** Each store tab has a Diff cell `T2 = S2 - Q2`, where `S2` is the full-year sum of the daily `Planned Sales` column and `Q2` is the sum of the 12 monthly dollar cells. `T2` must equal `$0.00`.

**Why it matters:** If this drifts, the store's plan doesn't reconcile to what leadership approved — the workbook would be silently lying about the total.

**Pass condition:** `abs(fullYearTotal - annualPlanBase) < $0.01` for every store.

---

## Criterion 2 — Store Isolation

**Statement:** No two stores with different Annual Plan Bases may end up with the same full-year total. Each store's 365-day schedule must be driven by *that store's own* base, not a shared network-level number.

**Why it matters:** This is the exact historical failure mode called out in the app's origin (§2 of the original handoff spec): a prior export tool computed the network's daily allocation once and wrote the *same* numbers into all 64 store tabs, so every store appeared to plan the full $145M network total instead of its own ~$1–2M share.

**Pass condition:** Among stores with distinct Annual Plan Bases, every one has a distinct full-year total.

---

## Criterion 3 — Closure Integrity

**Statement:** The count of "closed days" per month must exactly match the number of seeded/entered holidays that fall in that month — no more, no fewer.

**Why it matters:** Excel's `COUNTIFS(range, "<>")` counts a formula that *evaluates* to an empty string (`""`) as non-blank, so a naive closed-day count formula reports 365 closed days instead of 6. The workbook uses `SUMPRODUCT((month=X)*(LEN(holidayCell)>0))` specifically to avoid this.

**Pass condition:** Closed-day count derived from the day-factor table equals the number of holidays seeded for the year.

---

## Criterion 4 — Percentage Normalization

**Statement:** Two independent sums must land on exact round numbers:
- `SUM(Day % of Annual)` across all 365/366 days = **1.0000** (100.00%)
- `SUM(Day % of Month)` across the full year = **12.0000** (100% × 12 months)

**Why it matters:** These are the master invariants the entire daily-disaggregation model is built on. If they drift even slightly, every dollar figure downstream is proportionally wrong.

**Pass condition:** Both sums match to within 1e-6.

---

## Criterion 5 — Matrix Reconciliation

**Statement:** The network-level total (sum of every store's full-year total, as rolled up in `Monthly_Matrix`) must equal the sum of every store's Annual Plan Base from `Plan_Inputs`.

**Why it matters:** This is Criterion 1 re-checked at the network level — it catches a bug that happens to cancel out at the single-store level but shows up once you aggregate 65 stores.

**Pass condition:** `abs(networkTotalFromDailyRollup - networkTotalFromPlanBases) < $0.05`.

---

## Criterion 6 — DOW Sensitivity

**Statement:** Zeroing out one weekday's entered weight (e.g. setting Monday to 0%) must zero every Monday's `Day % of Annual` — **and** leave every month's total percentage completely unchanged (the lost Monday share redistributes to the other weekdays automatically via the normalization step).

**Why it matters:** Confirms the DOW slider in the UI actually drives the Excel formulas correctly, and that changing one lever doesn't silently break the "always sums to 100%" guarantee.

**Pass condition:** All Monday rows report `0%`; each month's `SUM(Day % of Month)` is unchanged before/after.

---

## Criterion 7 — Holiday Redistribution

**Statement:** Adding or removing a holiday must reallocate that day's dollars to the *other selling days in the same month* — the month's total planned dollars must stay **exactly the same**, only the daily split changes.

**Why it matters:** This is the core "intra-month redistribution" business rule (§2 of the original spec): when a store closes for a holiday, that day's expected revenue isn't lost from the annual plan — it's proportionally absorbed by the remaining open days in that month, so the month's committed number doesn't move.

**Pass condition:** Before/after adding a test holiday: `SUM(Day % of Month)` for the affected month stays `1.000000`, and the month's planned-dollar total matches within $0.05.

---

## Structural — Workbook Shape

**Statement:** The generated workbook actually contains every required sheet (`Exec_Summary`, `Calendar_Inputs`, `Day_Factors`, `Plan_Inputs`, `All_Stores_Summary`, `Monthly_Matrix`, `Daily_Disaggregated_Plan`, plus one tab per store), and each store tab's Diff cell (`T2`) is a literal formula (`=S2-Q2`), not a hardcoded value.

**Why it matters:** Guards against a regression where someone "optimizes" the export by writing a computed number into `T2` instead of a formula — which would make the diff check pass trivially every time regardless of whether the underlying math is actually correct.

**Pass condition:** All required sheet names present; `T2.formula === 'S2-Q2'`.

---

## Summary table

| # | Criterion | Guards against |
|---|---|---|
| 1 | Zero Difference | Per-store rounding/reconciliation drift |
| 2 | Store Isolation | Network total copy-pasted into every store tab |
| 3 | Closure Integrity | Excel's blank-string COUNTIFS bug over-counting closed days |
| 4 | Percentage Normalization | Drift in the master annual/monthly % invariants |
| 5 | Matrix Reconciliation | Store-level bug that only surfaces at network scale |
| 6 | DOW Sensitivity | DOW slider not actually driving the formulas; redistribution not happening on weight change |
| 7 | Holiday Redistribution | Holiday closures shrinking (rather than reallocating within) a month's total |
| — | Structural (Workbook Shape) | Diff check being faked with a hardcoded value instead of a live formula |
