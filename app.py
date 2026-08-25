"""Sales Planning — Databricks App entry point.

Launched by app.yaml:
    streamlit run app.py --server.port $DATABRICKS_APP_PORT --server.address 0.0.0.0
"""

from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

import databricks_io as dbx
from forecast_engine import (
    WEEKDAYS, build_day_factors, build_store_plan, compute_forecasted_bases,
    default_holidays, load_seed, normalize_weights, weekday_counts, weekday_mix,
)
from workbook import workbook_bytes

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Planning starts at FY2027. One more year unlocks every January 1st.
FIRST_YEAR = 2027
AVAILABLE_YEARS = list(range(FIRST_YEAR, max(FIRST_YEAR, dt.date.today().year) + 1))

OUTLET_CODES = {"BANS", "BROS", "OUTS"}

SEED = load_seed()
STORES = SEED["stores"]
PRESETS = SEED["dow_presets"]
PRESET_LABELS = {
    "recommended": "Recommended (from Actuals)",
    "excel_plan": "2026 Weights (COO Workbook)",
}

st.set_page_config(page_title="Sales Planning", layout="wide")


def _init():
    ss = st.session_state
    ss.setdefault("year", AVAILABLE_YEARS[-1])
    ss.setdefault("preset", "recommended")
    if "weights" not in ss:
        ss.weights = normalize_weights(PRESETS.get(ss.preset, PRESETS["recommended"]))
    ss.setdefault("weights_raw", {d: round(ss.weights.get(d, 0.0) * 100, 2) / 100 for d in WEEKDAYS})
    ss.setdefault("recommended_plan", 150_000_000.0)
    ss.setdefault("overrides", {})
    ss.setdefault("db_forecasts", {})
    ss.setdefault("run_status", None)


_init()
ss = st.session_state
if ss.year not in AVAILABLE_YEARS:
    ss.year = AVAILABLE_YEARS[-1]

# --- header -----------------------------------------------------------------------------
# Resolve the Planning Year selector before anything reads YEAR, so the title and every
# downstream calculation are guaranteed to reflect the selection in this same run.
left, mid, right = st.columns([2.6, 1, 1.4])
with mid:
    year = st.selectbox("Planning Year", AVAILABLE_YEARS,
                        index=AVAILABLE_YEARS.index(ss.year), key="year_select")
ss.year = year
YEAR = ss.year
with left:
    st.title(f"MERS Goodwill — FY{YEAR} Sales Planning")
    st.caption("Recommendation workbook generator. Every exported cell is a live formula.")
with right:
    mode = dbx.auth_mode()
    if mode != "mock":
        st.success("Databricks Live")
    else:
        st.warning("Local / Mock Data")
    st.caption(f"auth: {mode}")

    if st.button("Run Forecast", type="primary", use_container_width=True):
        try:
            result = dbx.fetch_store_forecasts()
            parsed, warning = dbx.parse_store_forecasts(result)
            if result.get("source") == "mock":
                ss.run_status = ("warning", "Databricks not connected — no live rows returned.")
            elif warning:
                ss.run_status = ("error", warning)
            else:
                rec = dbx.reconcile(parsed, [s["code"] for s in STORES])
                ss.db_forecasts = {k: v for k, v in parsed.items()
                                   if k in {s["code"] for s in STORES}}
                level = "success" if rec["matched"] == rec["expected"] else "error"
                msg = f"Matched {rec['matched']} of {rec['expected']} stores."
                if rec["matched"] < rec["expected"]:
                    msg += (f" Unmatched codes from Databricks: "
                            f"{rec['unmatched_from_databricks'] or 'none'}. "
                            f"Stores with no forecast: {rec['missing_from_databricks']}. "
                            "Those stores fall back to a proportional share.")
                ss.run_status = (level, msg)
        except Exception as exc:  # noqa: BLE001
            ss.run_status = ("error", f"Forecast run failed: {exc}")

if ss.run_status:
    level, msg = ss.run_status
    getattr(st, level)(msg)

# --- derived ----------------------------------------------------------------------------
holidays = default_holidays(YEAR)
days = build_day_factors(YEAR, ss.weights, holidays)
algorithmic = compute_forecasted_bases(STORES, ss.recommended_plan)
forecasted = {**algorithmic, **ss.db_forecasts}
effective = {c: ss.overrides.get(c, forecasted.get(c, 0.0)) for c in forecasted}
coo_total = sum(effective.values())

# --- export ------------------------------------------------------------------------------
st.markdown(
    """
    <style>
    div.st-key-export_build button {
        box-shadow: 0 6px 14px rgba(0, 0, 0, 0.35);
        font-weight: 600;
        font-size: 1.05rem;
        padding: 0.6rem 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.subheader("Export")
with st.container(key="export_build"):
    if st.button("📥  Build Workbook", type="primary", use_container_width=True):
        with st.spinner("Generating 73 sheets…"):
            data = workbook_bytes(
                year=YEAR, stores=STORES, weights=ss.weights, holidays=holidays,
                recommended_plan=ss.recommended_plan,
                store_overrides={c: {"plan_base": v} for c, v in ss.overrides.items()},
            )
        file_name = f"POC_Prototype_{YEAR}_Planned_Sales_Workbook_{YEAR}.xlsx"
        st.download_button(
            f"Download {file_name}",
            data=data,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# --- controls ---------------------------------------------------------------------------
c1, c2 = st.columns([1, 2])

with c1:
    st.subheader("Day-of-Week Weighting")
    preset = st.selectbox("Preset", list(PRESET_LABELS) + ["custom"],
                          format_func=lambda k: PRESET_LABELS.get(k, "Custom"),
                          index=list(PRESET_LABELS).index(ss.preset) if ss.preset in PRESET_LABELS
                          else len(PRESET_LABELS))
    if preset != ss.preset:
        ss.preset = preset
        if preset in PRESETS:
            ss.weights = normalize_weights(PRESETS[preset])
            ss.weights_raw = {d: round(ss.weights[d] * 100, 2) / 100 for d in WEEKDAYS}
            for d in WEEKDAYS:
                ss[f"w_{d}"] = round(ss.weights[d] * 100, 2)
        st.rerun()

    new_weights = {}
    for d in WEEKDAYS:
        new_weights[d] = st.number_input(
            d, min_value=0.0, max_value=100.0,
            value=round(ss.weights.get(d, 0.0) * 100, 2), step=0.01, format="%.2f",
            key=f"w_{d}",
        ) / 100
    if new_weights != ss.weights_raw:
        ss.weights_raw = new_weights
        ss.weights = normalize_weights(new_weights)
        ss.preset = "custom"
        st.rerun()

    total_raw = sum(ss.weights_raw.values())
    st.write(f"Total: {total_raw * 100:.2f}%")

    st.subheader("Recommended Plan")
    plan = st.number_input("Network plan ($)", min_value=0.0,
                           value=float(ss.recommended_plan), step=1_000_000.0, format="%.0f")
    if plan != ss.recommended_plan:
        ss.recommended_plan = plan
        st.rerun()

    variance = coo_total - ss.recommended_plan
    st.metric("COO Adjusted Plan", f"${coo_total:,.0f}",
              delta=None if abs(variance) < 0.5 else f"${variance:,.0f}")

with c2:
    st.subheader("Weekday Mix — Normalized % of Week")
    st.dataframe(
        [{"Day": m["day"], "Wkdy #": m["weekday_number"],
          "Day % of Annual": f"{m['day_pct_of_annual'] * 100:.4f}%",
          "% of Week": f"{m['pct_of_week'] * 100:.1f}%"} for m in weekday_mix(days)],
        hide_index=True, use_container_width=True,
    )
    mix = weekday_mix(days)
    wknd = sum(m["pct_of_week"] for m in mix if m["day"] in ("Saturday", "Sunday"))
    st.caption(f"Weekend (Sat+Sun) {wknd * 100:.1f}%  ·  Weekday (Mon–Fri) {(1 - wknd) * 100:.1f}%"
               "  ·  The same seven values for every store, all year.")

    st.subheader("Selling Days by Weekday")
    context_years = [YEAR - 2, YEAR - 1, YEAR, YEAR + 1]
    counts = pd.DataFrame({str(y): weekday_counts(y) for y in context_years}, index=WEEKDAYS)
    counts.loc["Total Days"] = counts.sum()
    styled = (
        counts.style
        .map(lambda v: "color:#c0392b; font-weight:700" if v == 53 else "")
        .set_properties(subset=[str(YEAR)], **{"background-color": "rgba(31,119,180,0.12)"})
        .format("{:.0f}")
    )
    st.dataframe(styled, use_container_width=True)
    st.caption(f"FY{YEAR} highlighted. A weekday in red occurs 53 times that year instead of "
               "the usual 52 — the extra selling day to weight for.")

    st.subheader("Planned Sales Comparison")
    recommended_monthly = build_store_plan(ss.recommended_plan, days)["monthly"]
    coo_monthly = build_store_plan(coo_total, days)["monthly"]
    chart_df = pd.DataFrame({
        "Month": MONTH_ABBR * 2,
        "Series": ["Recommended Plan"] * 12 + ["COO Adjusted Plan"] * 12,
        "Planned Sales": recommended_monthly + coo_monthly,
    })
    chart = alt.Chart(chart_df).mark_line(point=True).encode(
        x=alt.X("Month", sort=MONTH_ABBR, title=None),
        y=alt.Y("Planned Sales", title="Planned Sales ($)"),
        color=alt.Color("Series", title=None),
        tooltip=["Month", "Series", "Planned Sales"],
    )
    st.altair_chart(chart, use_container_width=True)

# --- per-store --------------------------------------------------------------------------
st.subheader("Stores")
st.caption("Set a COO Adjusted Plan Base to override a store. Leave blank to accept the forecast.")
edited = st.data_editor(
    [{"Code": s["code"], "Store": s["name"], "Region": s["region"],
      "Location Type": "Outlet" if s["code"] in OUTLET_CODES else "Store",
      "Status": s["status"],
      "Forecasted": round(forecasted.get(s["code"], 0.0), 2),
      "COO Adjusted": ss.overrides.get(s["code"]),
      "Variance": round(effective.get(s["code"], 0.0) - forecasted.get(s["code"], 0.0), 2)}
     for s in STORES],
    hide_index=True, use_container_width=True, height=380,
    disabled=["Code", "Store", "Region", "Location Type", "Status", "Forecasted", "Variance"],
    column_config={
        "Forecasted": st.column_config.NumberColumn(format="$%.0f"),
        "COO Adjusted": st.column_config.NumberColumn(format="$%.0f"),
        "Variance": st.column_config.NumberColumn(format="$%.0f"),
    },
)
new_over = {r["Code"]: float(r["COO Adjusted"]) for r in edited if pd.notna(r["COO Adjusted"])}
if new_over != ss.overrides:
    ss.overrides = new_over
    st.rerun()