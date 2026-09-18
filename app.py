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

# --- theme ------------------------------------------------------------------------------
# Day and night come from Streamlit's own menu (⋮ → Settings → Appearance); .streamlit/
# config.toml deliberately pins no base so that choice is the reader's.
#
# The colours below do NOT branch on the active theme. st.context.theme reports the
# *browser's* prefers-color-scheme, which is not always what Streamlit paints -- measured
# here reporting "dark" while the page rendered light. Styling against a signal that can
# disagree with the page is how you get an unreadable cell in one theme and never see it.
#
# So every highlight sets background AND foreground together and is self-contained: the
# cell paints its own two colours, reads the same on a white or a near-black page, and
# clears WCAG AA (4.5:1) either way. A fill without a foreground inherits the theme's text
# colour -- that is exactly how amber ended up carrying white text at 1.21:1.
# Chart hues are validated, not chosen by eye. scripts/validate_palette.js from the
# dataviz skill, run against BOTH of this app's surfaces (#ffffff light, #0e1117 dark):
# blue #2a78d6 with red #e34948 passes every check in both modes -- lightness band,
# chroma floor, CVD separation (worst adjacent dE 21.6 protan), normal-vision floor
# (32.3) and 3:1 contrast -- so one fixed pair serves day and night, no theme branching.
#
# "band" is deliberately the de-emphasis grey and fails the chroma floor on purpose: it
# is context, not a categorical series, and it still separates from the plan blue at
# dE 15.3 under CVD.
PAL = {
    "flag_bg": "#fde9a9", "flag_fg": "#3f2d00",   # 10.97:1, measured
    "band": "#8c8c8c", "band_opacity": 0.22,      # mid grey: visible on either ground
    "plan": "#2a78d6",                            # the plan line -- one series, one hue
    "above": "#2a78d6", "below": "#e34948",        # diverging by sign, warm vs cool poles
}


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
    # The model forecasts no growth -- it cannot tell a promotion from a real expansion, so
    # it reads all growth as one-off seasonality. This is where the year's stretch is stated.
    ss.setdefault("growth_rate", 0.05)
    ss.setdefault("overrides", {})
    ss.setdefault("db_forecasts", {})
    ss.setdefault("run_status", None)
    ss.setdefault("day_pct_status", None)
    ss.setdefault("day_pct_forecast", {})
    ss.setdefault("forecast_band", {})
    ss.setdefault("use_model_day_mix", False)
    ss.setdefault("plan_source", "manual")


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
            result = dbx.fetch_store_forecasts(plan_year=YEAR)
            parsed, warning = dbx.parse_store_forecasts(result, plan_year=YEAR)
            if result.get("source") == "mock":
                ss.run_status = ("warning", "Databricks not connected — no live rows returned.")
            elif warning:
                ss.run_status = ("error", warning)
            else:
                rec = dbx.reconcile(parsed, [s["code"] for s in STORES])
                ss.db_forecasts = {k: v for k, v in parsed.items()
                                   if k in {s["code"] for s in STORES}}
                # The forecast IS the recommendation. Leaving the network plan on a typed
                # round number would keep every store that the forecast does not cover on a
                # share of a figure nobody stands behind.
                if ss.db_forecasts:
                    ss.recommended_plan = float(sum(ss.db_forecasts.values()))
                    ss.plan_source = "forecast"
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
                ss.forecast_band = dbx.fetch_monthly_forecast_band(plan_year=YEAR)
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
        use_model = st.toggle(
            f"Use the model's day mix for {len(ss.day_pct_forecast)} stores",
            value=ss.use_model_day_mix,
            help="Off: every store shares the one weekday curve set below, and an open "
                 "Friday in January is worth the same as one in July. On: each store tab "
                 "carries its own day shares from the forecast, so real per-store "
                 "seasonality comes through. The weekday weights stay your control either "
                 "way — turning this off returns to them.",
        )
        if use_model != ss.use_model_day_mix:
            ss.use_model_day_mix = use_model
            st.rerun()
        if ss.use_model_day_mix:
            st.caption("Store tabs carry their own day shares (column AE). The weekday "
                       "weights below no longer drive the daily split while this is on.")
        else:
            st.caption("Weekday weights are driving the daily split. The model day mix is "
                       "loaded and ready if you want it.")

# --- derived ----------------------------------------------------------------------------
holidays = default_holidays(YEAR)
days = build_day_factors(YEAR, ss.weights, holidays)
algorithmic = compute_forecasted_bases(STORES, ss.recommended_plan)
forecasted = {**algorithmic, **ss.db_forecasts}
# Same rule as workbook.py: an explicit override is the COO's own number and stands as
# typed; every other store carries the forecast grown by the rate. If this screen applied
# growth differently from the workbook, the download would disagree with the page that
# produced it -- store by store, with nothing flagging it.
recommended_of = {c: forecasted.get(c, 0.0) * (1.0 + ss.growth_rate) for c in forecasted}
effective = {c: ss.overrides[c] if c in ss.overrides else recommended_of[c]
             for c in forecasted}
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
                growth_rate=ss.growth_rate,
                recommended_bases=dict(ss.db_forecasts) or None,
                day_pct_by_store=(dict(ss.day_pct_forecast)
                                  if ss.use_model_day_mix and ss.day_pct_forecast else None),
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
    # Compare with a tolerance, not ==. The widget round-trip (x -> x*100 -> /100) comes
    # back a single ULP off for some values -- 0.1289 returns as 0.12890000000000001 -- and
    # exact equality read that as the user editing a weight, flipping the preset to "Custom"
    # on first render before anyone had touched anything.
    edited = any(abs(new_weights[d] - ss.weights_raw.get(d, 0.0)) > 5e-7 for d in WEEKDAYS)
    if edited:
        ss.weights_raw = new_weights
        ss.weights = normalize_weights(new_weights)
        ss.preset = "custom"
        st.rerun()

    total_raw = sum(ss.weights_raw.values())
    if abs(total_raw - 1.0) < 5e-5:
        st.success(f"Total: {total_raw * 100:.2f}%")
    else:
        st.warning(f"Total: {total_raw * 100:.2f}% — the seven weights should add to 100%.")

    st.subheader("Recommended Plan")
    plan = st.number_input("Network plan ($)", min_value=0.0,
                           value=float(ss.recommended_plan), step=1_000_000.0, format="%.0f")
    if plan != ss.recommended_plan:
        ss.recommended_plan = plan
        ss.plan_source = "manual"
        st.rerun()
    if ss.plan_source == "forecast":
        st.caption(f"From the Databricks forecast, {len(ss.db_forecasts)} stores. "
                   "Type over it to plan against a different number.")
    else:
        st.caption("Typed in. Run Forecast to set this from the model instead.")

    growth_pct = st.number_input(
        "Growth rate above forecast (%)", min_value=-50.0, max_value=100.0,
        value=float(ss.growth_rate * 100), step=0.5, format="%.2f",
        help=("The forecast assumes no growth. This is the stretch on top of it, and it "
              "lands in the workbook as Plan_Inputs B9 where it can be changed again."),
    )
    if abs(growth_pct / 100.0 - ss.growth_rate) > 1e-9:
        ss.growth_rate = growth_pct / 100.0
        st.rerun()
    # Escaped dollar signs: Streamlit reads a $...$ pair as LaTeX, so three of them in one
    # caption swallowed the middle number and rendered "150,000,000+5.00157,500,000".
    _fc_total = sum(forecasted.values())
    st.caption(f"Forecast \\${_fc_total:,.0f} "
               f"+ {ss.growth_rate * 100:.2f}% = **\\${_fc_total * (1 + ss.growth_rate):,.0f}** "
               "before any store override.")

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
        # Background and foreground are always set together. A fill on its own inherits the
        # theme's text colour, which is how white-on-amber happened.
        .map(lambda v: (f"background-color:{PAL['flag_bg']};color:{PAL['flag_fg']};"
                        "font-weight:700") if v == 53 else "")
        # The plan year is marked with weight, not a fill. A whole column of background
        # colour is heavy in either theme, and typography carries "this is the one" without
        # competing with the 53 highlight beside it.
        .set_properties(subset=[str(YEAR)], **{"font-weight": "700"})
        # The 53 also carries a marker, so the meaning does not live in colour alone
        # (WCAG 1.4.1) -- colour-blind readers and greyscale printouts still get it.
        .format(lambda v: f"{v:.0f} *" if v == 53 else f"{v:.0f}")
    )
    st.dataframe(styled, use_container_width=True)
    st.caption(f"FY{YEAR} is shown in bold. **53 \\*** marks a weekday that falls 53 times "
               "that year instead of the usual 52 — the extra selling day to weight for.")

    # One series, not two. The previous chart drew "Recommended Plan" and "COO Adjusted
    # Plan" as separate lines, but both came from build_store_plan(total, days) with the
    # same `days` -- so they were exactly proportional: one shape drawn twice, a
    # constant ratio apart (measured spread across the 12 monthly ratios: 2.2e-16).
    # Neither line carried any per-store or per-day information, so nothing the COO
    # changed store-side could ever move them. The comparison that matters is per store,
    # and it is the chart below.
    st.subheader(f"Monthly Plan — FY{YEAR}")
    plan_monthly = build_store_plan(coo_total, days)["monthly"]
    plan_df = pd.DataFrame({"Month": MONTH_ABBR, "Planned Sales": plan_monthly})

    band_months = ss.forecast_band.get("months") if ss.forecast_band else None
    y_title = "Planned Sales ($)"

    if band_months:
        # Two marks on screen now, so identity cannot rest on colour alone -- both get a
        # legend entry. A constant-value colour field per layer is what makes Altair emit
        # one shared legend instead of two unlabelled layers.
        band_df = pd.DataFrame([
            {"Month": m["month"], "lower": m["lower"], "upper": m["upper"],
             "forecast": m["forecast"], "Series": "Model forecast range"}
            for m in band_months
        ])
        plan_df["Series"] = "Your plan"
        scale = alt.Scale(domain=["Your plan", "Model forecast range"],
                          range=[PAL["plan"], PAL["band"]])
        band = alt.Chart(band_df).mark_area(opacity=PAL["band_opacity"]).encode(
            x=alt.X("Month", sort=MONTH_ABBR, title=None,
                    axis=alt.Axis(labelAngle=0)),
            y=alt.Y("lower", title=y_title, scale=alt.Scale(zero=False)),
            y2=alt.Y2("upper"),
            color=alt.Color("Series:N", scale=scale, title=None),
            tooltip=["Month", "forecast", "lower", "upper"],
        )
        mid = alt.Chart(band_df).mark_line(strokeDash=[4, 3], color=PAL["band"]).encode(
            x=alt.X("Month", sort=MONTH_ABBR, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("forecast"),
        )
        line = alt.Chart(plan_df).mark_line(point=True, strokeWidth=2).encode(
            x=alt.X("Month", sort=MONTH_ABBR, title=None,
                    axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Planned Sales", title=y_title,
                    scale=alt.Scale(zero=False)),
            color=alt.Color("Series:N", scale=scale, title=None),
            tooltip=["Month", "Planned Sales"],
        )
        st.altair_chart(band + mid + line, use_container_width=True)
        covered = [m["month"] for m in band_months]
        st.caption(
            f"Grey band = the model's forecast range (forecast_lower to forecast_upper), "
            f"covering {len(covered)} of 12 months: {', '.join(covered)}. Where the band "
            f"stops is where the forecast stops. Read the two against each other with care: "
            f"your plan is spread by the weekday weights, the band by the model's own "
            f"monthly shape, so a gap can be method rather than disagreement. "
            f"{ss.forecast_band.get('message', '')}"
        )
    else:
        line = alt.Chart(plan_df).mark_line(
            point=True, strokeWidth=2, color=PAL["plan"]
        ).encode(
            x=alt.X("Month", sort=MONTH_ABBR, title=None,
                    axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Planned Sales", title=y_title,
                    scale=alt.Scale(zero=False)),
            tooltip=["Month", "Planned Sales"],
        )
        st.altair_chart(line, use_container_width=True)
        st.caption("The COO Adjusted plan spread across the year by the weekday weights — "
                   "the shape every daily cell in the workbook is priced off. The vertical "
                   "axis does not start at zero, so the month-to-month differences are "
                   "readable; the table below carries the actual dollars. Run Forecast to "
                   "overlay the model's forecast range.")

    # A tooltip must never be the only way to read a value.
    with st.expander("Monthly plan as a table"):
        st.dataframe(
            [{"Month": m, "Planned Sales": f"${v:,.0f}",
              "% of Year": f"{(v / coo_total * 100) if coo_total else 0:.2f}%"}
             for m, v in zip(MONTH_ABBR, plan_monthly)],
            hide_index=True, use_container_width=True,
        )

    # The real decision surface: what the COO moved, per store. Rendered only when there
    # is something to render -- an axis with no bars is worse than a sentence.
    st.subheader("What You Changed")
    deltas = sorted(
        ((c, ss.overrides[c] - recommended_of.get(c, 0.0))
         for c in ss.overrides if c in recommended_of),
        key=lambda t: abs(t[1]), reverse=True,
    )
    deltas = [(c, d) for c, d in deltas if abs(d) >= 0.01]
    if not deltas:
        st.caption("No store overrides yet — every store is carrying its Recommended "
                   "number, so the plan matches the recommendation exactly. Set a COO "
                   "Adjusted Plan Base in the table below and it will show up here.")
    else:
        names = {s["code"]: s["name"] for s in STORES}
        delta_df = pd.DataFrame([
            {"Store": f"{c} — {names.get(c, c)}", "Variance": d,
             "Direction": "Above recommended" if d > 0 else "Below recommended"}
            for c, d in deltas
        ])
        # Diverging by SIGN, never by size: the bar length already carries magnitude, and
        # a value-ramp over store names would colour nominal categories by rank.
        bars = alt.Chart(delta_df).mark_bar(cornerRadius=3).encode(
            x=alt.X("Variance", title="COO Adjusted minus Recommended ($)"),
            # Vega truncates axis labels at 180px by default, which cropped
            # "LEAD - ADC - Leawood" to "LEAD - ADC - Lea...". Store names are the
            # identity here, so they get the room to be read.
            y=alt.Y("Store", sort=[r["Store"] for r in delta_df.to_dict("records")],
                    title=None, axis=alt.Axis(labelLimit=280)),
            color=alt.Color(
                "Direction:N", title=None,
                scale=alt.Scale(domain=["Above recommended", "Below recommended"],
                                range=[PAL["above"], PAL["below"]]),
            ),
            tooltip=["Store", "Variance", "Direction"],
        # 34px a row plus room for the axis band and legend. At 22px the two-bar case put
        # the store labels almost on top of each other, and a height that excludes the
        # axis band is what gives a chart its own tiny scrollbar.
        ).properties(height=min(700, 34 * len(delta_df) + 70))
        st.altair_chart(bars, use_container_width=True)
        net = sum(d for _, d in deltas)
        st.caption(
            f"{len(deltas)} of {len(STORES)} stores overridden, biggest move first. "
            f"Net effect on the network plan: **{'+' if net >= 0 else '−'}"
            f"\\${abs(net):,.0f}**. Every number here is also in the Stores table below."
        )

# --- per-store --------------------------------------------------------------------------
st.subheader("Stores")
st.caption("Set a COO Adjusted Plan Base to override a store. Leave blank to accept the "
           "Recommended number, which is the forecast plus the growth rate.")
# Forecasted and Recommended are separate columns on purpose. Measuring the variance
# against the raw forecast made every store show a difference it had not been given -- 65
# rows reading "COO Adjusted: None" beside a non-zero variance, which is just the growth
# rate wearing the wrong label. These three columns are Plan_Inputs E, F and G.
edited = st.data_editor(
    [{"Code": s["code"], "Store": s["name"], "Region": s["region"],
      "Location Type": "Outlet" if s["code"] in OUTLET_CODES else "Store",
      "Status": s["status"],
      "Forecasted": round(forecasted.get(s["code"], 0.0), 2),
      "Recommended": round(recommended_of.get(s["code"], 0.0), 2),
      "COO Adjusted": ss.overrides.get(s["code"]),
      "Variance": round(effective.get(s["code"], 0.0) - recommended_of.get(s["code"], 0.0), 2)}
     for s in STORES],
    hide_index=True, use_container_width=True, height=380,
    disabled=["Code", "Store", "Region", "Location Type", "Status", "Forecasted",
              "Recommended", "Variance"],
    column_config={
        "Forecasted": st.column_config.NumberColumn(
            format="$%.0f", help="The model's number, with no growth assumed."),
        "Recommended": st.column_config.NumberColumn(
            format="$%.0f", help="Forecast plus the growth rate. Plan_Inputs column E."),
        "COO Adjusted": st.column_config.NumberColumn(
            format="$%.0f", help="Your override. Leave blank to accept Recommended."),
        "Variance": st.column_config.NumberColumn(
            format="$%.0f", help="COO Adjusted minus Recommended. Zero until you override."),
    },
)
new_over = {r["Code"]: float(r["COO Adjusted"]) for r in edited if pd.notna(r["COO Adjusted"])}
if new_over != ss.overrides:
    ss.overrides = new_over
    st.rerun()
