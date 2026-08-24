# Build Spec — FY2027 Sales Planning App, native on Databricks

**How to use this file.** Paste it into Databricks Assistant (or any coding LLM) as the build brief
for rebuilding this tool as a Databricks App. It is written to be executed, not skimmed: every
formula, cell address and invariant needed is stated explicitly.

**One clarification before you start.** AI/BI **Genie** is natural-language-to-SQL over a data
room — it answers questions about data, it does not generate applications. The parts of this spec
Genie *can* own are in §3 (a Genie space over the forecast tables, so the COO can ask "what is
Alton's plan for March"). Everything else — the app and the workbook generator — needs
**Databricks Assistant** or equivalent. Both are covered below.

Reference implementation this is derived from: a working React + ExcelJS app, 65 stores,
12/12 acceptance criteria passing.

---

## 1. What to build

A Databricks App that:

1. Reads per-store annual sales forecasts from Unity Catalog
2. Lets a planner set day-of-week weights, a network plan total, and per-store overrides
3. Emits a **fully formula-driven** 73-sheet Excel workbook the COO edits offline
4. Writes the agreed scenario back to Unity Catalog

The workbook is the actual deliverable. The app exists to produce it. The COO's stated success
metric is **~20 hours saved**, not forecast accuracy — so traceability and easy override beat
sophistication everywhere they conflict.

### Target runtime

| Choice | Recommendation |
|---|---|
| Runtime | Python (Streamlit or Dash) — best-supported on Databricks Apps. Node/React also supported. |
| Excel writer | `openpyxl` (needs formula strings + styling; `xlsxwriter` cannot re-open files) |
| SQL access | `databricks-sql-connector` |
| Compute size | **Medium** (2 vCPU / 6 GB, 0.5 DBU/hr) — ample |

---

## 2. Data contract

```
Workspace : adb-201205741376717.17.azuredatabricks.net
HTTP path : /sql/1.0/warehouses/3d33bbee9a23df31
Schema    : gold.retail_data_science
```

| Table | Role |
|---|---|
| `gold.retail_data_science.aggregate_sales_forecast` | **Input.** Per-store annual forecast. |
| `gold.retail_data_science.published_planning_scenarios` | **Output.** Saved scenarios (`id`, `payload` JSON). |

### Required shape of the input table

The app needs, at minimum, one row per store with a store code and an annual dollar figure:

```sql
SELECT store_code, forecast_sales
FROM gold.retail_data_science.aggregate_sales_forecast;
```

> **Confirm before coding.** The exact column names are not yet fixed, and `store_code` **must**
> match the app's roster (`ALTS`, `ARNS`, `LEAD`, `BAYS`, …). A code mismatch is the most likely
> failure and it fails *silently* — every row is ignored and stores fall back to a proportional
> share. Always report how many stores were matched out of 65 and fail loudly under that.

### Auth

Do not hard-code a token. Bind the SQL warehouse as an **App Resource** in the Apps UI; Databricks
injects `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`, and the SDK picks them up:

```python
from databricks import sql
from databricks.sdk.core import Config

cfg = Config()  # reads injected OAuth env vars
conn = sql.connect(
    server_hostname=cfg.host,
    http_path=os.environ["DATABRICKS_HTTP_PATH"],
    credentials_provider=lambda: cfg.authenticate,
)
```

Grant the app's service principal `CAN_USE` on the warehouse and `SELECT` on the schema.

---

## 3. Optional: a Genie space (this is the part Genie is for)

Create a Genie space over `aggregate_sales_forecast` and the published scenarios so the COO can
ask questions in plain language instead of opening the workbook.

Recommended instructions for the space:

```
This data room contains FY2027 retail sales plans for MERS Goodwill, 65 stores.
- "Recommended" / "forecast" = the model output, before any human change.
- "COO Adjusted" / "plan" / "budget" = the committed number after overrides.
- "Variance" always means COO Adjusted minus Recommended.
A store's annual plan is split across days by "Day % of Annual" (see below); months are
the sum of their days. Never present a month total that was not summed from daily rows.
```

Genie handles the *questions*. It does not build the app or the workbook.

---

## 4. Core math — implement exactly

This is the part that must be correct. Everything downstream is arithmetic on it.

### 4.1 Day % of Annual

Every day gets a share of the **whole year**, using **one annual denominator**:

```python
def build_day_factors(year: int, week_weights: dict[str, float], closed_dates: set) -> list[dict]:
    """week_weights: {'Monday': 0.1341, ...} — need not sum to 1, it is normalized here."""
    import datetime as dt

    total_w = sum(week_weights.values())
    norm = {k: v / total_w for k, v in week_weights.items()}

    days = []
    d = dt.date(year, 1, 1)
    while d.year == year:
        wd = d.strftime("%A")
        days.append({"date": d, "weekday": wd, "month": d.month,
                     "closed": d in closed_dates, "weight": norm[wd]})
        d += dt.timedelta(days=1)

    # ONE denominator for the whole year: total weight of every OPEN day.
    open_weight = sum(x["weight"] for x in days if not x["closed"])

    for x in days:
        x["day_pct_of_annual"] = 0.0 if x["closed"] else x["weight"] / open_weight

    return days
```

**Why one annual denominator:** it makes the seven weekday values identical in every month and
identical for every store. An open Friday is worth the same slice of the year in January as in
July. `sum(day_pct_of_annual) == 1.0` exactly.

Do **not** renormalize per month. That was an earlier design and it is incompatible with flat
weekday shares — if January loses a day and the rest of January rises to compensate, January's
Fridays no longer equal July's.

### 4.2 Daily dollars

```python
recommended_daily = forecasted_plan_base * day_pct_of_annual
coo_adjusted_daily = effective_plan_base  * day_pct_of_annual   # editable
```

No month-level intermediate. Because the shares sum to 1.0, the year total equals the plan base
exactly — zero variance by construction.

### 4.3 Closure semantics

Closing days removes them from their month and **preserves the store's annual total** (the
denominator shrinks, so remaining days lift slightly).

- Close all of June → June `$0`, annual total **unchanged**
- Close 3 days → those days leave June, annual total **unchanged**

To genuinely *reduce* a store's year, lower its **COO Adjusted Plan Base**. Closures reshape
*when* revenue lands, not how much.

### 4.4 Plan distribution — no reallocation factor

```python
forecasted_plan_base[s] = base_sales[s] / sum(base_sales.values()) * recommended_plan
```

Computed inline; there is no named reallocation factor anywhere in the app or workbook. New stores
have no history — impute `base_sales` as the mean of up to 3 same-region comparable stores.
`sum(forecasted_plan_base) == recommended_plan` exactly.

### 4.5 Current weekday weights

Normalized, sum to 100%:

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

Weekend (Sat+Sun) 32.0% · Weekday (Mon–Fri) 68.0%

FY2027 network holidays (all stores closed): Jan 1, Easter (Apr 18… *verify*), Jul 4,
Thanksgiving, Dec 24, Dec 25.

---

## 5. Workbook generation — the main deliverable

73 sheets in this tab order:

| # | Sheet | Contents |
|---|---|---|
| 1 | `How_To_Use` | Plain-English guide, colour legend, sheet index, store-tab walkthrough |
| 2 | `Exec_Summary` | KPIs, monthly rollup, store index |
| 3 | `All_Stores_Summary` | One row per store: actuals, forecast, adjustment, variance |
| 4 | `Monthly_Matrix` | Store × 12 months → network total |
| 5 | `Daily_Disaggregated_Plan` | Flat 23,726-row feed for Power BI |
| 6 | `Calendar_Inputs` | Weekday weights, holidays, month summary, Weekday Mix |
| 7 | `Day_Factors` | The 365-day engine |
| 8 | `Plan_Inputs` | Plan lever + per-store bases and overrides |
| 9–73 | 65 × store tabs | One 365-day schedule per store |

### Non-negotiable: emit formulas, not values

The COO edits this workbook after export. Write `"=B6*E27"`, never a computed number. Anything
pre-computed is silently wrong the moment he changes an input.

### Colour convention

| Style | Meaning |
|---|---|
| **Blue text (`FF0000FF`) on yellow fill (`FFFFFF99`)** | Editable input — the only cells to type in |
| Black | Formula, same sheet |
| Green (`FF008000`) | Cross-sheet reference |
| Red italic (`FFCC0000`) | Audit note |

### 5.1 `Plan_Inputs`

```
A4 Recommended Plan     B4  150000000                ← INPUT
A5 COO Adjusted Plan    B5  =SUM($H$14:$H$78)
A6 Variance             B6  =B5-B4

Row 13: Code | Name | Region | Status | Base Sales | Forecasted Plan Base
        | COO Adjusted Plan Base | Effective Plan Base | Variance
Rows 14–78 (65 stores):
  E  Base Sales                              ← INPUT
  F  =E14/SUM($E$14:$E$78)*$B$4
  G  COO Adjusted Plan Base                  ← INPUT, blank by default
  H  =IF(G14="",F14,G14)
  I  =H14-F14
```

### 5.2 Store tab — exact cell map (identical on all 65)

```
Row 1: A Store Code | B Store Name | C POS | D 2027 Planned Sales | E..P Jan..Dec | Q Total
       S COO Adjusted Planned Sales | T Variance | V/W/X Closure Start/End/Label

Row 2: D2 "Recommended"        E2:P2 =$B$6 * SUM(E{mStart}:E{mEnd})
       Q2 =SUM(E2:P2)          S2 ='Plan_Inputs'!$H$n     T2 ='Plan_Inputs'!$I$n
Row 3: D3 "COO Adjusted Plan"  E3:P3 =SUM(D{mStart}:D{mEnd})    ← sums the DAILY column
       Q3 =D374
Row 5: Q5 "Diff"
Row 6: A6 "Forecasted Plan Base"  B6 ='Plan_Inputs'!$F$n    Q6 =Q3-Q2

Row 8: A Date | B Day of Week | C Recommended Sales | D COO Adjusted Plan
       | E Day % of Annual | F Holiday | G Month
       | H Recommended Month Total | I COO Month Total

Rows 9–373 (365 days):
  A ='Day_Factors'!A{n}     B ='Day_Factors'!C{n}
  C =$B$6 * E{row}          D =$S$2 * E{row}      ← D is the ONLY editable money column
  E ='Day_Factors'!F{n}     F ='Day_Factors'!D{n}
  G =TEXT(A{row},"mmmm")
  On each month's LAST day only:
    H =SUM(C{mStart}:C{mEnd})      I =SUM(D{mStart}:D{mEnd})

Row 374: A "FULL YEAR"  C =SUM(C9:C373)  D =SUM(D9:D373)  E =SUM(E9:E373)
```

**The single most important formula is `E3:P3`.** It sums the daily COO column rather than
recomputing from the base. That is what lets the COO select any run of cells in column D — three
days, half a month, a whole month — press Delete, and have `COO Month Total` → row 3 → `Q3` →
`Q6 Diff` all follow automatically, while column C holds still as the before-picture.

If you implement row 3 as `=$S$2 * SUM(E…)` instead, deleting days will appear to do nothing and
the whole workflow silently breaks.

### 5.3 Two-track pattern

Recommended and COO Adjusted sit side by side at **day**, **month**, **store** and **network**
level. Both read the same `Day % of Annual` column. From the requirements review:

> "Put the forecast in both columns, but he would only edit the one column and you'd be able to
> see the variances."

### 5.4 `Calendar_Inputs` blocks

| Rows | Block |
|---|---|
| 5–13 | Weekday weights — **one** editable column, `B13 = SUM(B6:B12)` |
| 15–26 | Holiday dates + labels (editable), day-of-week derived |
| 28–41 | Month summary: days, closed days, selling days, % of annual |
| 46–57 | **Weekday Mix** — Day, Wkdy #, Day % of Annual, % of Week, + Weekend/Weekday split |

One weight column only. An earlier version had "Entered Weight" *and* "Normalized Weight" side by
side, which confused readers about which one to change.

---

## 6. App UI

| Panel | Behaviour |
|---|---|
| Day-of-Week Weighting | 7 rows, slider **and** typeable % box, kept in sync. Live total with tolerance colouring. Presets: Recommended (from Actuals), Current Excel Plan, Even Split, Custom. |
| Recommended Plan | Editable network total; shows COO Adjusted total and variance. East/West split bar. |
| Weekday Counts | How many of each weekday per year, 4 years side by side (53-count weekdays flagged) |
| Store table | Per store: Forecasted / COO Adjusted (editable, yellow+blue) / Variance |
| Daily schedule modal | Per-store 365-day view; closure date-range editor |
| **Run Forecast** | Large header button → query `aggregate_sales_forecast` → overlay per store → report **how many of 65 matched** |
| Export | Emits `POC_Prototype_2027_Planned_Sales_Workbook_2027.xlsx` |
| Contacts / User Guide | Header icons — who owns it, how to read it |

---

## 7. Acceptance tests — port these

Run against generated output before shipping. All 12 pass in the reference implementation.

| # | Assert | Guards against |
|---|---|---|
| 1 | For each store, `abs(daily_total − plan_base) < $0.01` | rounding drift |
| 2 | Stores with distinct bases have distinct totals | network total copied into every tab |
| 3 | Closed-day count == holiday count | Excel's blank-string `COUNTIFS` bug |
| 4 | `sum(day_pct_of_annual) == 1.000000` | the master invariant |
| 5 | `sum(store totals) == sum(plan bases)` | store bug only visible at network scale |
| 6 | Zeroing a weekday zeroes its rows | weights not driving the formulas |
| 7 | **Within-weekday spread across the year == 0** | per-month renormalization creeping back |
| 8 | Full-month close → month `$0`, annual total unchanged | closure semantics |
| 9 | `H{monthEnd}=SUM(C…)`, `I{monthEnd}=SUM(D…)`, `E3=SUM(D…)` | the deletion workflow |
| 10 | Month header sums its own daily rows | header decoupled from days |
| 11 | Required sheets present; `Q6` is a formula | Diff faked with a hardcoded value |
| 12 | No formula matches `![A-Za-z]{2,}\(` | see gotcha below |

---

## 8. Deployment

```yaml
# app.yaml
command: ["python", "app.py"]
env:
  - name: "DATABRICKS_HTTP_PATH"
    value: "/sql/1.0/warehouses/3d33bbee9a23df31"
```

`DATABRICKS_HOST`, `DATABRICKS_APP_PORT`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` are
injected automatically — do not declare them. Bind the warehouse via **+ Add resource** in the
Apps UI. Listen on `DATABRICKS_APP_PORT`. Premium tier workspace required.

**Cost:** Medium = 0.5 DBU/hr, billed *per hour while running* (provisioned, no scale-to-zero).
Roughly $200–350/month if left up 24/7. **Stop the app between planning sessions** — this is a
seasonal tool for ~3 people and idle time is the entire cost.

---

## 9. Gotchas that have already bitten this project

1. **`'Sheet'!FUNC(...)` is invalid Excel.** Writing `'Day_Factors'!COUNTIF($B$2:$B$366,A2)`
   instead of `COUNTIF('Day_Factors'!$B$2:$B$366,A2)` makes Excel "repair" the file on open by
   **silently deleting the formula**, with only a vague warning. Test 12 exists solely for this.

2. **Excel treats a formula returning `""` as non-blank.** `COUNTIFS(range,"<>")` will count 365
   closed days instead of 6. Use `SUMPRODUCT((month=X)*(LEN(holidayCell)>0))`.

3. **Apostrophes in sheet names must be doubled** in references:
   `'OFAS - O''Fallon'!$D$27`.

4. **Never let the month header and its daily rows disagree.** If a month can be computed two ways
   they will drift. Row 3 sums its days; the Diff cell is a pure error signal, not a place for a
   real discrepancy to hide.

5. **Circular references.** Daily `D` is `$S$2 * E`; row 3 sums `D`. Do not also make daily `D`
   read row 3 — Excel resolves the cycle to `0` and quietly zeroes a month.

6. **Set every column width explicitly.** Currency columns at Excel's ~8.43 default render as
   `####`.

7. **Row anchors are load-bearing.** Daily block rows 9–373, total row 374. `Monthly_Matrix` and
   the 23,726-row feed both reference them by absolute position.

---

## 10. Open items

1. **Forecast-implied YoY growth is unresolved.** The most-repeated review request was to surface
   the growth already inside the forecast, so any added growth is visibly *on top of* it:
   *"if Mark's like, oh I want to grow at 8%, but there's already 5% built into it… it might get
   unrealistic pretty quick."* The number isn't available yet — keep the field labelled and blank,
   and default any additional growth adjustment to **0%, not 3%**.

2. **Six store codes are unmapped** — Bridgeton Outlet, the O'Fallon MO/IL split, and Springfield
   Battlefield/Chestnut Crossing. Confirm before relying on code joins.

3. **Out of scope for FY2027, requested for later:** splitting donated goods vs new goods so the
   new-goods programme can flex without disturbing donated trend lines.
