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
    DEFAULT_GROWTH_RATE, DEFAULT_STORE_GROWTH, MIN_FLAT_RUN, _cents, borrow_day_shapes,
    flat_stretches, grown_forecast,
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
def _wb(subset=None, overrides=None, growth_rate=None):
    kw = {} if growth_rate is None else {"growth_rate": growth_rate}
    return build_workbook(
        year=YEAR, stores=subset or STORES, weights=WEIGHTS, holidays=HOLIDAYS,
        recommended_plan=PLAN, store_overrides=overrides, **kw,
    )


def _recommended(s, row):
    """Plan_Inputs column E is `=J*$B$4*(1+$B$9)*(1+K)`: the store's forecast share, grown by
    the all-stores rate in B9 and by its own rate in K. openpyxl never evaluates a formula,
    so reproduce the arithmetic and assert the shape while we are here -- a test that
    silently stopped resolving the real formula would pass on anything."""
    expected = f"=ROUND(J{row}*$B$4*(1+$B$9)*(1+K{row}),2)"
    assert s[f"E{row}"].value == expected, s[f"E{row}"].value
    return _cents(float(s[f"J{row}"].value) * float(s["B4"].value)
                  * (1.0 + float(s["B9"].value)) * (1.0 + float(s[f"K{row}"].value)))


def _forecast_of(s, row):
    """The store's share of the model forecast, before any growth is applied."""
    return float(s[f"J{row}"].value) * float(s["B4"].value)


def test_workbook_shape():
    wb = _wb(STORES[:3])
    # Plan_Inputs leads; the daily feed and the day engine close, after every store tab.
    head = ["Plan_Inputs", "How_To_Use", "Exec Calendar Inputs", "Data_Validation",
            "All_Stores_Summary", "Monthly_Matrix", "Final_Sales_Goals"]
    tail = ["Daily_Disaggregated_Plan", "Day_Factors"]
    assert wb.sheetnames[:len(head)] == head
    assert wb.sheetnames[-len(tail):] == tail
    assert wb.sheetnames[len(head):-len(tail)] == [tab_name(st) for st in STORES[:3]], (
        "the store tabs belong between the working sheets and the two closing ones"
    )
    assert len(wb.sheetnames) == 9 + 3
    assert all(len(n) <= 31 for n in wb.sheetnames), "Excel caps sheet names at 31 chars"


def _selected_tabs(data: bytes) -> list[str]:
    """Names of the sheets the SAVED file flags as selected, read from the raw XML."""
    import re
    import zipfile
    from io import BytesIO

    z = zipfile.ZipFile(BytesIO(data))
    book = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    target = {}
    for m in re.finditer(r"<Relationship [^>]*>", rels):
        tag = m.group(0)
        rid = re.search(r'Id="([^"]+)"', tag).group(1)
        target[rid] = re.search(r'Target="([^"]+)"', tag).group(1).lstrip("/")
    out = []
    for name, rid in re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', book):
        part = target[rid] if target[rid].startswith("xl/") else "xl/" + target[rid]
        if re.search(r'tabSelected="(1|true)"', z.read(part).decode("utf-8")[:2000]):
            out.append(name)
    return out


def test_exactly_one_tab_is_selected_after_the_reorder():
    """Reordering sheets can leave two flagged as selected, and Excel then opens them as a
    GROUP: whatever the COO types on one sheet lands on every sheet in the group. That would
    quietly write over 65 store tabs, so it is asserted on the saved file, not the object."""
    from workbook import workbook_bytes

    data = workbook_bytes(year=YEAR, stores=STORES[:3], weights=WEIGHTS, holidays=HOLIDAYS,
                          recommended_plan=PLAN)
    assert _selected_tabs(data) == ["Plan_Inputs"], _selected_tabs(data)


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

    assert [s.cell(13, c).value for c in range(1, 12)] == [
        "Code", "Name", "Region", "Status", "Recommended Planned Sales",
        "COO Adjusted Planned Sales", "Variance ($)", "Variance (%)",
        "% of Agency", "Multiplier", "Store Growth %"]

    # B4 is a constant on purpose. Column E is priced off it, so `=SUM(E)` would be a
    # circular reference -- Excel would blank the whole sheet rather than warn.
    assert isinstance(s["B4"].value, (int, float)), f"B4 must be a constant, got {s['B4'].value!r}"
    assert s["B6"].value == f"=SUM($F${start}:$F${end})", "COO total is bottom-up from stores"
    assert s["B7"].value == "=B6-B5", "allocation-vs-total reconciliation"
    # B8 is the sum of the stores' Recommended, not a formula of its own: with a rate per
    # store there is no single "forecast times one rate" any more.
    assert s["B8"].value == f"=SUM($E${start}:$E${end})", "planned total is the sum of column E"
    assert s["B10"].value == "=B6-B8", "variance vs Recommended"

    # Typed cells on this sheet: the top line, the all-stores rate, and one rate per store.
    assert isinstance(s["B5"].value, (int, float)), f"B5 must be editable, got {s['B5'].value!r}"
    assert isinstance(s["B9"].value, (int, float)), f"B9 must be editable, got {s['B9'].value!r}"

    for i in range(3):
        r = start + i
        assert s[f"E{r}"].value == f"=ROUND(J{r}*$B$4*(1+$B$9)*(1+K{r}),2)", "Recommended follows both rates"
        assert isinstance(s[f"K{r}"].value, (int, float)), "each store's rate is typed, not derived"
        assert "$B$8" not in str(s[f"E{r}"].value), (
            "column E must not read B8: B8 is =SUM(E), so that would be a circular reference"
        )
        assert isinstance(s[f"F{r}"].value, (int, float)), "COO Adjusted is a seeded number"
        assert isinstance(s[f"J{r}"].value, (int, float)), "the multiplier is a constant"
        assert s[f"G{r}"].value == f"=F{r}-E{r}"
        assert s[f"H{r}"].value == f"=IF(E{r}=0,0,G{r}/E{r})"
        assert s[f"I{r}"].value == f"=IF($B$6=0,0,F{r}/$B$6)", "% of agency is of the COO total"


def _model_day_pct(days, skew=0.0, scale=1.0):
    raw = {d["date"]: (1.0 + skew * (i % 7)) * scale for i, d in enumerate(days)}
    return raw


def test_model_day_mix_is_opt_in_and_per_store():
    """Opt-in by design: the weekday curve is the COO's lever and stays the default. A store
    with no model data keeps the flat curve, so Criterion 7 still holds for it."""
    stores = STORES[:3]
    days = _days()
    # Both series must carry a real shape. skew=0 is FLAT, and a flat series is treated as
    # "no shape" and replaced by a borrowed one -- that is the behaviour under test elsewhere.
    supplied = {stores[0]["code"]: _model_day_pct(days, skew=0.15),
                stores[1]["code"]: _model_day_pct(days, skew=0.3)}

    wb = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN, day_pct_by_store=supplied)

    for st in stores[:2]:
        s = wb[tab_name(st)]
        assert s["AE8"].value == "Model Day % of Year"
        assert s["E9"].value == "=AE9", "day share must come from this store's own column"

    # The store without model data is untouched.
    s3 = wb[tab_name(stores[2])]
    assert s3["AE9"].value is None
    assert str(s3["E9"].value).startswith("='Day_Factors'"), s3["E9"].value

    # And with nothing supplied at all, every store is on the shared curve.
    plain = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                           recommended_plan=PLAN)
    for st in stores:
        assert plain[tab_name(st)]["AE9"].value is None


def test_model_day_shares_total_exactly_one():
    """Every daily figure is priced off this column. Rounding 365 values independently left
    it summing to 1.00000145, which would push Q6 -- the cell that proves a store's days add
    up to its annual input -- off zero for no visible reason."""
    stores = STORES[:1]
    days = _days()
    code = stores[0]["code"]

    for scale in (1.0, 3.7, 0.02):          # unnormalized input must be normalized, not trusted
        wb = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                            recommended_plan=PLAN,
                            day_pct_by_store={code: _model_day_pct(days, 0.2, scale)})
        s = wb[tab_name(stores[0])]
        total = sum(s[f"AE{9 + i}"].value for i in range(len(days)))
        assert abs(total - 1.0) < 1e-12, f"scale {scale}: AE sums to {total!r}"


def test_model_day_mix_still_honours_closures():
    stores = STORES[:1]
    days = _days()
    code = stores[0]["code"]
    wb = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN,
                        day_pct_by_store={code: _model_day_pct(days)},
                        store_overrides={code: {"closures": [
                            {"start": f"{YEAR}-06-01", "end": f"{YEAR}-06-30"}]}})
    e9 = wb[tab_name(stores[0])]["E9"].value
    assert 'IF(F9<>""' in e9, "closed days must read 0%"
    assert "AE9 / SUMPRODUCT" in e9 and "$AE$" in e9, (
        f"must renormalize over the model column, not the weekday one: {e9}"
    )


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

    assert abs(_forecast_of(s, 14) - 1_234_567.0) < 0.01, "forecast did not reach Recommended"
    assert abs(_forecast_of(s, 15) - 2_000_000.0) < 0.01
    assert abs(_forecast_of(s, 16) - split[stores[2]["code"]]) < 0.01, (
        "an uncovered store must fall back to the proportional split"
    )
    # COO Adjusted seeds from Recommended, so it follows the forecast AND the growth on it.
    # Seeding from the ungrown forecast would open the workbook already down by the growth.
    assert abs(s["F14"].value - _recommended(s, 14)) < 0.01
    assert abs(s["F14"].value - 1_234_567.0 * (1 + float(s["K14"].value))
               * (1 + float(s["B9"].value))) < 0.01

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
        assert abs(_forecast_of(s, 14 + i) - split[st["code"]]) < 0.01
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
    assert abs(_forecast_of(s, 14) - 1_000_000.0) < 0.01, "Recommended shows the forecast"
    assert abs(s["F14"].value - 500_000.0) < 0.01, "COO Adjusted shows the override"
    assert s["G14"].value == "=F14-E14", "variance exposes the difference"


def test_recommended_column_is_untouched_by_an_override():
    """An override moves the COO track only. If it moved Recommended too, the variance would
    always read zero and the COO would have no before-and-after."""
    code = STORES[0]["code"]
    baseline = compute_forecasted_bases(STORES[:3], PLAN)[code]
    wb = _wb(STORES[:3], overrides={code: {"plan_base": baseline * 1.10}})
    s = wb["Plan_Inputs"]
    assert abs(_forecast_of(s, 14) - baseline) < 0.01, "override leaked into Recommended"
    assert abs(s["F14"].value - baseline * 1.10) < 0.01, "override not applied to COO Adjusted"


def test_the_growth_rate_reaches_every_store_and_the_shares_total_one():
    """B9 is the lever the model cannot pull for itself, so it has to actually move things.

    Guards the three ways the hand-built version of this sheet was inert:

    1. The share column summed to 0.9709, so the network total quietly missed 2.9% of
       itself -- about $4.5M on a $155M plan -- with nothing on screen saying so.
    2. `Sum of Store Plans` and `COO Adjusted Total` were the same SUM(F), so the balance
       check subtracted a number from itself and read "Balanced" no matter what.
    3. Nothing referenced the growth rate at all. Typing 10% into it changed one display
       cell and not one store's goal.
    """
    stores = STORES[:5]
    forecasts = {st["code"]: 1_000_000.0 * (i + 1) for i, st in enumerate(stores)}
    total_forecast = sum(forecasts.values())

    for growth in (0.0, 0.05, 0.12):
        wb = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                            recommended_plan=PLAN, recommended_bases=forecasts,
                            growth_rate=growth)
        s = wb["Plan_Inputs"]
        start, end = 14, 14 + len(stores) - 1

        assert abs(float(s["B9"].value) - growth) < 1e-12, "growth rate did not reach B9"
        assert abs(float(s["B4"].value) - total_forecast) < 0.01, "B4 is the forecast total"

        # 1. Exactly 1, not 0.9709. Rounding 65 shares independently leaves a residual;
        #    the largest store absorbs it.
        shares = [float(s[f"J{r}"].value) for r in range(start, end + 1)]
        assert abs(sum(shares) - 1.0) < 1e-9, f"multipliers total {sum(shares)!r}, not 1"

        # 3. Every store moves with the rate, and they still add to the grown total. Each
        #    store also carries the 1% default of its own, so the expectation includes it.
        resolved = [_recommended(s, r) for r in range(start, end + 1)]
        for i, st in enumerate(stores):
            expected = forecasts[st["code"]] * (1 + DEFAULT_STORE_GROWTH) * (1 + growth)
            assert abs(resolved[i] - expected) < 0.01, (
                f"{st['code']} at growth {growth}: {resolved[i]:,.2f} != {expected:,.2f}"
            )
        assert abs(sum(resolved) - total_forecast * (1 + DEFAULT_STORE_GROWTH) * (1 + growth)) \
            <= 0.005 * len(stores) + 1e-6

    # 2. The balance check must compare the store rows against the TYPED total in B5.
    #    B6 and B7 holding the same formula is what made it always read zero.
    assert s["B7"].value == "=B6-B5"
    assert s["B6"].value != s["B5"].value, "the check would be comparing a cell to itself"
    assert "SUM" not in str(s["B5"].value), "B5 is typed, not summed"


def test_the_screen_and_the_file_apply_growth_the_same_way():
    """app.py computes `effective` for the screen; workbook.py computes `planned_sales` for
    the file. They are two implementations of one rule, and this project has already shipped
    that shape of bug once -- 60 stores disagreed between the page and the download because
    the forecasts were never passed through. Growth is a second chance to do it again.
    """
    stores = STORES[:4]
    forecasts = {st["code"]: 2_000_000.0 + 100_000 * i for i, st in enumerate(stores)}
    override_code = stores[1]["code"]
    overrides = {override_code: 9_999_999.0}
    growth = 0.07
    # Stores 0 and 2 carry their own rate; 1 is overridden (so its rate must not matter);
    # 3 is left on the default.
    store_growth = {stores[0]["code"]: 0.03, stores[1]["code"]: 0.40, stores[2]["code"]: -0.02}

    def rate(code):
        return store_growth.get(code, DEFAULT_STORE_GROWTH)

    # The screen's rule. app.py calls grown_forecast, so using it here IS the screen's rule;
    # the source assertions below pin that app.py really does.
    screen = {c: (overrides[c] if c in overrides
                  else grown_forecast(forecasts[c], rate(c), growth))
              for c in forecasts}

    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "grown_forecast(forecasted.get(c, 0.0), store_rate(c), ss.growth_rate)" in app_src, (
        "app.py must price Recommended with the shared grown_forecast, not its own copy"
    )
    assert "store_growth={s[\"code\"]: store_rate(s[\"code\"]) for s in STORES}" in app_src, (
        "app.py must hand the per-store rates to the workbook, or the download would price "
        "every store at the default while the screen showed the COO's edits"
    )

    wb = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN, recommended_bases=forecasts,
                        growth_rate=growth, store_growth=store_growth,
                        store_overrides={c: {"plan_base": v} for c, v in overrides.items()})
    s = wb["Plan_Inputs"]
    for i, st in enumerate(stores):
        file_value = float(s[f"F{14 + i}"].value)
        assert abs(file_value - screen[st["code"]]) < 0.01, (
            f"{st['code']}: screen {screen[st['code']]:,.2f} != file {file_value:,.2f}"
        )
    # Each store is rounded to the cent in the file and not on the screen, so the totals may
    # differ by up to half a cent per store.
    assert abs(float(s["B5"].value) - sum(screen.values())) <= 0.005 * len(stores) + 1e-6, (
        "top line disagrees"
    )

    # An override is a decision, not a baseline: growth must not be applied on top of it.
    assert abs(float(s[f"F{15}"].value) - 9_999_999.0) < 0.01, "growth marked up an override"


def test_growth_defaults_are_one_percent_per_store_and_zero_for_the_network():
    """The all-stores rate in B9 starts at 0% and each store's own rate starts at 1%.

    The split is deliberate: a network-wide rate already baked into every store is a number
    nobody chose, while a per-store default is one the COO can see in column K and change.
    """
    assert DEFAULT_GROWTH_RATE == 0.0
    assert DEFAULT_STORE_GROWTH == 0.01

    stores = STORES[:5]
    wb = _wb(stores)
    s = wb["Plan_Inputs"]
    assert float(s["B9"].value) == 0.0, f"B9 should open at 0%, got {s['B9'].value!r}"
    for i in range(len(stores)):
        assert abs(float(s[f"K{14 + i}"].value) - 0.01) < 1e-12, "every store opens at 1%"

    # And the file opens balanced: COO seed == Recommended, so no variance nobody created.
    for i in range(len(stores)):
        r = 14 + i
        assert abs(float(s[f"F{r}"].value) - _recommended(s, r)) < 0.01, (
            f"row {r}: the COO track did not seed from the 1%-grown recommendation"
        )


def test_a_stores_growth_rate_moves_that_store_and_no_other():
    """The reason column K exists: change one store without touching the rest."""
    stores = STORES[:4]
    forecasts = {st["code"]: 3_000_000.0 for st in stores}
    target = stores[2]["code"]

    base = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                          recommended_plan=PLAN, recommended_bases=forecasts)["Plan_Inputs"]
    bumped = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                            recommended_plan=PLAN, recommended_bases=forecasts,
                            store_growth={target: 0.08})["Plan_Inputs"]

    for i, st in enumerate(stores):
        r = 14 + i
        b, n = _recommended(base, r), _recommended(bumped, r)
        if st["code"] == target:
            assert abs(float(bumped[f"K{r}"].value) - 0.08) < 1e-12
            assert abs(n - 3_000_000.0 * 1.08) < 0.01, f"{st['code']}: {n:,.2f}"
            assert n > b
        else:
            assert abs(n - b) < 0.01, f"{st['code']} moved when only {target} was edited"
            assert abs(float(bumped[f"K{r}"].value) - 0.01) < 1e-12


def test_the_two_growth_rates_compound():
    """A 3% store under a 2% network rate carries 1.03 x 1.02 = 5.06%, not 5%. Compounding
    is the documented behaviour; this pins it so the two cannot silently become additive."""
    stores = STORES[:2]
    forecasts = {st["code"]: 1_000_000.0 for st in stores}
    s = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                       recommended_plan=PLAN, recommended_bases=forecasts,
                       growth_rate=0.02,
                       store_growth={stores[0]["code"]: 0.03})["Plan_Inputs"]
    assert abs(_recommended(s, 14) - 1_000_000.0 * 1.03 * 1.02) < 0.01
    assert abs(_recommended(s, 14) - 1_050_600.0) < 0.01, "5.06%, not 5%"
    assert grown_forecast(1_000_000.0, 0.03, 0.02) == 1_000_000.0 * 1.03 * 1.02


def test_an_override_ignores_the_stores_growth_rate():
    """An override is the COO's own number. A rate on top of it would mark up a decision."""
    stores = STORES[:2]
    code = stores[0]["code"]
    s = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                       recommended_plan=PLAN, recommended_bases={c["code"]: 2_000_000.0 for c in stores},
                       growth_rate=0.10, store_growth={code: 0.50},
                       store_overrides={code: {"plan_base": 1_234_000.0}})["Plan_Inputs"]
    assert abs(float(s["F14"].value) - 1_234_000.0) < 0.01, "growth marked up an override"
    # ...but Recommended still carries both rates, so the variance shows the gap.
    assert abs(_recommended(s, 14) - 2_000_000.0 * 1.50 * 1.10) < 0.01


def test_plan_inputs_total_row_reports_the_blended_store_rate():
    """K's total is weighted by forecast share. A plain average would let a tiny outlet
    count as much as the largest store."""
    stores = STORES[:2]
    forecasts = {stores[0]["code"]: 9_000_000.0, stores[1]["code"]: 1_000_000.0}
    s = build_workbook(year=YEAR, stores=stores, weights=WEIGHTS, holidays=HOLIDAYS,
                       recommended_plan=PLAN, recommended_bases=forecasts,
                       store_growth={stores[0]["code"]: 0.02,
                                     stores[1]["code"]: 0.10})["Plan_Inputs"]
    total_row = 14 + len(stores)
    assert s[f"A{total_row}"].value == "TOTAL"
    formula = str(s[f"K{total_row}"].value)
    assert "SUMPRODUCT" in formula and f"J14:J{total_row - 1}" in formula, formula
    # Resolve it: 0.9 x 2% + 0.1 x 10% = 2.8%, not the plain average 6%.
    j = [float(s[f"J{r}"].value) for r in (14, 15)]
    k = [float(s[f"K{r}"].value) for r in (14, 15)]
    blended = sum(a * b for a, b in zip(j, k)) / sum(j)
    assert abs(blended - 0.028) < 1e-9, blended


def test_growth_chosen_in_the_app_lands_in_the_downloaded_workbook():
    """End to end through the REAL app.py: set rates in session state, click Build Workbook,
    and read column K back out of the file the app produced.

    The earlier screen-vs-file test pins the source text of the call. This one proves the
    behaviour -- that what the COO selected on the page is what is in the file -- which is
    the thing that has gone wrong in this project before (60 stores once disagreed between
    the page and the download because a value was never passed through).

    workbook_bytes is wrapped, not replaced, so the genuine call from app.py is what runs.
    """
    import io as _io

    import openpyxl
    from streamlit.testing.v1 import AppTest

    import workbook as _wb_mod

    captured = {}
    real = _wb_mod.workbook_bytes

    def spy(**kwargs):
        captured["kwargs"] = kwargs
        captured["bytes"] = real(**kwargs)
        return captured["bytes"]

    _wb_mod.workbook_bytes = spy
    try:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        chosen = {"ALTS": 0.08, "LEAD": 0.0, "CAPS": -0.03}
        at.session_state["store_growth"] = chosen
        at.session_state["growth_rate"] = 0.02
        at.run()
        assert not at.exception, at.exception
        buttons = [b for b in at.button if "Build Workbook" in (b.label or "")]
        assert buttons, "Build Workbook button not found"
        buttons[0].click().run()
        assert not at.exception, at.exception
    finally:
        _wb_mod.workbook_bytes = real

    assert "bytes" in captured, "clicking Build Workbook never reached workbook_bytes"
    sheet = openpyxl.load_workbook(_io.BytesIO(captured["bytes"]))["Plan_Inputs"]

    assert abs(float(sheet["B9"].value) - 0.02) < 1e-12, "the all-stores rate did not arrive"
    by_code = {sheet[f"A{r}"].value: r for r in range(14, 14 + len(STORES))}
    for code, rate in chosen.items():
        got = float(sheet[f"K{by_code[code]}"].value)
        assert abs(got - rate) < 1e-12, f"{code}: app chose {rate}, workbook has {got}"

    # Every OTHER store must read the 1% default, not 0 and not blank.
    others = [c for c in by_code if c not in chosen]
    assert others, "no stores left to check"
    for code in others:
        got = float(sheet[f"K{by_code[code]}"].value)
        assert abs(got - DEFAULT_STORE_GROWTH) < 1e-12, f"{code} opened at {got}, not 1%"


def _varied_shape(dates, seed):
    """A day-share series with genuine shape, normalised to one year."""
    import math
    import random

    r = random.Random(seed)
    raw = {d: 1.0 + 0.6 * math.sin(i / 11.0) + r.uniform(-0.25, 0.25)
           + (0.3 if i % 7 in (4, 5) else 0.0) for i, d in enumerate(dates)}
    total = sum(raw.values())
    return {d: v / total for d, v in raw.items()}


def _new_stores():
    return [st for st in STORES if st["status"] not in ("Continuing", "Closed")]


def _distinct_shares(sheet):
    return len({round(float(sheet[f"AE{r}"].value), 12) for r in range(9, 9 + 365)})


def test_a_new_store_never_ends_up_with_the_same_goal_every_day():
    """The reported problem. A new store has no history, so the model either has no shape for
    it or a flat one, and both used to reach the workbook as a repeating pattern while every
    neighbouring store carried 365 distinct days:

      * missing from the model -> fell back to the weekday curve: 6 distinct daily goals in
        365 days, every Monday identical
      * present but flat       -> literally the same goal every day (2 distinct values)
    """
    news = _new_stores()
    assert news, "the roster needs at least one new store for this to mean anything"
    dates = [d["date"] for d in build_day_factors(YEAR, WEIGHTS, HOLIDAYS)]
    own = {st["code"]: _varied_shape(dates, i) for i, st in enumerate(STORES)
           if st["status"] == "Continuing"}
    flat = {d: 1.0 / 365 for d in dates}

    for label, extra in (("missing from the model", {}),
                         ("flat in the model", {st["code"]: dict(flat) for st in news})):
        # NB: `flat` has no closed days. The live table's flat stores DO -- see the next test.
        wb = build_workbook(year=YEAR, stores=STORES, weights=WEIGHTS, holidays=HOLIDAYS,
                            recommended_plan=PLAN, day_pct_by_store={**own, **extra})
        for st in news:
            sheet = wb[tab_name(st)]
            assert sheet["AE8"].value == "Borrowed Day % of Year", (label, st["code"])
            assert sheet["B6"].value == "Borrowed"
            n = _distinct_shares(sheet)
            assert n >= 300, f"{st['code']} ({label}): only {n} distinct daily shares in 365"
            total = sum(float(sheet[f"AE{r}"].value) for r in range(9, 9 + 365))
            assert abs(total - 1.0) < 1e-9, f"{st['code']}: borrowed shape totals {total!r}"

            # and it says WHERE it came from
            note = sheet["AE8"].comment.text
            comps = [c["code"] for c in STORES
                     if c["status"] == "Continuing" and c["region"] == st["region"]][:3]
            assert comps and all(c in note for c in comps), (note, comps)


def test_borrowed_shape_is_the_average_of_the_comparables():
    dates = [f"2027-01-{d:02d}" for d in range(1, 11)]
    stores = [
        {"code": "A", "status": "Continuing", "region": "East"},
        {"code": "B", "status": "Continuing", "region": "East"},
        {"code": "C", "status": "Continuing", "region": "East"},
        {"code": "D", "status": "Continuing", "region": "East"},   # a 4th: must NOT be used
        {"code": "W", "status": "Continuing", "region": "West"},
        {"code": "N", "status": "New store", "region": "East"},
    ]

    def norm(vals):
        t = sum(vals)
        return [v / t for v in vals]

    raw = {"A": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "B": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
           "C": [5, 1, 5, 1, 5, 1, 5, 1, 5, 1], "D": [1] * 4 + [50] * 6,
           "W": [3, 3, 3, 9, 9, 9, 1, 1, 1, 1]}
    supplied = {c: dict(zip(dates, norm(v))) for c, v in raw.items()}

    out, borrowed = borrow_day_shapes(stores, supplied, dates)
    assert set(borrowed) == {"N"}, borrowed
    assert borrowed["N"]["from"] == ["A", "B", "C"], borrowed   # first three, same region
    assert borrowed["N"]["whole"] is True and borrowed["N"]["days"] == len(dates)
    want = [sum(norm(raw[c])[i] for c in "ABC") / 3 for i in range(len(dates))]
    want = norm(want)
    got = [out["N"][d] for d in dates]
    assert all(abs(a - b) < 1e-12 for a, b in zip(got, want)), (got, want)
    assert abs(sum(got) - 1.0) < 1e-12

    # No same-region comparable with a usable shape -> widen to every region.
    only_west = {"W": supplied["W"]}
    _, b2 = borrow_day_shapes(stores, only_west, dates)
    assert b2["N"]["from"] == ["W"], b2

    # Nothing usable anywhere -> leave the store alone rather than invent a shape. The only
    # series on offer must be genuinely flat, which takes at least MIN_FLAT_RUN days.
    long_dates = [f"2027-02-{d:02d}" for d in range(1, 29)]
    flat_only = {"A": {d: 0.1 for d in long_dates}}
    out3, b3 = borrow_day_shapes(stores, flat_only, long_dates)
    assert b3 == {} and out3 == flat_only, b3


def test_stores_with_a_real_shape_of_their_own_are_left_alone():
    dates = [f"2027-01-{d:02d}" for d in range(1, 11)]
    shape = _varied_shape(dates, 3)
    stores = [
        {"code": "A", "status": "Continuing", "region": "East"},
        {"code": "N", "status": "New store", "region": "East"},      # new, but has its own
        {"code": "X", "status": "Continuing", "region": "East"},     # absent from the model
        {"code": "Z", "status": "Closed", "region": "East"},
    ]
    supplied = {"A": shape, "N": _varied_shape(dates, 9)}
    out, borrowed = borrow_day_shapes(stores, supplied, dates)
    assert borrowed == {}, f"nobody needed a borrowed shape: {borrowed}"
    assert out["N"] == supplied["N"], "a new store's own real shape was overwritten"
    assert "X" not in out, "a continuing store missing from the model is the weekday default"
    assert "Z" not in out, "a closed store has no sales to shape"

    # With no model day mix at all, every store is on the weekday curve by design.
    assert borrow_day_shapes(stores, None, dates) == ({}, {})
    assert borrow_day_shapes(stores, {}, dates) == ({}, {})


def test_a_flat_series_on_a_continuing_store_is_replaced_too():
    """A flat series prices every day identically whatever the store's status."""
    dates = [f"2027-02-{d:02d}" for d in range(1, 29)] + [f"2027-03-{d:02d}" for d in range(1, 13)]
    stores = [{"code": c, "status": "Continuing", "region": "East"} for c in "ABCD"]
    supplied = {"A": _varied_shape(dates, 1), "B": _varied_shape(dates, 2),
                "C": _varied_shape(dates, 3), "D": {d: 1 / len(dates) for d in dates}}
    _, borrowed = borrow_day_shapes(stores, supplied, dates)
    assert set(borrowed) == {"D"} and borrowed["D"]["from"] == ["A", "B", "C"], borrowed
    assert borrowed["D"]["whole"] is True


def _closed_dates():
    return {d["date"] for d in build_day_factors(YEAR, WEIGHTS, HOLIDAYS) if d["holiday_label"]}


def test_flat_stretches_ignore_closed_days_and_need_two_weeks():
    """Closed days are zeros. They must neither break a run nor extend one.

    The first version judged flatness by max-min over all 365 values, zeros included, so a
    store reading 0.2786% on every open day and 0% on six closed days looked wildly varied
    and sailed through. That is exactly what EURS and OFKS did in production while 70 tests
    passed, because the fixture had no closed days.
    """
    v = 0.002786
    year = [v if i not in (0, 100, 200, 250, 300, 360) else 0.0 for i in range(365)]
    runs = flat_stretches(year)
    assert len(runs) == 1 and len(runs[0]) == 359, [len(r) for r in runs]

    # a weekday rhythm is not flat, however regular
    weekly = [0.003 + 0.0005 * (i % 7) for i in range(365)]
    assert flat_stretches(weekly) == []

    # the threshold: 13 identical days is a coincidence, 14 is the model saying nothing
    assert MIN_FLAT_RUN == 14
    base = [0.001 * (1 + (i % 7)) for i in range(60)]
    for n, expect in ((13, 0), (14, 1)):
        series = list(base)
        series[10:10 + n] = [0.005] * n
        assert len(flat_stretches(series)) == expect, (n, flat_stretches(series))


def test_a_constant_with_closed_days_is_borrowed_not_passed_through():
    """EURS and OFKS as the live table gives them: one value on every open day, zero on the
    closed ones. Through the whole build_workbook path."""
    news = _new_stores()
    closed = _closed_dates()
    dates = [d["date"] for d in build_day_factors(YEAR, WEIGHTS, HOLIDAYS)]
    n_open = len(dates) - len(closed)
    own = {st["code"]: _varied_shape(dates, i) for i, st in enumerate(STORES)
           if st["status"] == "Continuing"}
    for st in news:
        own[st["code"]] = {d: (0.0 if d in closed else 1.0 / n_open) for d in dates}

    wb = build_workbook(year=YEAR, stores=STORES, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN, day_pct_by_store=own)
    for st in news:
        sheet = wb[tab_name(st)]
        assert sheet["B6"].value == "Borrowed", (st["code"], sheet["B6"].value)
        shares = [float(sheet[f"AE{r}"].value) for r in range(9, 9 + 365)]
        assert _distinct_shares(sheet) >= 300, st["code"]
        assert abs(sum(shares) - 1.0) < 1e-9
        for i, d in enumerate(dates):
            if d in closed:
                assert shares[i] == 0.0, f"{st['code']} {d}: a closed day was given a goal"


def test_a_partly_flat_store_is_reshaped_only_where_it_is_flat():
    """BANS and FETS as the live table gives them: continuing stores with real sales whose
    forecast repeats ONE value for a stretch (BANS: 2 Jan to May; FETS: 2-28 Jan) and is
    varied elsewhere. A whole-series test cannot see this -- the series as a whole is varied.

    Re-shaping must (a) touch only the flat run, (b) keep the run's total share of the year,
    so the model's level survives and only the shape within it changes, (c) leave closed
    days at zero, and (d) leave every other day, including an outlier spike, exactly as the
    model gave it.
    """
    dates = [d["date"] for d in build_day_factors(YEAR, WEIGHTS, HOLIDAYS)]
    closed = _closed_dates()
    base = _varied_shape(dates, 5)
    flat_len = 27
    open_idx = [i for i, d in enumerate(dates) if d not in closed]
    run_idx = open_idx[:flat_len]

    series = {d: (0.0 if d in closed else base[d]) for d in dates}
    level = 0.002627
    for i in run_idx:
        series[dates[i]] = level                       # the flat stretch
    spike = [dates[open_idx[flat_len + k]] for k in range(3)]
    for d, v in zip(spike, (0.024969, 0.016458, 0.015321)):
        series[d] = v                                  # FETS's end-of-January surge

    stores = [dict(st) for st in STORES]
    code = next(st["code"] for st in stores if st["status"] == "Continuing")
    own = {st["code"]: _varied_shape(dates, i) for i, st in enumerate(stores)
           if st["status"] == "Continuing" and st["code"] != code}
    own[code] = series

    out, borrowed = borrow_day_shapes(stores, own, dates)
    info = borrowed[code]
    assert info["whole"] is False and info["days"] == flat_len, info
    assert info["ranges"][0][0] == dates[run_idx[0]] and info["ranges"][0][1] == dates[run_idx[-1]]

    new = out[code]
    # (b) mass of the run is preserved
    assert abs(sum(series[dates[i]] for i in run_idx) - sum(new[dates[i]] for i in run_idx)) < 1e-12
    # the run is no longer one value
    assert len({round(new[dates[i]], 9) for i in run_idx}) >= flat_len - 3
    # (c) closed days stay closed, (d) everything outside the run is untouched
    for i, d in enumerate(dates):
        if d in closed:
            assert new[d] == 0.0, f"{d}: a closed day was given a goal"
        elif i not in run_idx:
            assert new[d] == series[d], f"{d}: a day outside the flat run was changed"
    assert [new[d] for d in spike] == [0.024969, 0.016458, 0.015321], "the spike must be left alone"

    # and the tab says exactly that
    wb = build_workbook(year=YEAR, stores=STORES, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN, day_pct_by_store=own)
    sheet = wb[tab_name(next(st for st in STORES if st["code"] == code))]
    assert sheet["B6"].value == "Part borrowed", sheet["B6"].value
    assert sheet["AE8"].value == "Day % of Year (part borrowed)"
    assert dates[run_idx[0]] in sheet["AE8"].comment.text


def _balance(sheet, n):
    """B7 and B10 resolved the way Excel will, plus the per-store F-E gaps."""
    start, end = 14, 14 + n - 1
    f = [float(sheet[f"F{r}"].value) for r in range(start, end + 1)]
    e = [_recommended(sheet, r) for r in range(start, end + 1)]
    b5 = float(sheet["B5"].value)
    return {"B7": sum(f) - b5, "B10": sum(f) - sum(e),
            "worst_store": max(abs(a - b) for a, b in zip(f, e))}


def test_the_workbook_opens_balanced_to_the_cent():
    """The reported "Not balanced" on Plan_Inputs by a couple of cents.

    Every store's COO number was rounded to the cent on its own, but the typed total in B5
    was rounded from the UNROUNDED sum, so sixty-five stores' fractions of a cent surfaced as
    a gap -- +$0.06 at the app's own defaults -- and the check, which tolerates under a cent,
    called it unbalanced. Recommended (a formula) and COO Adjusted (a rounded constant) also
    differed by up to half a cent a store: a variance nobody created.

    Both are asserted here at the inputs that exposed it, including non-round forecasts like
    the live table's.
    """
    # the app's own defaults: $150M split in proportion to base sales, 1% per store
    cases = {
        "app defaults": dict(),
        "non-round forecasts": dict(recommended_bases={
            st["code"]: 1_500_000.37 + 20_123.41 * i for i, st in enumerate(STORES)}),
        "an override": dict(store_overrides={STORES[2]["code"]: {"plan_base": 987_654.321}}),
    }
    for label, kw in cases.items():
        wb = build_workbook(year=YEAR, stores=STORES, weights=WEIGHTS, holidays=HOLIDAYS,
                            recommended_plan=PLAN, **kw)
        sheet = wb["Plan_Inputs"]
        bal = _balance(sheet, len(STORES))
        assert abs(bal["B7"]) < 0.005, f"{label}: B7 reads {bal['B7']:+.4f}, the sheet would say Not balanced"
        if label != "an override":
            assert abs(bal["B10"]) < 1e-6, f"{label}: B10 reads {bal['B10']:+.6f}, a variance nobody created"
            assert bal["worst_store"] < 1e-6, f"{label}: a store opens with Recommended != COO Adjusted"
        else:
            # only the overridden store may differ from its Recommended
            f = [float(sheet[f"F{14 + i}"].value) for i in range(len(STORES))]
            e = [_recommended(sheet, 14 + i) for i in range(len(STORES))]
            off = [STORES[i]["code"] for i in range(len(STORES)) if abs(f[i] - e[i]) > 1e-6]
            assert off == [STORES[2]["code"]], off


def test_cents_rounds_the_way_excel_does():
    """Half away from zero, on what Excel sees rather than the raw binary float. Python's own
    round() would give 2.67 for 2.675 and 0.12 for 0.125."""
    assert _cents(2.675) == 2.68
    assert _cents(1234.565) == 1234.57
    assert _cents(1234.5649999999998) == 1234.57      # last-bit noise on a half-cent
    assert _cents(0.125) == 0.13
    assert _cents(-2.675) == -2.68
    assert _cents(5.0) == 5.0 and _cents(0.0) == 0.0
    assert _cents(1234.5649) == 1234.56


def test_every_store_tab_shows_its_growth_rate():
    """Plan_Inputs column K and B9 are where a rate is typed; every store tab mirrors them so
    the number behind a store's Recommended is on the page that shows it."""
    wb = build_workbook(year=YEAR, stores=STORES, weights=WEIGHTS, holidays=HOLIDAYS,
                        recommended_plan=PLAN, growth_rate=0.02,
                        store_growth={STORES[3]["code"]: 0.07})
    pi = wb["Plan_Inputs"]
    for idx, st in enumerate(STORES):
        sheet = wb[tab_name(st)]
        row = 14 + idx
        assert sheet["A3"].value == "Store Growth %"
        assert sheet["B3"].value == f"='Plan_Inputs'!$K${row}", (st["code"], sheet["B3"].value)
        assert sheet["A4"].value == "Network Growth %"
        assert sheet["B4"].value == "='Plan_Inputs'!$B$9"
        assert sheet["A5"].value == "Combined Growth %"
        assert sheet["B5"].value == "=(1+B3)*(1+B4)-1"
        assert sheet["A6"].value == "Day Shape" and sheet["B6"].value == "Weekday curve"
        # the row it points at really is this store's row
        assert pi[f"A{row}"].value == st["code"], f"{st['code']} points at {pi[f'A{row}'].value}"
    assert abs(float(pi[f"K{14 + 3}"].value) - 0.07) < 1e-12

    # They are mirrors, not inputs: nothing here may be styled as a typed cell.
    sample = wb[tab_name(STORES[0])]
    for ref in ("B3", "B4", "B5"):
        assert sample[ref].font.color.rgb != _wb_input_font(wb), f"{ref} looks editable"


def _wb_input_font(wb):
    return wb["Plan_Inputs"]["B9"].font.color.rgb


def test_the_model_day_mix_chosen_in_the_app_reaches_the_file_reshaped():
    """Through the real app: turn the model day mix on, click Build Workbook, and confirm the
    new stores' tabs carry a varied, borrowed shape rather than the model's repeated value.
    The fix lives in the workbook generator, so it holds whether or not the page says so."""
    import io as _io

    import openpyxl
    from streamlit.testing.v1 import AppTest

    import workbook as _wb_mod

    news = _new_stores()
    dates = [d["date"] for d in build_day_factors(YEAR, WEIGHTS, HOLIDAYS)]
    model = {st["code"]: _varied_shape(dates, i) for i, st in enumerate(STORES)
             if st["status"] == "Continuing"}
    closed = {d["date"] for d in build_day_factors(YEAR, WEIGHTS, HOLIDAYS)
              if d["holiday_label"]}
    n_open = len(dates) - len(closed)
    for st in news:                  # what the live table gives a store with no history
        model[st["code"]] = {d: (0.0 if d in closed else 1.0 / n_open) for d in dates}

    captured = {}
    real = _wb_mod.workbook_bytes

    def spy(**kwargs):
        captured["bytes"] = real(**kwargs)
        return captured["bytes"]

    _wb_mod.workbook_bytes = spy
    try:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
        at.session_state["day_pct_forecast"] = model
        at.session_state["day_pct_status"] = ("success", "loaded")
        at.session_state["use_model_day_mix"] = True
        at.run()
        assert not at.exception, at.exception
        captions = " ".join(c.value for c in at.caption)
        assert "repeats one value" not in captions, (
            "the on-screen note about re-shaped stores was removed on request"
        )
        buttons = [b for b in at.button if "Build Workbook" in (b.label or "")]
        buttons[0].click().run()
        assert not at.exception, at.exception
    finally:
        _wb_mod.workbook_bytes = real

    wb = openpyxl.load_workbook(_io.BytesIO(captured["bytes"]))
    for st in news:
        sheet = wb[tab_name(st)]
        assert sheet["B6"].value == "Borrowed" and _distinct_shares(sheet) >= 300, st["code"]


import contextlib


@contextlib.contextmanager
def _warehouse(configured=True, rows=None, raises=False):
    """Stand in for the warehouse and nothing else.

    Only databricks_io.execute is replaced, so fetch_store_forecasts, parse_store_forecasts,
    the 360-day horizon guard and reconcile are the real ones. Yields the list of statements
    the app sent, so a test can count queries.
    """
    import databricks_io as dbx

    calls = []
    saved = {k: getattr(dbx, k) for k in
             ("execute", "is_configured", "auth_mode", "fetch_monthly_forecast_band")}

    def fake_execute(statement):
        calls.append(statement)
        if raises:
            raise RuntimeError("warehouse unavailable")
        return {"columns": ["store_code", "forecast_total", "first_date", "last_date", "n_days"],
                "rows": rows or [], "source": "live", "run_timestamp": "t"}

    dbx.execute = fake_execute
    dbx.is_configured = lambda: configured
    dbx.auth_mode = lambda: "oauth-m2m" if configured else "mock"
    dbx.fetch_monthly_forecast_band = lambda plan_year: {}
    try:
        yield calls
    finally:
        for k, v in saved.items():
            setattr(dbx, k, v)


def _open_app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()                                  # no click anywhere
    return at


def test_the_forecast_is_the_default_when_the_app_opens():
    """The COO should land on the forecast, not on a typed round number and a button to
    press. Nothing is clicked here."""
    rows = _full_year_rows([st["code"] for st in STORES], value=2_000_000.0)
    with _warehouse(configured=True, rows=rows) as calls:
        at = _open_app()
        assert not at.exception, at.exception
        ss = at.session_state
        assert len(ss["db_forecasts"]) == len(STORES), len(ss["db_forecasts"])
        assert abs(ss["recommended_plan"] - 2_000_000.0 * len(STORES)) < 0.01
        assert ss["plan_source"] == "forecast"
        level, msg = ss["run_status"]
        assert level == "success" and "automatically" in msg, ss["run_status"]
        assert f"Matched {len(STORES)} of {len(STORES)}" in msg
        assert len(calls) == 1, f"expected one query on open, got {len(calls)}"

        # ...and it is what the page shows: the plan starts from the forecast, not $150M
        assert any("$" in m.value and "150,000,000" not in m.value for m in at.success)


def test_the_forecast_loads_once_not_on_every_interaction():
    """Streamlit reruns the whole script on every widget touch. Reloading each time would be
    slow and would overwrite what the COO is in the middle of editing."""
    rows = _full_year_rows([st["code"] for st in STORES], value=1_000_000.0)
    with _warehouse(configured=True, rows=rows) as calls:
        at = _open_app()
        assert len(calls) == 1
        at.session_state["growth_rate"] = 0.02
        at.session_state["store_growth"] = {STORES[0]["code"]: 0.07}
        at.run()
        assert not at.exception, at.exception
        assert len(calls) == 1, f"a widget touch re-queried the warehouse ({len(calls)} queries)"
        assert at.session_state["growth_rate"] == 0.02, "the COO's edit was overwritten"
        assert at.session_state["store_growth"] == {STORES[0]["code"]: 0.07}
        assert len(at.session_state["db_forecasts"]) == len(STORES)


def test_with_no_warehouse_the_seeded_plan_stands():
    """Mock mode must stay exactly as it was: nothing queried, nothing claimed."""
    with _warehouse(configured=False) as calls:
        at = _open_app()
        assert not at.exception, at.exception
        assert calls == [], "the app queried a warehouse that is not configured"
        assert at.session_state["db_forecasts"] == {}
        assert at.session_state["recommended_plan"] == 150_000_000.0
        assert at.session_state["plan_source"] == "manual"
        assert at.session_state["run_status"] is None


def test_a_failed_load_on_open_leaves_a_usable_page():
    """A cold or unreachable warehouse must not take the whole page down on open."""
    with _warehouse(configured=True, raises=True) as calls:
        at = _open_app()
        assert not at.exception, at.exception
        level, msg = at.session_state["run_status"]
        assert level == "error" and msg.startswith("Forecast run failed"), at.session_state["run_status"]
        assert at.session_state["db_forecasts"] == {}
        assert at.session_state["recommended_plan"] == 150_000_000.0, "fell back to the seeded plan"
        assert any("Forecast run failed" in e.value for e in at.error), "the failure is not shown"


def test_the_button_and_the_automatic_load_share_one_implementation():
    """Two copies would drift, and what the COO sees on open would stop matching what the
    button gives them."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert src.count("dbx.fetch_store_forecasts(") == 1, "the forecast is fetched in more than one place"
    assert "load_forecast(YEAR)" in src and "load_forecast(YEAR, automatic=True)" in src


def test_blank_growth_cells_fall_back_to_the_default():
    """Reading the edited Stores table back. A blank cell must mean "the default", not 0%
    and not a crash -- 0% would silently strip the 1% from a store the COO only meant to
    tidy. Tested here because the browser harness cannot send a Delete keypress to the grid,
    so the clear gesture could not be driven live."""
    import app as _app

    rows = [
        {"Code": "A", "Growth (%)": None},          # cleared
        {"Code": "B", "Growth (%)": float("nan")},  # cleared (pandas flavour)
        {"Code": "C", "Growth (%)": 1.0},           # typed the default back
        {"Code": "D", "Growth (%)": 8.0},           # a real edit
        {"Code": "E", "Growth (%)": 0.0},           # an explicit 0% is a decision, not blank
        {"Code": "F", "Growth (%)": 1.0000000000001},  # widget round-trip noise
    ]
    got = _app.read_store_growth(rows)
    assert set(got) == {"D", "E"}, got
    assert abs(got["D"] - 0.08) < 1e-12
    assert got["E"] == 0.0, "an explicit 0% must be kept, not mistaken for blank"


def test_how_to_use_names_the_per_store_growth_column():
    s = _wb(STORES[:2])["How_To_Use"]
    text = " ".join(str(c.value) for row in s.iter_rows() for c in row if c.value)
    assert "column K" in text, "the growth lever must say where per-store growth lives"
    assert "starting at 1%" in text and "starting at 0%" in text, (
        "the defaults must be stated where the COO reads the levers"
    )


def test_the_monthly_chart_does_not_draw_one_shape_twice():
    """The page used to plot "Recommended Plan" and "COO Adjusted Plan" as two monthly
    lines. Both came from build_store_plan(total, days) with the same `days`, so they were
    exactly proportional -- the same curve at two scales, a constant ratio apart -- and
    neither carried any per-store or per-day information, so nothing the COO changed
    store-side could move them. This proves the redundancy rather than trusting the memory
    of it, and pins the chart to one series.
    """
    days = build_day_factors(YEAR, WEIGHTS, HOLIDAYS)
    a = build_store_plan(100_000_000, days)["monthly"]
    b = build_store_plan(157_500_000, days)["monthly"]
    ratios = [y / x for x, y in zip(a, b)]
    assert max(ratios) - min(ratios) < 1e-12, (
        "the two series are NOT proportional any more -- if the monthly split has become "
        "store-aware, a two-series comparison chart is worth reconsidering"
    )

    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"Series": ["Recommended Plan"] * 12 + ["COO Adjusted Plan"] * 12' not in src, (
        "the proportional two-line chart is back"
    )
    # Exactly one monthly series is extracted. Counting `build_store_plan(` would count
    # the comment that explains why -- prose is not a call site.
    n = src.count('["monthly"]')
    assert n == 1, f"expected one monthly series, found {n} monthly extractions in app.py"

    # What replaced it: per-store deltas, which DO carry store-level information, coloured
    # by sign from a fixed two-pole domain. A domain fixed in code is what keeps a filtered
    # chart from repainting its survivors.
    assert "COO Adjusted minus Recommended" in src, "the per-store variance chart is missing"
    assert 'domain=["Above recommended", "Below recommended"]' in src, (
        "the diverging scale must pin its domain, not infer it from the rows present"
    )


def test_chart_hues_are_validated_and_theme_independent():
    """Every mark colour has to clear 3:1 on BOTH surfaces, because the app pins no theme
    base -- the reader picks. A hue chosen against white only is unreadable for half the
    audience, which is how white-on-amber at 1.21:1 shipped once already."""
    import app as _app

    for surface in ("#ffffff", "#0e1117"):
        for role in ("plan", "above", "below"):
            ratio = _contrast(_app.PAL[role], surface)
            assert ratio >= 3.0, f"{role} is {ratio:.2f}:1 on {surface}, below the 3:1 floor"

    # Diverging poles must read as opposite -- warm against cool. Two cool hues give a
    # midpoint that does not read as "nothing".
    assert _app.PAL["above"] != _app.PAL["below"], "the diverging poles are the same colour"
    r_above = int(_app.PAL["above"][1:3], 16)
    r_below = int(_app.PAL["below"][1:3], 16)
    assert r_below > r_above, "the 'below' pole should be the warm one"


def test_exec_summary_shows_both_numbers_and_the_variance():
    wb = _wb(STORES[:3])
    s = wb[EXEC_CAL_SHEET_NAME]
    assert s["A5"].value == "Recommended Planned Sales ($)"
    assert s["B5"].value == "='Plan_Inputs'!$B$8", "must mirror the grown total, not B4"
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
    # The four levers the COO actually has authority over.
    assert "1. The total" in text
    assert "2. Growth rate factor" in text
    assert "3. Any individual store" in text
    assert "4. The day inputs for the store" in text
    assert "Plan_Inputs cell B9" in text, "the growth lever must name its cell"


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


def test_both_themes_are_defined_and_clear_wcag_aa():
    """Day and night are both authored, not one authored and one inherited from stock.

    [theme] is the light palette and [theme.dark] overrides it; neither pins `base`, so the
    reader's Appearance choice still decides which applies. Every text pair must clear AA
    (4.5:1) in BOTH, because a palette is only half-checked if it is only checked on white.
    """
    import tomllib

    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    theme = cfg.get("theme", {})
    dark = theme.get("dark", {})
    assert theme, "no [theme] section: the light palette would fall back to stock"
    assert dark, "no [theme.dark] section: dark mode would fall back to stock"
    assert "base" not in theme, (
        "pinning theme.base overrides the reader's Appearance choice, so half the audience "
        "gets the theme they did not pick"
    )

    # [theme.dark] inherits anything it does not override, which is how a dark palette ends
    # up with a light background nobody set.
    light = {k: v for k, v in theme.items() if isinstance(v, str)}
    modes = {"light": light, "dark": {**light, **dark}}

    for mode, pal in modes.items():
        for key in ("primaryColor", "backgroundColor", "secondaryBackgroundColor",
                    "textColor", "linkColor"):
            assert key in pal, f"{mode} theme is missing {key}"

        for ground in ("backgroundColor", "secondaryBackgroundColor"):
            ratio = _contrast(pal["textColor"], pal[ground])
            assert ratio >= 4.5, f"{mode}: text on {ground} is {ratio:.2f}:1, below AA"

        ratio = _contrast(pal["linkColor"], pal["backgroundColor"])
        assert ratio >= 4.5, f"{mode}: links are {ratio:.2f}:1, below AA"

        # Primary buttons carry a white label. A blue light enough to pop against a dark
        # page drops that label below AA -- the trade that makes a button unreadable.
        ratio = _contrast("#ffffff", pal["primaryColor"])
        assert ratio >= 4.5, f"{mode}: white on primaryColor is {ratio:.2f}:1, below AA"

        # 3:1 is the WCAG floor for a UI component against its surroundings (1.4.11).
        ratio = _contrast(pal["primaryColor"], pal["backgroundColor"])
        assert ratio >= 3.0, f"{mode}: buttons are {ratio:.2f}:1 against the page"

    # [theme.dark] is ignored before Streamlit 1.46 -- silently, leaving stock dark.
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    floor = re.search(r"streamlit>=(\d+)\.(\d+)", req)
    assert floor, "requirements.txt must pin a streamlit floor"
    major, minor = int(floor.group(1)), int(floor.group(2))
    assert (major, minor) >= (1, 46), (
        f"streamlit>={major}.{minor} predates [theme.dark]; dark mode would be stock"
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
