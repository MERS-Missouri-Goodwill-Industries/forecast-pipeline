# MERS Goodwill — FY2027 Retail Sales Planning System

**Purpose of this document:** a self-contained briefing you can paste into Google AI Studio (or any
LLM) to get useful help on this project without the model needing the repo. It describes what exists
today, the exact math, the exact workbook cell layout, and the open questions.

Written 2026-08-21. Everything below reflects code that builds clean with all 12 acceptance
criteria passing.

---

## 1. The business problem

The COO (Mark Kahrs) builds the annual store sales budget by hand in Excel — roughly **20 hours**
of work, and FY2027 is the first year covering 60+ stores instead of 44. From the requirements
interview, his definition of success is explicitly *time saved*, not forecast accuracy:

> "This recommendation workbook is just going to save me hours of time… success is just the fact
> that I'm not doing all this manually this year and that you all are giving me something to start
> with, which is going to jump me ahead 20 plus hours of work."

He does **not** want a black box. He wants a defensible starting point he can override:

> "I really like the idea of you all giving me through technology a recommendation that I can work
> with… I don't want to over science it. I want the science to kind of inform, this is kind of where
> you want to go. And then I want the artist to get in there."

He works at **monthly** granularity, not daily:

> "I don't think he gets into the daily stuff… he'll only look at it if he knows the store is going
> to be closed and he wants to take it to 0."

**Consequence for design:** every number must be traceable, every override must be visible as a
variance against the forecast, and the COO must be able to zero out arbitrary date ranges without
breaking the rollups.

---

## 2. What exists

Two deliverables, plus an upstream model.

| Artifact | What it is | Status |
|---|---|---|
| **React planning app** | Local tool for the data team. Set day-of-week weights, set the network plan, override stores, export the workbook. | Working locally |
| **Excel workbook (73 sheets)** | The actual deliverable for the COO. Fully formula-driven — he changes a cell, everything recalculates. | Working |
| **Forecast model** | Trained/scored in Databricks. The app consumes its output. | Exists; not yet wired live |

### Stack

- React 19 + TypeScript 5.8 + Vite 6 + Tailwind 4
- ExcelJS — emits **formulas, not values**, so the workbook stays live after export
- Express backend + `@databricks/sql`
- Deployable as a Databricks App (`app.yaml`, Node.js custom app)

### Store roster

65 stores — 63 continuing, 2 new (`EURS` Eureka, `OFKS` O'Fallon Market Center), 0 closed.
Codes are the real POS codes from the FY2026 Daily Goals workbook (`ALTS`, `ARNS`, `LEAD`, …).

---

## 3. The math (this is the important part)

### 3.1 Day % of Annual — one flat weekday share, all year

Every day gets a share of the **whole year**, not of its month:

```
dayPctOfAnnual(d) = 0                                    if d is closed
                  = weekWeight(weekday(d)) / totalOpenWeight   otherwise

totalOpenWeight = Σ over all 365 days of (closed ? 0 : weekWeight(weekday))
```

One annual denominator means **the seven weekday values are identical in every month and
identical for every store**. An open Friday is worth the same slice of the year in January as in
July. The column sums to exactly `1.000000`.

Current values (FY2027, `recommended` preset):

| Day | Wkdy # | Day % of Annual | % of Week |
|---|---|---|---|
| Monday | 2 | 0.2616% | 13.4% |
| Tuesday | 3 | 0.2532% | 13.0% |
| Wednesday | 4 | 0.2540% | 13.0% |
| Thursday | 5 | 0.2538% | 13.0% |
| Friday | 6 | 0.3045% | 15.6% |
| Saturday | 7 | 0.3702% | 19.0% |
| Sunday | 1 | 0.2536% | 13.0% |
| **Total** | | **1.9507%** | **100.0%** |
| Weekend (Sat+Sun) | | | 32.0% |
| Weekday (Mon–Fri) | | | 68.0% |

### 3.2 Daily dollars

```
recommendedDaily(store, d) = forecastedPlanBase(store) × dayPctOfAnnual(d)
cooAdjustedDaily(store, d) = effectivePlanBase(store)  × dayPctOfAnnual(d)   ← editable
```

No month-level intermediate step. `Σ dayPctOfAnnual = 1.0` guarantees
`Σ dailyDollars = planBase` exactly (zero variance by construction).

### 3.3 Closure semantics — NOTE THIS, IT CHANGED

Closing days **removes them from their own month** and **preserves the store's annual total** —
the freed volume lifts the rest of the year, because the annual denominator shrinks.

- Close all of June → June = $0, annual total **unchanged**
- Close 3 days in June → June drops by those days, annual total **unchanged**

This is *annual* redistribution. An earlier version used *within-month* redistribution (a holiday's
dollars stayed inside its month, keeping the month total flat). Those two rules are mutually
exclusive: if January loses a day and the rest of January rises to compensate, January's Fridays
no longer equal July's Fridays — which breaks the flat-weekday-share requirement.

**To actually reduce a store's year** (e.g. a real revenue loss from renovation), lower its
`COO Adjusted Plan Base` on `Plan_Inputs`. Closures alone only reshape *when* the money lands.

### 3.4 Plan distribution

There is **no reallocation factor** anywhere. Each store's share is computed inline:

```
forecastedPlanBase(store) = baseSales(store) / Σ baseSales × recommendedPlan
```

New stores have no history, so `baseSales` is imputed as the average of up to 3 regional comps.
`Σ forecastedPlanBase = recommendedPlan` exactly.

---

## 4. Workbook structure

73 sheets, in this tab order:

| Sheet | Contents |
|---|---|
| `How_To_Use` | Plain-English guide, colour legend, sheet index, store-tab walkthrough |
| `Exec_Summary` | KPIs, monthly rollup, master store index |
| `All_Stores_Summary` | One row per store: actuals, forecast, COO adjustment, variance |
| `Monthly_Matrix` | Every store × 12 months, rolling to network total |
| `Daily_Disaggregated_Plan` | Flat 23,726-row feed for Power BI |
| `Calendar_Inputs` | **COO control centre** — weekday weights, holidays, month summary, Weekday Mix |
| `Day_Factors` | The 365-day engine |
| `Plan_Inputs` | Recommended Plan lever + per-store bases and overrides |
| 65 × store tabs | One 365-day schedule per store |

### Colour convention (used throughout)

| Style | Meaning |
|---|---|
| **Blue text on yellow fill** | Editable input — the only cells to type in |
| Black | Formula within the same sheet |
| Green | Cross-sheet reference |
| Red italic | Audit note / invariant explanation |

### 4.1 `Plan_Inputs`

```
A4  Recommended Plan        B4  150000000              ← INPUT
A5  COO Adjusted Plan       B5  =SUM($H$14:$H$78)
A6  Variance                B6  =B5-B4

Row 13 headers: Code | Name | Region | Status | Base Sales | Forecasted Plan Base
                | COO Adjusted Plan Base | Effective Plan Base | Variance
Rows 14–78 (65 stores):
  E  Base Sales (FY2026)                      ← INPUT
  F  =E14/SUM($E$14:$E$78)*$B$4               inline share, no named factor
  G  COO Adjusted Plan Base                   ← INPUT (blank by default)
  H  =IF(G14="",F14,G14)                      effective
  I  =H14-F14                                 variance
```

### 4.2 Store tab — exact cell map

This layout was specified by the COO-facing review and is identical on all 65 tabs.

```
Row 1:  A Store Code | B Store Name | C POS | D 2027 Planned Sales | E..P Jan..Dec | Q Total
        S COO Adjusted Planned Sales | T Variance | V..X Closure Start/End/Label

Row 2:  D2 "Recommended"        E2:P2  =$B$6 * SUM(E{monthStart}:E{monthEnd})
        Q2 =SUM(E2:P2)          S2 ='Plan_Inputs'!$H$n     T2 ='Plan_Inputs'!$I$n
Row 3:  D3 "COO Adjusted Plan"  E3:P3  =SUM(D{monthStart}:D{monthEnd})    ← sums the DAILY column
        Q3 =D374
Row 5:  Q5 "Diff"
Row 6:  A6 "Forecasted Plan Base"   B6 ='Plan_Inputs'!$F$n     Q6 =Q3-Q2

Row 8 headers:
  A Date | B Day of Week | C Recommended Sales | D COO Adjusted Plan | E Day % of Annual
  | F Holiday | G Month | H Recommended Month Total | I COO Month Total

Rows 9–373 (365 days):
  A ='Day_Factors'!A{n}      B ='Day_Factors'!C{n}
  C =$B$6 * E{row}           D =$S$2 * E{row}        ← D is the ONLY editable money column
  E ='Day_Factors'!F{n}      F ='Day_Factors'!D{n}   G =TEXT(A{row},"mmmm")
  On each month's LAST day only:
  H =SUM(C{mStart}:C{mEnd})  I =SUM(D{mStart}:D{mEnd})

Row 374: A "FULL YEAR"  C =SUM(C9:C373)  D =SUM(D9:D373)  E =SUM(E9:E373)
```

**Why `E3:P3` sums the daily column rather than recomputing from the base:** this is what makes
the COO's workflow work. He selects any run of cells in column D — a few days, half a month, a
whole month — presses Delete, and `COO Month Total` → row 3 → `Q3` → `Q6 Diff` all follow
automatically. Column C (Recommended) holds still, so the before/after comparison survives.

### 4.3 Two-track pattern

The whole workbook is built on a single idea, from the review:

> "Put the forecast in both columns, but he would only edit the one column and you'd be able to
> see the variances."

Recommended and COO Adjusted sit side by side at **day**, **month**, **store**, and **network**
level. Both read the same `Day % of Annual` column, so closures affect them identically.

---

## 5. Model comparison (real evaluation results)

Candidate models on the same holdout, ranked by WAPE:

| Model | WAPE | Accuracy | N Scored |
|---|---|---|---|
| **LightGBM** | **10.41%** | **89.59%** | 1,783 |
| Prophet | 12.45% | 87.55% | 1,755 |
| XGBoost | 12.56% | 87.44% | 1,783 |
| Mixed Effects | 13.84% | 86.16% | 1,603 |
| Naive Baseline | 14.27% | 85.73% | 1,692 |
| OLS | 15.80% | 84.20% | 1,692 |

**WAPE, not MAPE** — daily store sales contain many low/zero days (closures, slow days) which
distort a simple average percentage error. WAPE divides total absolute error by total actual sales,
so accuracy reflects the dollars that matter. Validation uses a rolling (walk-forward) window.

---

## 6. Databricks integration

| Setting | Value |
|---|---|
| Host | `adb-201205741376717.17.azuredatabricks.net` |
| HTTP Path | `/sql/1.0/warehouses/3d33bbee9a23df31` |
| Warehouse | Serverless Starter (`3d33bbee9a23df31`), Compute: Medium |
| Schema | `gold.retail_data_science` |
| Forecast table | `gold.retail_data_science.aggregate_sales_forecast` |
| Scenario table | `gold.retail_data_science.published_planning_scenarios` |

### The Run Forecast button

Wired end-to-end; only credentials are missing.

```
Click → POST /api/databricks/run-forecast
      → SELECT * FROM gold.retail_data_science.aggregate_sales_forecast
      → parse into Map<storeCode, forecastValue>
      → overlay onto each store's Forecasted Plan Base
```

Stores absent from the table keep their proportional share of the Recommended Plan.

**Auth** — two paths, auto-detected:
- Local dev: `DATABRICKS_TOKEN` (a PAT) in `.env`
- Production: bind the warehouse as an App Resource; Databricks injects
  `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` and OAuth M2M is preferred automatically

**Known fragility:** column matching is loose — first column containing `code`, first matching
`sales|forecast|planned`. And **if store codes don't match, it fails silently**: every row is
ignored, stores keep their fallback shares, and the status line reports
`Updated 0 store forecast(s)`. Watch that count on the first live run.

---

## 7. Verification

`npm run test:excel` — 12 criteria, all passing:

| # | Criterion | Guards against |
|---|---|---|
| 1 | Zero Difference | per-store rounding drift (max diff 0.0000) |
| 2 | Store Isolation | the network total being copied into every store tab |
| 3 | Closure Integrity | Excel's blank-string `COUNTIFS` bug over-counting closed days |
| 4 | Percentage Normalization | annual = 1.000000, month sum = 12.000000 |
| 5 | Matrix Reconciliation | store-level bug only visible at network scale |
| 6 | DOW Sensitivity | weight sliders not actually driving the formulas |
| 7 | **Weekday Share Is Flat All Year** | within-weekday spread across the year (must be `0.00e+0`) |
| 8 | Store Closure Handling | month → $0 on full close, annual total held |
| — | Dual Month Totals + COO Row Sums Daily | `H=SUM(C…)`, `I=SUM(D…)`, `E3=SUM(D…)` |
| — | Monthly Header Sums Its Own Days | month header decoupled from its daily rows |
| — | Workbook Shape | `Q6` Diff being faked with a hardcoded value |
| — | Formula Syntax Guard | `'Sheet'!COUNTIF(…)` — invalid syntax Excel silently strips |

That last one is worth knowing about: writing `'Day_Factors'!COUNTIF(...)` instead of
`COUNTIF('Day_Factors'!...)` produces a file Excel "repairs" on open by **deleting the formula**,
with only a vague warning. It happened once. The guard scans every generated formula for it.

---

## 8. Open items

1. **Forecast-implied YoY growth is a placeholder.** The most-repeated request from the review was
   to surface the growth already inside the forecast, so a flat growth adjustment is visibly *on
   top of* it rather than silently double-counting:

   > "If Mark's like, oh I want to grow at 8%, but there's already 5% built into it… it might get
   > unrealistic pretty quick."

   The number isn't available yet. `Plan_Inputs B5` is labelled and blank, and the additional
   growth adjustment defaults to **0%, not 3%**, deliberately.

2. **Ambiguous store-code mappings.** Six codes from the FY2026 Daily Goals workbook don't map
   1:1 and are still using auto-generated codes: Bridgeton Outlet (`BROS`), the O'Fallon MO/IL
   split (`OFAS`/`OFIS`), and Springfield Battlefield/Chestnut Crossing (`SPBS`/`SPCS`).

3. **Not yet deployed.** Databricks CLI auth + `databricks apps create/deploy` still to do.

4. **Next-phase ask** from the review — split donated goods vs new goods, so the COO can flex the
   new-goods programme without disturbing donated trend lines. Explicitly out of scope for FY2027.

5. **Weather features** were discussed (Open-Meteo, free, historical + forecast). Parked — the
   historical data already absorbs seasonal weather patterns, and future weather isn't predictable
   a year out.

---

## 9. Things to be careful about if you change this

- **Formulas, not values.** The workbook must stay live after export. Never write a computed number
  where a formula belongs — the `Q6` Diff check exists precisely to catch that.
- **`Day % of Annual` must total exactly 1.0.** Every dollar figure derives from it.
- **Flat weekday shares and within-month redistribution are incompatible.** Pick one knowingly.
- **Sheet-name escaping.** Store names contain apostrophes (`O'Fallon`), which must be doubled in
  formula references (`'OFAS - O''Fallon'!$D$27`).
- **Excel treats a formula returning `""` as non-blank.** Closed-day counts use
  `SUMPRODUCT(...*(LEN(x)>0))`, never `COUNTIFS(range,"<>")`.
- **Store tab row anchors are load-bearing.** Daily block = rows 9–373, total = row 374, month
  ranges are precomputed. `Monthly_Matrix` and the 23,726-row daily feed both reference them.
