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
    default_holidays, load_seed, normalize_weights, rounded_weights, weekday_counts,
    weekday_mix,
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
    # The COO's own 2026 workbook weights are the default starting point -- it is the curve
    # he already plans against, so the workbook opens on familiar ground.
    ss.setdefault("preset", "excel_plan")
    if "weights" not in ss:
        ss.weights = normalize_weights(PRESETS.get(ss.preset, PRESETS["recommended"]))
    # 4 places of a fraction == 2 decimals of a percent, the precision shown in the inputs.
    ss.setdefault("weights_raw", rounded_weights(ss.weights, places=4))
    ss.setdefault("recommended_plan", 150_000_000.0)
    ss.setdefault("overrides", {})
    ss.setdefault("db_forecasts", {})
    ss.setdefault("run_status", None)
    ss.setdefault("day_pct_status", None)
    ss.setdefault("day_pct_forecast", {})
    ss.setdefault("forecast_band", {})


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
            parsed, warning = dbx.parse_store_forecasts(result, plan_year=YEAR)
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
            # The prediction-interval band for the chart is independent of whether the
            # annual totals passed the horizon guard -- it is useful either way.
            try:
                ss.forecast_band = dbx.fetch_monthly_forecast_band()
            except Exception:  # noqa: BLE001
                ss.forecast_band = {}
        except Exception as exc:  # noqa: BLE001
            ss.run_status = ("error", f"Forecast run failed: {exc}")

    if st.button("Use Forecasted Day Mix", use_container_width=True,
                 help="Replace the weekday-weight curve with the model's own day-of-year "
                      "percentages, if the forecast pipeline is publishing them yet."):
        try:
            res = dbx.fetch_day_pct_forecast(plan_year=YEAR)
            level = {"ok": "success", "mock": "warning"}.get(res["status"], "error")
            ss.day_pct_forecast = res["day_pct"]
            ss.day_pct_status = (level, res["message"])
        except Exception as exc:  # noqa: BLE001
            ss.day_pct_forecast = {}
            ss.day_pct_status = ("error", f"Could not read the model day mix: {exc}")

if ss.run_status:
    level, msg = ss.run_status
    getattr(st, level)(msg)

if ss.day_pct_status:
    level, msg = ss.day_pct_status
    getattr(st, level)(msg)
    if ss.day_pct_forecast:
        st.info(
            f"Model day percentages are loaded for {len(ss.day_pct_forecast)} stores but are "
            "not driving the workbook yet. They vary by store, while every store currently "
            "shares one flat weekday curve — switching means each store tab carries its own "
            "day shares. Say the word and that change goes in."
        )

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
            # Pass the Databricks forecasts through, or the workbook silently rebuilds the
            # proportional split and disagrees with the figures shown on this screen.
            data = workbook_bytes(
                year=YEAR, stores=STORES, weights=ss.weights, holidays=holidays,
                recommended_plan=ss.recommended_plan,
                recommended_bases=dict(ss.db_forecasts) or None,
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
            ss.weights_raw = rounded_weights(ss.weights, places=4)
            for d in WEEKDAYS:
                ss[f"w_{d}"] = round(ss.weights_raw[d] * 100, 2)
        st.rerun()

    new_weights = {}
    for d in WEEKDAYS:
        # Seed from weights_raw, which already carries the residual in its last weekday.
        # Re-deriving from ss.weights here would round each day independently again and put
        # the drift straight back, overwriting the corrected values on the very next run.
        new_weights[d] = st.number_input(
            d, min_value=0.0, max_value=100.0,
            value=round(ss.weights_raw.get(d, 0.0) * 100, 2), step=0.01, format="%.2f",
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

    st.subheader("COO Adjusted Plan")
    st.caption("This is the total sales goal that will be in the downloaded workbook.")
    st.success(f"${coo_total:,.0f}")

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
        .map(lambda v: "background-color:#fde9a9; font-weight:700" if v == 53 else "")
        .set_properties(subset=[str(YEAR)], **{"background-color": "rgba(31,119,180,0.12)"})
        .format("{:.0f}")
    )
    st.dataframe(styled, use_container_width=True)
    st.caption(f"FY{YEAR} highlighted. A weekday shaded amber occurs 53 times that year instead of "
               "the usual 52 — the extra selling day to weight for.")

    st.subheader("Planned Sales Comparison")
    recommended_monthly = build_store_plan(ss.recommended_plan, days)["monthly"]
    coo_monthly = build_store_plan(coo_total, days)["monthly"]
    chart_df = pd.DataFrame({
        "Month": MONTH_ABBR * 2,
        "Series": ["Recommended Plan"] * 12 + ["COO Adjusted Plan"] * 12,
        "Planned Sales": recommended_monthly + coo_monthly,
    })
    lines = alt.Chart(chart_df).mark_line(point=True).encode(
        x=alt.X("Month", sort=MONTH_ABBR, title=None),
        y=alt.Y("Planned Sales", title="Planned Sales ($)"),
        color=alt.Color("Series", title=None),
        tooltip=["Month", "Series", "Planned Sales"],
    )

    # The model's prediction interval, drawn behind the plan lines for the months it
    # actually covers. Where the band stops is where the forecast stops -- that gap is the
    # clearest statement of the horizon limit available.
    band_months = ss.forecast_band.get("months") if ss.forecast_band else None
    if band_months:
        band_df = pd.DataFrame([
            {"Month": m["month"], "lower": m["lower"], "upper": m["upper"],
             "forecast": m["forecast"]}
            for m in band_months
        ])
        band = alt.Chart(band_df).mark_area(opacity=0.18, color="#8c8c8c").encode(
            x=alt.X("Month", sort=MONTH_ABBR, title=None),
            y=alt.Y("lower", title="Planned Sales ($)"),
            y2=alt.Y2("upper"),
            tooltip=["Month", "forecast", "lower", "upper"],
        )
        mid = alt.Chart(band_df).mark_line(strokeDash=[4, 3], color="#8c8c8c").encode(
            x=alt.X("Month", sort=MONTH_ABBR),
            y=alt.Y("forecast"),
        )
        st.altair_chart(band + mid + lines, use_container_width=True)
        covered = {m["month"] for m in band_months}
        st.caption(
            f"Grey band = model forecast spread (forecast_lower to forecast_upper), "
            f"covering {len(covered)} of 12 months: {', '.join(m['month'] for m in band_months)}. "
            f"{ss.forecast_band.get('message', '')}"
        )
    else:
        st.altair_chart(lines, use_container_width=True)
        st.caption("Run Forecast to overlay the model's forecast spread on this chart.")

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
