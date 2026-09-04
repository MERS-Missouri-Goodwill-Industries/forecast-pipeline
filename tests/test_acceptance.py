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
        # Columns C (old baseline "Recommended Sales"), F (old "Holiday"), and H (old
        # "Recommended Month Total") no longer carry a header or any data -- COO Adjusted
        # Plan is the only money column on the sheet.
        assert [s.cell(8, c).value for c in range(1, 10)] == [
            "Date", "Day of Week", None, "COO Adjusted Plan",
            "Day % of Annual", None, "Month",
            None, "COO Month Total"]
        assert s["C1"].value is None and s["C2"].value is None, "no POS column"
        assert s["D2"].value is None, "no baseline 'Recommended' row"
        assert s["D3"].value == "COO Adjusted Plan"
        assert s["Q2"].value is None, "no baseline annual total"
        assert s["Q3"].value == f"=D{total_row}"
        assert s["Q5"].value is None, "no Diff label"
        assert s["Q6"].value == "=Q3-$S$2"
        assert s["S1"].value == "COO Adjusted Planned Sales"
        assert s["V1"].value == "Closure Start"
        assert f"$E${8 + i}" in s["S2"].value
        assert s["A6"].value is None and s["B6"].value is None, "no Forecasted Plan Base"

        for m, (a, b) in enumerate(ranges):
            col = "EFGHIJKLMNOP"[m]
            assert s[f"{col}2"].value is None, "no baseline monthly row"
            # The formula the whole delete-days workflow depends on:
            assert s[f"{col}3"].value == f"=SUM(D{a}:D{b})", "row 3 must SUM the daily COO column"
            assert s[f"H{b}"].value is None
            assert s[f"I{b}"].value == f"=SUM(D{a}:D{b})"

        for r in (9, 150, total_row - 1):
            assert s[f"C{r}"].value is None
            assert s[f"F{r}"].value is None, "no Holiday column without closures"
            assert s[f"D{r}"].value == f"=$S$2 * E{r}"
        assert s[f"C{total_row}"].value is None
        assert s[f"D{total_row}"].value == f"=SUM(D9:D{total_row - 1})"


def test_formulas_not_values():
    """Every money cell must be a live formula -- the workbook is edited after export."""
    wb = _wb(STORES[:2])
    s = wb[tab_name(STORES[0])]
    for cell in ("D9", "E9", "Q3", "Q6", "I39"):
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
    for col in ("D", "E", "I", "Q", "S"):
        assert (s.column_dimensions[col].width or 0) >= 12, f"column {col} too narrow"


def test_databricks_parsing_and_reconciliation():
    import databricks_io as io

    good = {"columns": ["store_code", "forecast_sales"],
            "rows": [{"store_code": s["code"], "forecast_sales": 1000.0} for s in STORES]}
    parsed, warn = io.parse_store_forecasts(good)
    assert warn is None and len(parsed) == 65
    rec = io.reconcile(parsed, [s["code"] for s in STORES])
    assert rec["matched"] == 65 and rec["expected"] == 65

    bad_cols = {"columns": ["a", "b"], "rows": [{"a": 1, "b": 2}]}
    _, warn = io.parse_store_forecasts(bad_cols)
    assert warn, "unmatched columns must produce a warning, not silence"

    wrong_codes = {"columns": ["store_code", "forecast_sales"],
                   "rows": [{"store_code": "ZZZZ", "forecast_sales": 1.0}]}
    parsed, _ = io.parse_store_forecasts(wrong_codes)
    rec = io.reconcile(parsed, [s["code"] for s in STORES])
    assert rec["matched"] == 0 and rec["missing_from_databricks"], (
        "a code mismatch must be detectable, not silent"
    )

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
