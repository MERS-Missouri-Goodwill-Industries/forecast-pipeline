"""Acceptance tests.

test_deploy_config exists because of this deployment failure:

    "No command to run and no Python file found. Please add a 'command' field to your
     app.yml file."

That happens when app.yaml is missing, misnamed (app.yml), missing its `command` field,
or when `command` points at a file that is not in the deployed source. Every one of those
conditions is asserted below, so the failure is caught here rather than on deploy.

Run:  python -m pytest tests/ -q      (or: python tests/test_acceptance.py)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forecast_engine import (  # noqa: E402
    WEEKDAYS, build_day_factors, build_store_plan, compute_forecasted_bases,
    default_holidays, expand_closures, load_seed, month_row_ranges, weekday_mix,
)
from workbook import (  # noqa: E402
    build_workbook, tab_name, validate_plan, EXEC_CAL_SHEET_NAME, CAL_OFFSET,
    mm_coo_first_row, mm_coo_network_row, mm_rec_first_row, mm_rec_network_row,
)

YEAR = 2027
SEED = load_seed()
STORES = SEED["stores"]
WEIGHTS = SEED["dow_presets"]["recommended"]
HOLIDAYS = default_holidays(YEAR)
PLAN = 150_000_000.0


# =========================================================================================
# Deployment configuration -- guards the "No command to run" failure
# =========================================================================================
def test_deploy_config():
    # 1. Must be app.yaml at the deployed root. app.yml is NOT recognised.
    app_yaml = ROOT / "app.yaml"
    assert app_yaml.is_file(), "app.yaml missing from the deployed root directory"
    assert not (ROOT / "app.yml").exists(), (
        "app.yml found -- Databricks Apps reads app.yaml. Rename it."
    )

    raw = app_yaml.read_text(encoding="utf-8")
    try:
        import yaml
        cfg = yaml.safe_load(raw)
    except ImportError:                      # keep the guard working without PyYAML
        cfg = _minimal_yaml(raw)

    # 2. `command` must exist, be a list, and be non-empty.
    assert isinstance(cfg, dict), "app.yaml did not parse to a mapping"
    assert "command" in cfg, "app.yaml has no 'command' field -- this is the exact failure"
    cmd = cfg["command"]
    assert isinstance(cmd, list) and cmd, "'command' must be a non-empty list"
    assert all(isinstance(p, str) for p in cmd), "every 'command' entry must be a string"

    # 3. The entry point the command names must actually exist in the source.
    entry = next((p for p in cmd if p.endswith(".py")), None)
    assert entry, f"command names no .py entry point: {cmd}"
    assert (ROOT / entry).is_file(), f"command points at {entry!r}, which is not in the source"

    # 4. Port must be bound to the platform-provided variable, on all interfaces.
    joined = " ".join(cmd)
    assert "$DATABRICKS_APP_PORT" in joined, (
        "command must bind $DATABRICKS_APP_PORT -- it is the only variable interpolated here"
    )
    assert "0.0.0.0" in joined, "server must listen on 0.0.0.0, not localhost"

    # 5. Dependencies must be declared, and every local import must be present.
    reqs = ROOT / "requirements.txt"
    assert reqs.is_file(), "requirements.txt missing -- dependencies will not be installed"
    body = reqs.read_text(encoding="utf-8").lower()
    for pkg in ("streamlit", "openpyxl"):
        assert pkg in body, f"{pkg} missing from requirements.txt"

    for module in ("forecast_engine.py", "workbook.py", "databricks_io.py", "seed_data.json"):
        assert (ROOT / module).is_file(), f"{module} missing from the deployed source"

    # 6. Nothing that would leak a credential into the deployment.
    assert "DATABRICKS_TOKEN" not in raw, "app.yaml must not carry a token"
    assert not (ROOT / ".env").exists(), ".env must not be deployed"


def _minimal_yaml(raw: str) -> dict:
    """Tiny fallback parser for the command/env shape, used only if PyYAML is absent."""
    cfg, cur = {}, None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^command:\s*$", line):
            cfg["command"], cur = [], "command"
        elif re.match(r"^\w[\w-]*:", line):
            cur = None
        elif cur == "command":
            m = re.match(r'^\s*-\s*"?(.*?)"?\s*$', line)
            if m:
                cfg["command"].append(m.group(1))
    return cfg


def test_seed_data_intact():
    assert len(STORES) == 65
    assert {s["code"] for s in STORES} .__len__() == 65, "store codes must be unique"
    assert abs(sum(WEIGHTS.values()) - 1.0) < 5e-4
    assert len(HOLIDAYS) == 6


# =========================================================================================
# Math
# =========================================================================================
def _days():
    return build_day_factors(YEAR, WEIGHTS, HOLIDAYS)


def test_c1_zero_difference():
    days = _days()
    for st in [s for s in STORES if s["status"] == "Continuing"][:5]:
        plan = build_store_plan(float(st["base_sales"]), days)
        assert abs(plan["full_year_total"] - plan["plan_base"]) < 0.01


def test_c2_store_isolation():
    days = _days()
    picked = [s for s in STORES if s["status"] == "Continuing"][:5]
    totals = {round(build_store_plan(float(s["base_sales"]), days)["full_year_total"], 2)
              for s in picked}
    assert len(totals) == len(picked)


def test_c3_closure_integrity():
    days = _days()
    assert sum(1 for d in days if d["holiday_label"]) == len(HOLIDAYS)


def test_c4_percentage_normalization():
    days = _days()
    assert abs(sum(d["day_pct_of_annual"] for d in days) - 1.0) < 1e-9
    for m in range(1, 13):
        month = [d["day_pct_of_month"] for d in days if d["month"] == m]
        assert abs(sum(month) - 1.0) < 1e-9


def test_c5_matrix_reconciliation():
    bases = compute_forecasted_bases(STORES, PLAN)
    assert abs(sum(bases.values()) - PLAN) < 0.01
    assert all(v >= 0 for v in bases.values())


def test_c6_dow_sensitivity():
    days = build_day_factors(YEAR, {**WEIGHTS, "Monday": 0.0}, HOLIDAYS)
    assert all(d["day_pct_of_annual"] == 0 for d in days if d["weekday"] == "Monday")
    assert abs(sum(d["day_pct_of_annual"] for d in days) - 1.0) < 1e-9


def test_c7_weekday_share_flat_all_year():
    """The seven values must be identical in every month -- the core property."""
    days = _days()
    by_day: dict[str, set[float]] = {}
    for d in days:
        if not d["holiday_label"]:
            by_day.setdefault(d["weekday"], set()).add(round(d["day_pct_of_annual"], 15))
    assert set(by_day) == set(WEEKDAYS)
    for wd, shares in by_day.items():
        assert max(shares) - min(shares) < 1e-12, f"{wd} varies across the year"
    assert all(d["day_pct_of_annual"] == 0 for d in days if d["holiday_label"])


def test_c8_store_closure_handling():
    base = float(STORES[1]["base_sales"])
    full = expand_closures([{"start": f"{YEAR}-06-01", "end": f"{YEAR}-06-30"}])
    closed = build_store_plan(base, build_day_factors(YEAR, WEIGHTS, HOLIDAYS + full))
    assert abs(closed["monthly"][5]) < 0.01, "a fully closed month must be $0"
    assert abs(closed["full_year_total"] - base) < 0.05, "annual commitment must be held"

    partial = expand_closures([{"start": f"{YEAR}-06-10", "end": f"{YEAR}-06-12"}])
    p = build_store_plan(base, build_day_factors(YEAR, WEIGHTS, HOLIDAYS + partial))
    baseline = build_store_plan(base, _days())
    assert p["monthly"][5] < baseline["monthly"][5], "June must drop"
    assert abs(p["full_year_total"] - base) < 0.05


def test_weekday_weight_inputs_sum_to_exactly_100_pct():
    """Rounding each weekday's input independently can drift the total off 100% by a hair
    (e.g. 99.9999%) even though the seven raw weights are mathematically normalized. The
    last weekday must absorb that residual so the displayed Total is exact, not just close."""
    O = CAL_OFFSET
    for weights in (WEIGHTS, {d: 1.0 for d in WEEKDAYS}):  # preset, and the classic 1/7 case
        wb = build_workbook(year=YEAR, stores=STORES[:1], weights=weights, holidays=HOLIDAYS,
                            recommended_plan=PLAN)
        ecx = wb[EXEC_CAL_SHEET_NAME]
        vals = [ecx.cell(6 + i + O, 2).value for i in range(7)]
        assert sum(vals) == 1.0, f"weekday weight inputs sum to {sum(vals)!r}, not 1.0"


def test_weekday_mix_table():
    mix = weekday_mix(_days())
    assert len(mix) == 7
    assert abs(sum(m["pct_of_week"] for m in mix) - 1.0) < 1e-9
    assert mix[0]["weekday_number"] == 2 and mix[-1]["weekday_number"] == 1


# =========================================================================================
# Workbook structure
# =========================================================================================
def _wb(subset=None, overrides=None):
    return build_workbook(
        year=YEAR, stores=subset or STORES, weights=WEIGHTS, holidays=HOLIDAYS,
        recommended_plan=PLAN, store_overrides=overrides,
    )


def test_workbook_shape():
    wb = _wb(STORES[:3])
    expected = ["How_To_Use", "Exec Calendar Inputs", "Data_Validation", "All_Stores_Summary",
                "Monthly_Matrix", "Daily_Disaggregated_Plan", "Final_Sales_Goals",
                "Day_Factors", "Plan_Inputs"]
    assert wb.sheetnames[:9] == expected
    assert len(wb.sheetnames) == 9 + 3
    assert all(len(n) <= 31 for n in wb.sheetnames), "Excel caps sheet names at 31 chars"


def test_validate_plan_clean_input_is_all_ok():
    days = _days()
    planned = compute_forecasted_bases(STORES, PLAN)
    results = validate_plan(STORES, planned, days, overrides={})
    bad = [r for r in results if r["status"] != "OK"]
    assert not bad, f"clean input flagged something: {bad}"


def test_validate_plan_catches_duplicate_codes():
    days = _days()
    dup_stores = [STORES[0], {**STORES[1], "code": STORES[0]["code"]}]
    planned = {s["code"]: 1000.0 for s in dup_stores}
    results = validate_plan(dup_stores, planned, days, overrides={})
    hit = next(r for r in results if r["check"] == "Store codes are unique")
    assert hit["status"] == "ERROR"


def test_validate_plan_catches_negative_and_swing():
    days = _days()
    stores = STORES[:3]
    planned = compute_forecasted_bases(stores, PLAN)
    planned[stores[0]["code"]] = -500.0                                   # negative
    planned[stores[1]["code"]] = float(stores[1]["base_sales"]) * 10      # 10x swing

    results = validate_plan(stores, planned, days, overrides={})
    neg = next(r for r in results if "negative" in r["check"])
    assert neg["status"] == "ERROR" and stores[0]["code"] in neg["detail"]
    swing = next(r for r in results if "off its own prior-year" in r["check"])
    assert swing["status"] == "FLAG" and stores[1]["code"] in swing["detail"]


def test_validate_plan_catches_reversed_closure():
    days = _days()
    stores = STORES[:1]
    planned = compute_forecasted_bases(stores, PLAN)
    overrides = {stores[0]["code"]: {"closures": [
        {"start": f"{YEAR}-06-10", "end": f"{YEAR}-06-01"}]}}   # end before start
    results = validate_plan(stores, planned, days, overrides=overrides)
    hit = next(r for r in results if "on or before" in r["check"])
    assert hit["status"] == "ERROR"


def test_build_workbook_refuses_bad_input():
    dup_stores = [STORES[0], {**STORES[1], "code": STORES[0]["code"]}]
    try:
        _wb(dup_stores)
        assert False, "build_workbook should have raised on duplicate store codes"
    except ValueError as exc:
        assert "unique" in str(exc)


def test_data_validation_sheet_present_and_live():
    wb = _wb(STORES[:3])
    s = wb["Data_Validation"]
    assert s["A1"].value == "Data Validation"
    # static section: one row per validate_plan check, colour-coded by status
    assert s["A6"].value and s["B6"].value in {"OK", "FLAG", "ERROR"}
    # live section: one formula-driven reconciliation row per store, referencing its own Q6
    header_row = next(r for r in range(1, 30) if s.cell(r, 1).value == "Code")
    first_code_cell = s.cell(header_row + 1, 3).value
    assert isinstance(first_code_cell, str) and first_code_cell.startswith("=")
    assert "$Q$6" in first_code_cell


def test_store_tab_layout():
    stores = STORES[:3]
    wb = _wb(stores)
    days = _days()
    ranges = month_row_ranges(days, 9)
    total_row = 9 + len(days)

    for i, st in enumerate(stores):
        s = wb[tab_name(st)]
        # Two money columns side by side: C is the Recommended baseline, D is the COO's.
        assert [s.cell(8, c).value for c in range(1, 11)] == [
            "Date", "Day of Week", "Recommended Sales", "COO Adjusted Plan",
            "Day % of Annual", None, "Month",
            "Recommended Month Total", "COO Month Total", "Month Variance ($)"]
        assert s["D2"].value == "Recommended"
        assert s["D3"].value == "COO Adjusted Plan"
        assert s["D4"].value == "Variance ($)"
        assert s["Q2"].value == f"=C{total_row}"
        assert s["Q3"].value == f"=D{total_row}"
        assert s["Q4"].value == "=Q3-Q2"
        assert s["Q6"].value == "=Q3-$T$2"
        assert s["S1"].value == "Recommended Planned Sales"
        assert s["T1"].value == "COO Adjusted Planned Sales"
        assert s["U1"].value == "Variance ($)"
        assert s["U2"].value == "=T2-S2"
        assert s["V1"].value == "Closure Start"
        # Plan_Inputs stores start at row 14: E is Recommended, F is COO Adjusted.
        assert s["S2"].value == f"='Plan_Inputs'!$E${14 + i}", s["S2"].value
        assert s["T2"].value == f"='Plan_Inputs'!$F${14 + i}", s["T2"].value

        for m, (a, b) in enumerate(ranges):
            col = "EFGHIJKLMNOP"[m]
            assert s[f"{col}2"].value == f"=SUM(C{a}:C{b})", "row 2 must SUM the daily Recommended column"
            # The formula the whole delete-days workflow depends on:
            assert s[f"{col}3"].value == f"=SUM(D{a}:D{b})", "row 3 must SUM the daily COO column"
            assert s[f"{col}4"].value == f"={col}3-{col}2"
            assert s[f"H{b}"].value == f"=SUM(C{a}:C{b})"
            assert s[f"I{b}"].value == f"=SUM(D{a}:D{b})"
            assert s[f"J{b}"].value == f"=I{b}-H{b}"

        for r in (9, 150, total_row - 1):
            assert s[f"C{r}"].value == f"=$S$2 * E{r}"
            assert s[f"D{r}"].value == f"=$T$2 * E{r}"
            assert s[f"F{r}"].value is None, "no Holiday column without closures"
        assert s[f"C{total_row}"].value == f"=SUM(C9:C{total_row - 1})"
        assert s[f"D{total_row}"].value == f"=SUM(D9:D{total_row - 1})"


def test_formulas_not_values():
    """Every money cell must be a live formula -- the workbook is edited after export."""
    wb = _wb(STORES[:2])
    s = wb[tab_name(STORES[0])]
    for cell in ("C9", "D9", "E9", "Q2", "Q3", "Q4", "Q6", "H39", "I39", "J39"):
        v = s[cell].value
        assert isinstance(v, str) and v.startswith("="), f"{cell} is not a formula: {v!r}"


def test_no_sheet_qualified_function_call():
    """'Day_Factors'!COUNTIF(...) is invalid; Excel silently DELETES such formulas on open."""
    wb = _wb(STORES[:3])
    bad = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and re.search(r"![A-Za-z]{2,}\(", c.value):
                    bad.append(f"{ws.title}!{c.coordinate}: {c.value[:70]}")
    assert not bad, "malformed sheet-qualified call(s): " + "; ".join(bad[:5])


def test_apostrophe_sheet_names_escaped():
    quoted = [s for s in STORES if "'" in s["name"]]
    if not quoted:
        return
    wb = _wb([STORES[0], quoted[0]])
    mm = wb["Monthly_Matrix"]
    found = [mm.cell(r, 3).value for r in (3, 4)]
    target = [v for v in found if v and "''" in v]
    assert target, f"apostrophe not doubled in cross-sheet reference: {found}"


def test_closures_render_and_zero_days():
    code = STORES[0]["code"]
    wb = _wb(STORES[:2], overrides={code: {"closures": [
        {"start": f"{YEAR}-06-05", "end": f"{YEAR}-06-07", "label": "Renovation"}]}})
    s = wb[tab_name(STORES[0])]
    assert s["V2"].value is not None and s["X2"].value == "Renovation"
    assert "Store Closure" in s["E9"].value or "Store Closure" in s["F9"].value


def test_no_circular_reference():
    """Row 3 sums column D; column D must not read row 3 back."""
    wb = _wb(STORES[:2])
    s = wb[tab_name(STORES[0])]
    for r in (9, 200, 373):
        assert "$3" not in (s[f"D{r}"].value or ""), f"D{r} reads row 3 -- circular"


def test_column_widths_set():
    """Currency at Excel's ~8.43 default renders as ####."""
    wb = _wb(STORES[:2])
    s = wb[tab_name(STORES[0])]
    for col in ("C", "D", "E", "H", "I", "J", "Q", "S", "T"):
        assert (s.column_dimensions[col].width or 0) >= 12, f"column {col} too narrow"


# =========================================================================================
# Recommended vs COO Adjusted, and the variance between them
# =========================================================================================
def test_plan_inputs_carries_both_tracks_and_an_editable_total():
    wb = _wb(STORES[:3])
    s = wb["Plan_Inputs"]
    start, end = 14, 14 + 3 - 1

    assert [s.cell(13, c).value for c in range(1, 9)] == [
        "Code", "Name", "Region", "Status", "Recommended Planned Sales",
        "COO Adjusted Planned Sales", "Variance ($)", "Variance (%)"]

    assert s["B4"].value == f"=SUM($E${start}:$E${end})", "Recommended total"
    assert s["B6"].value == f"=SUM($F${start}:$F${end})", "COO total is bottom-up from stores"
    assert s["B7"].value == "=B6-B5", "allocation-vs-total reconciliation"
    assert s["B9"].value == "=B6-B4", "variance vs Recommended"

    # The COO's top-line number is a typed value, not a formula -- it is theirs to change.
    assert isinstance(s["B5"].value, (int, float)), f"B5 must be editable, got {s['B5'].value!r}"

    for i in range(3):
        r = start + i
        assert isinstance(s[f"E{r}"].value, (int, float)), "Recommended is a seeded number"
        assert isinstance(s[f"F{r}"].value, (int, float)), "COO Adjusted is a seeded number"
        assert s[f"G{r}"].value == f"=F{r}-E{r}"
        assert s[f"H{r}"].value == f"=IF(E{r}=0,0,G{r}/E{r})"


def test_supplied_forecasts_drive_the_recommended_column():
    """The whole point of Run Forecast. Before this wiring existed the app showed model
    numbers on screen while the workbook rebuilt the proportional split -- two different
    sets of figures in one session, with nothing flagging the disagreement."""
    stores = STORES[:3]
    split = compute_forecasted_bases(stores, PLAN)
    # A forecast for the first two stores only; the third must fall back to the split.
    supplied = {stores[0]["code"]: 1_234_567.0, stores[1]["code"]: 2_000_000.0}

    wb = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN, recommended_bases=supplied)
    s = wb["Plan_Inputs"]

    assert abs(s["E14"].value - 1_234_567.0) < 0.01, "forecast did not reach Recommended"
    assert abs(s["E15"].value - 2_000_000.0) < 0.01
    assert abs(s["E16"].value - split[stores[2]["code"]]) < 0.01, (
        "an uncovered store must fall back to the proportional split"
    )
    # COO Adjusted seeds from Recommended, so it follows the forecast too.
    assert abs(s["F14"].value - 1_234_567.0) < 0.01

    # The sheet must say where Recommended came from -- the Recommended Total means
    # something different depending on the answer.
    note = str(s["A3"].value)
    assert "model forecast for 2 of 3" in note, note
    assert "will not equal a round plan figure" in note, note


def test_recommended_defaults_to_the_split_when_no_forecast_supplied():
    stores = STORES[:3]
    split = compute_forecasted_bases(stores, PLAN)
    wb = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN)
    s = wb["Plan_Inputs"]
    for i, st in enumerate(stores):
        assert abs(s[f"E{14 + i}"].value - split[st["code"]]) < 0.01
    assert "base-sales-weighted split" in str(s["A3"].value)


def test_an_explicit_override_still_beats_a_supplied_forecast():
    """Precedence: COO override > model forecast > proportional split."""
    stores = STORES[:2]
    code = stores[0]["code"]
    wb = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN,
                        recommended_bases={code: 1_000_000.0},
                        store_overrides={code: {"plan_base": 500_000.0}})
    s = wb["Plan_Inputs"]
    assert abs(s["E14"].value - 1_000_000.0) < 0.01, "Recommended shows the forecast"
    assert abs(s["F14"].value - 500_000.0) < 0.01, "COO Adjusted shows the override"
    assert s["G14"].value == "=F14-E14", "variance exposes the difference"


def test_recommended_column_is_untouched_by_an_override():
    """An override moves the COO track only. If it moved Recommended too, the variance would
    always read zero and the COO would have no before-and-after."""
    code = STORES[0]["code"]
    baseline = compute_forecasted_bases(STORES[:3], PLAN)[code]
    wb = _wb(STORES[:3], overrides={code: {"plan_base": baseline * 1.10}})
    s = wb["Plan_Inputs"]
    assert abs(s["E14"].value - baseline) < 0.01, "override leaked into Recommended"
    assert abs(s["F14"].value - baseline * 1.10) < 0.01, "override not applied to COO Adjusted"


def test_exec_summary_shows_both_numbers_and_the_variance():
    wb = _wb(STORES[:3])
    s = wb[EXEC_CAL_SHEET_NAME]
    assert s["A5"].value == "Recommended Planned Sales ($)"
    assert s["B5"].value == "='Plan_Inputs'!$B$4"
    assert s["A6"].value == "COO Adjusted Planned Sales ($)"
    assert s["B6"].value == "='Plan_Inputs'!$B$6"
    assert s["A7"].value == "Variance ($)" and s["B7"].value == "=B6-B5"
    assert s["A8"].value == "Variance (%)"

    # Monthly comparison table: both tracks plus the variance, per month.
    assert [s.cell(15, c).value for c in range(1, 7)] == [
        "Month", "% of Annual", "Recommended ($)", "COO Adjusted ($)",
        "Variance ($)", "Variance (%)"]
    for m in range(12):
        r = 16 + m
        assert s[f"E{r}"].value == f"=D{r}-C{r}", f"row {r} variance"
    assert s["A28"].value == "TOTAL"


def test_monthly_matrix_stacks_both_tracks_with_a_variance_row():
    stores = STORES[:3]
    n = len(stores)
    wb = _wb(stores)
    s = wb["Monthly_Matrix"]
    coo_net, rec_first, rec_net = (mm_coo_network_row(n), mm_rec_first_row(n),
                                   mm_rec_network_row(n))
    assert s[f"A{coo_net}"].value == "NETWORK TOTAL"
    assert s[f"A{rec_net}"].value == "NETWORK TOTAL"
    first = tab_name(stores[0])
    assert s[f"C{mm_coo_first_row()}"].value == f"='{first}'!$E$3", "COO block reads store row 3"
    assert s[f"C{rec_first}"].value == f"='{first}'!$E$2", "Recommended block reads store row 2"
    var_row = rec_net + 2
    assert s[f"A{var_row}"].value == "VARIANCE"
    assert s[f"C{var_row}"].value == f"=C{coo_net}-C{rec_net}"


def test_all_stores_summary_shows_variance_per_store():
    stores = STORES[:3]
    wb = _wb(stores)
    s = wb["All_Stores_Summary"]
    assert [s.cell(1, c).value for c in range(1, 9)] == [
        "Store Code", "Store Name", "Region", "Status", "Recommended ($)",
        "COO Adjusted ($)", "Variance ($)", "Variance (%)"]
    first = tab_name(stores[0])
    assert s["E2"].value == f"='{first}'!$Q$2"
    assert s["F2"].value == f"='{first}'!$Q$3"
    assert s["G2"].value == "=F2-E2"


def test_no_red_text_on_a_white_background():
    """Red on white reads as an error even when the message is informational, and it is the
    first thing to go illegible on a projector or printed page."""
    wb = _wb(STORES[:3])

    def reddish(rgb):
        if not isinstance(rgb, str) or len(rgb) < 6:
            return False
        try:
            r, g, b = (int(rgb[-6:][i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return False
        return r > 120 and r > g + 50 and r > b + 50

    offenders = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value is None:
                    continue
                if "[Red]" in (c.number_format or ""):
                    offenders.append(f"{ws.title}!{c.coordinate} [Red] number format")
                    continue
                colour = c.font.color.rgb if (c.font and c.font.color) else None
                if not reddish(colour):
                    continue
                fg = c.fill.fgColor.rgb if (c.fill and c.fill.fgColor) else None
                if c.fill is None or c.fill.patternType is None or fg in (None, "00000000", "FFFFFFFF"):
                    offenders.append(f"{ws.title}!{c.coordinate} red font {colour} on white")
    assert not offenders, "red on white: " + "; ".join(offenders[:6])


def test_how_to_use_states_what_can_and_cannot_change():
    wb = _wb(STORES[:2])
    s = wb["How_To_Use"]
    text = " ".join(str(c.value) for row in s.iter_rows() for c in row if c.value)
    assert "WHAT YOU CAN CHANGE" in text
    assert "WHAT NOT TO CHANGE" in text
    # The three levers the COO actually has authority over.
    assert "1. The total" in text
    assert "2. The day inputs" in text
    assert "3. Any individual store" in text


def _agg_result(rows):
    return {"columns": ["store_code", "forecast_total", "first_date", "last_date", "n_days"],
            "rows": rows}


def _full_year_rows(codes, value=1000.0, year=YEAR):
    return [{"store_code": c, "forecast_total": value,
             "first_date": f"{year}-01-01", "last_date": f"{year}-12-31", "n_days": 365}
            for c in codes]


def test_databricks_parsing_and_reconciliation():
    import databricks_io as io

    good = _agg_result(_full_year_rows([s["code"] for s in STORES]))
    parsed, warn = io.parse_store_forecasts(good, plan_year=YEAR)
    assert warn is None and len(parsed) == 65
    rec = io.reconcile(parsed, [s["code"] for s in STORES])
    assert rec["matched"] == 65 and rec["expected"] == 65

    bad_cols = {"columns": ["a", "b"], "rows": [{"a": 1, "b": 2}]}
    _, warn = io.parse_store_forecasts(bad_cols)
    assert warn, "unmatched columns must produce a warning, not silence"

    wrong_codes = _agg_result(_full_year_rows(["ZZZZ"]))
    parsed, _ = io.parse_store_forecasts(wrong_codes, plan_year=YEAR)
    rec = io.reconcile(parsed, [s["code"] for s in STORES])
    assert rec["matched"] == 0 and rec["missing_from_databricks"], (
        "a code mismatch must be detectable, not silent"
    )


def test_every_forecast_query_is_scoped_to_one_calendar_year():
    """Once the horizon reached into FY2027 the forecast spanned two calendar years. Summing
    every forecast row then gave ~a third of 2026 on top of all of 2027 -- day percentages
    read 130%, and the annual totals were overstated by the same ~30% while looking entirely
    plausible. The horizon guard cannot catch that: 2027 really is fully covered, the number
    is just too big. Only a year filter catches it."""
    import databricks_io as io

    q = io._FORECAST_QUERY.format(table=io.FORECAST_TABLE, split=io.FORECAST_SPLIT, year=2027)
    assert "YEAR(date) = 2027" in q, "the totals query must be scoped to the plan year"

    seen = []
    real = io.execute
    try:
        def capture(stmt):
            seen.append(stmt)
            cols = ["unique_id", "date", io.DAY_PCT_COLUMN, "forecast_lower", "forecast_upper"]
            return {"columns": cols, "rows": [{}], "source": "live"}
        io.execute = capture
        io.fetch_day_pct_forecast(plan_year=2027)
        io.fetch_monthly_forecast_band(plan_year=2027)
    finally:
        io.execute = real

    # The probes (SELECT * ... LIMIT 1) need no filter; the aggregating queries do.
    aggregating = [s for s in seen if "LIMIT 1" not in s]
    assert aggregating, "expected at least one aggregating query"
    for stmt in aggregating:
        assert "YEAR(date) = 2027" in stmt, f"unscoped query would mix years:\n{stmt}"


def test_forecast_query_targets_the_forward_looking_split():
    """'test' rows are a backtest holdout scored against known actuals. Summing those would
    be planning off history."""
    import databricks_io as io
    q = io._FORECAST_QUERY.format(table=io.FORECAST_TABLE, split=io.FORECAST_SPLIT,
                                 year=YEAR)
    assert "split = 'forecast'" in q
    assert "SUM(forecast)" in q and "GROUP BY unique_id" in q
    assert "actual_sales" not in q, "must never plan off the actuals column"


def test_short_horizon_is_refused_not_silently_summed():
    """The live table covers 2026-08-25 to 2026-11-22. Summed and called an annual figure it
    understates the plan by ~75%, and the number still looks like money -- so it has to fail
    loudly rather than flow through."""
    import databricks_io as io

    ninety_day = _agg_result([
        {"store_code": s["code"], "forecast_total": 500_000.0,
         "first_date": "2026-08-25", "last_date": "2026-11-22", "n_days": 90}
        for s in STORES[:5]
    ])
    parsed, warn = io.parse_store_forecasts(ninety_day, plan_year=2027)
    assert parsed == {}, "nothing may be applied from a partial-year forecast"
    assert warn and "does not overlap" in warn, warn

    # Same window, planning 2026: overlaps, but only 90 of 365 days.
    parsed, warn = io.parse_store_forecasts(ninety_day, plan_year=2026)
    assert parsed == {} and warn and "understate" in warn, warn

    # A full year passes.
    parsed, warn = io.parse_store_forecasts(
        _agg_result(_full_year_rows([s["code"] for s in STORES[:5]])), plan_year=YEAR)
    assert warn is None and len(parsed) == 5

    # Without a plan year the guard stays out of the way.
    parsed, warn = io.parse_store_forecasts(ninety_day)
    assert warn is None and len(parsed) == 5


def test_day_mix_offer_reports_a_missing_column_instead_of_erroring():
    """day_of_year_forecast_pct was not in the table as of 2026-09-10. A hard-coded SELECT
    would surface as a raw SQL error, so the offer probes for the column and says plainly
    that the weekday weights are still in charge."""
    import databricks_io as io

    real_execute = io.execute
    try:
        io.execute = lambda stmt: {
            "columns": ["unique_id", "date", "forecast", "split", "segment"],
            "rows": [{}], "source": "live",
        }
        res = io.fetch_day_pct_forecast(plan_year=YEAR)
        assert res["status"] == "missing_column", res
        assert io.DAY_PCT_COLUMN in res["message"]
        assert "weekday weights" in res["message"], "must say what is still driving the split"
        assert res["day_pct"] == {}
    finally:
        io.execute = real_execute


def _fake_day_pct(rows, n_days):
    """Stand in for the two queries fetch_day_pct_forecast runs."""
    import databricks_io as io

    def fake(stmt):
        if "LIMIT 1" in stmt:
            return {"columns": ["unique_id", "date", io.DAY_PCT_COLUMN, "split"],
                    "rows": [{}], "source": "live"}
        return {"columns": ["store_code", "date", "day_pct", "first_date", "last_date"],
                "rows": rows, "source": "live"}
    return fake


def test_day_mix_on_a_percent_scale_is_read_correctly_not_called_broken():
    """Live data arrived on a 0-100 percent scale covering ~90 days, so per-store sums were
    ~24.8 rather than ~1.0. Judging the scale by the sum flagged correct data as broken --
    and would have failed a perfect full-year table too, since that sums to ~100. Scale is
    decided from a single day's magnitude instead, and the shortfall reported as coverage."""
    import databricks_io as io

    real_execute = io.execute
    try:
        # 90 days at ~0.2757 each -> 24.8, matching the real ALTS figure.
        rows = [{"store_code": "ALTS", "date": f"2026-09-{(d % 28) + 1:02d}", "day_pct": 0.2757,
                 "first_date": "2026-08-25", "last_date": "2026-11-22"} for d in range(90)]
        # Dates must be distinct or they collapse into one dict key.
        for i, r in enumerate(rows):
            r["date"] = (dt.date(2026, 8, 25) + dt.timedelta(days=i)).isoformat()

        io.execute = _fake_day_pct(rows, 90)
        res = io.fetch_day_pct_forecast(plan_year=2027)

        assert res["status"] == "unusable", res
        msg = res["message"]
        assert "look right" in msg, "correct-but-partial data must not be called broken"
        assert "percent scale" in msg, "must say how the values were read"
        assert "24.8" in msg or "25." in msg, f"must report actual coverage: {msg}"
        assert "horizon" in msg, "must name the real blocker"
        assert res["day_pct"] == {}, "still must not apply partial-year shares"
    finally:
        io.execute = real_execute


def test_day_mix_full_year_on_a_percent_scale_is_accepted():
    import databricks_io as io

    real_execute = io.execute
    try:
        rows = [{"store_code": "ALTS",
                 "date": (dt.date(YEAR, 1, 1) + dt.timedelta(days=i)).isoformat(),
                 "day_pct": 100.0 / 365,
                 "first_date": f"{YEAR}-01-01", "last_date": f"{YEAR}-12-31"}
                for i in range(365)]
        io.execute = _fake_day_pct(rows, 365)
        res = io.fetch_day_pct_forecast(plan_year=YEAR)
        assert res["status"] == "ok", res
        assert abs(sum(res["day_pct"]["ALTS"].values()) - 1.0) < 1e-6, "normalized to a fraction"
    finally:
        io.execute = real_execute


def _contrast(fg: str, bg: str) -> float:
    def lum(h):
        h = h.lstrip("#")
        chan = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        r, g, b = (f(c) for c in chan)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_highlight_colours_clear_wcag_aa_and_never_rely_on_the_theme():
    """A fill set without a foreground inherits the theme's text colour. That put white on
    amber at 1.21:1 -- invisible. Highlights set both, so the cell reads the same on a white
    or a near-black page."""
    import app as _app  # noqa: F401 - imported for its PAL constant only

    ratio = _contrast(_app.PAL["flag_fg"], _app.PAL["flag_bg"])
    assert ratio >= 4.5, f"highlight contrast {ratio:.2f}:1 is below WCAG AA (4.5:1)"

    src = (ROOT / "app.py").read_text(encoding="utf-8")
    # Every background-color we set must be accompanied by a colour on the same rule.
    for line in src.splitlines():
        if "background-color" in line and "PAL[" in line:
            assert "color:" in line or "flag_fg" in line, (
                f"background set without a foreground: {line.strip()}"
            )


def test_the_53_marker_is_not_colour_alone():
    """WCAG 1.4.1. Colour-blind readers and greyscale printouts still need the signal."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'f"{v:.0f} *" if v == 53' in src, "the 53 highlight needs a non-colour marker"


def test_displayed_weekday_weights_total_exactly_100_pct():
    """The app showed 'Total: 100.01%' on the 2026 preset. Rounding each weekday
    independently to 2 decimals of a percent leaves a residual with nowhere to go. The
    workbook already absorbed it into the last weekday; the app did not, so the same
    numbers disagreed between the screen and the file."""
    from forecast_engine import rounded_weights

    for name, raw in load_seed()["dow_presets"].items():
        shown = rounded_weights(raw, places=4)
        total = sum(shown.values())
        assert abs(total - 1.0) < 1e-9, f"{name} displays {total * 100:.2f}%, not 100.00%"
        assert len(shown) == 7

    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    # The number inputs must read weights_raw. Seeding them from ss.weights re-rounds each
    # day independently and puts the drift straight back on the next run.
    assert "value=round(ss.weights_raw.get(d, 0.0) * 100, 2)" in app_src, (
        "weekday number_inputs must seed from weights_raw, not re-round ss.weights"
    )


def test_default_weekday_preset_is_the_2026_coo_weights():
    """The COO plans against his own 2026 curve, so the app opens on it."""
    seed = load_seed()
    assert "excel_plan" in seed["dow_presets"], "the 2026 preset must exist"
    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'ss.setdefault("preset", "excel_plan")' in app_src, (
        "default weekday preset should be the 2026 COO workbook weights"
    )


def test_horizon_check_boundaries():
    import databricks_io as io
    import datetime as _dt
    ok, _ = io.horizon_check(_dt.date(2027, 1, 1), _dt.date(2027, 12, 31), 2027)
    assert ok
    ok, msg = io.horizon_check(None, None, 2027)
    assert not ok and "no usable date range" in msg
    # A year's worth of days, but shifted so only part lands inside the plan year.
    ok, _ = io.horizon_check(_dt.date(2026, 7, 1), _dt.date(2027, 6, 30), 2027)
    assert not ok, "only half the window falls inside the plan year"

    assert io.auth_mode() in {"mock", "pat", "oauth", "oauth-u2m"}
    assert io.FORECAST_TABLE == "gold.retail_data_science.test_agg_sales_forecast"


def test_oauth_u2m_is_opt_in_not_a_silent_fallback():
    """DATABRICKS_AUTH_TYPE=u2m is for workspaces where PATs are disabled: browser sign-in,
    no secret in any file. It must never fire on its own -- a deployed app's container has
    no browser and no human to click "Allow"."""
    import databricks_io as io

    saved = {k: os.environ.pop(k, None) for k in
              ("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_CLIENT_ID",
               "DATABRICKS_CLIENT_SECRET", "DATABRICKS_TOKEN", "DATABRICKS_AUTH_TYPE")}
    try:
        os.environ["DATABRICKS_HOST"] = "example.azuredatabricks.net"
        os.environ["DATABRICKS_HTTP_PATH"] = "/sql/1.0/warehouses/x"

        assert io.auth_mode() == "mock", "no auth configured must stay mock, not u2m"

        os.environ["DATABRICKS_AUTH_TYPE"] = "u2m"
        assert io.auth_mode() == "oauth-u2m"

        os.environ["DATABRICKS_CLIENT_ID"] = "id"
        os.environ["DATABRICKS_CLIENT_SECRET"] = "secret"
        assert io.auth_mode() == "oauth", "M2M must take priority over U2M when both are set"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print("\n" + ("ALL TESTS PASSED" if not failures else f"{failures} FAILURE(S)"))
    sys.exit(1 if failures else 0)
