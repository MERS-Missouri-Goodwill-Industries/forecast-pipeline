"""FY2027 Sales Planning — Databricks App entry point.

Launched by app.yaml:
    streamlit run app.py --server.port $DATABRICKS_APP_PORT --server.address 0.0.0.0
"""

from __future__ import annotations

import streamlit as st

import databricks_io as dbx
from forecast_engine import (
    WEEKDAYS, build_day_factors, build_store_plan, compute_forecasted_bases,
    default_holidays, load_seed, weekday_mix,
)
from workbook import workbook_bytes

YEAR = 2027
SEED = load_seed()
STORES = SEED["stores"]
PRESETS = SEED["dow_presets"]
PRESET_LABELS = {
    "recommended": "Recommended (from Actuals)",
    "excel_plan": "Current Excel Plan",
    "even": "Even Split",
}

st.set_page_config(page_title="FY2027 Sales Planning", layout="wide")


def _init():
    ss = st.session_state
    ss.setdefault("weights", dict(PRESETS["recommended"]))
    ss.setdefault("preset", "recommended")
    ss.setdefault("recommended_plan", 150_000_000.0)
    ss.setdefault("overrides", {})
    ss.setdefault("db_forecasts", {})
    ss.setdefault("run_status", None)


_init()
ss = st.session_state

# --- header -----------------------------------------------------------------------------
left, right = st.columns([3, 1])
with left:
    st.title("MERS Goodwill — FY2027 Sales Planning")
    st.caption("Recommendation workbook generator. Every exported cell is a live formula.")
with right:
    mode = dbx.auth_mode()
    st.success("Databricks Live") if mode != "mock" else st.warning("Local / Mock Data")
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

# --- controls ---------------------------------------------------------------------------
c1, c2 = st.columns([1, 2])

with c1:
    st.subheader("Day-of-Week Weighting")
    preset = st.selectbox("Preset", list(PRESET_LABELS) + ["custom"],
                          format_func=lambda k: PRESET_LABELS.get(k, "Custom"),
                          index=list(PRESET_LABELS).index(ss.preset) if ss.preset in PRESET_LABELS else 3)
    if preset != ss.preset:
        ss.preset = preset
        if preset in PRESETS:
            ss.weights = dict(PRESETS[preset])
        st.rerun()

    new_weights = {}
    for d in WEEKDAYS:
        new_weights[d] = st.number_input(
            d, min_value=0.0, max_value=100.0,
            value=round(ss.weights.get(d, 0.0) * 100, 2), step=0.01, format="%.2f",
            key=f"w_{d}",
        ) / 100
    if new_weights != ss.weights:
        ss.weights, ss.preset = new_weights, "custom"
        st.rerun()

    total = sum(ss.weights.values())
    (st.success if abs(total - 1) < 5e-4 else st.warning)(f"Total: {total * 100:.2f}%")

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

    st.subheader("Monthly Plan")
    network = build_store_plan(coo_total, days)
    st.dataframe(
        [{"Month": m, "Planned Sales": f"${network['monthly'][i]:,.0f}"}
         for i, m in enumerate(
             ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])],
        hide_index=True, use_container_width=True, height=250,
    )

# --- per-store --------------------------------------------------------------------------
st.subheader("Stores")
st.caption("Set a COO Adjusted Plan Base to override a store. Leave blank to accept the forecast.")
edited = st.data_editor(
    [{"Code": s["code"], "Store": s["name"], "Region": s["region"], "Status": s["status"],
      "Forecasted": round(forecasted.get(s["code"], 0.0), 2),
      "COO Adjusted": ss.overrides.get(s["code"]),
      "Variance": round(effective.get(s["code"], 0.0) - forecasted.get(s["code"], 0.0), 2)}
     for s in STORES],
    hide_index=True, use_container_width=True, height=380,
    disabled=["Code", "Store", "Region", "Status", "Forecasted", "Variance"],
    column_config={
        "Forecasted": st.column_config.NumberColumn(format="$%.0f"),
        "COO Adjusted": st.column_config.NumberColumn(format="$%.0f"),
        "Variance": st.column_config.NumberColumn(format="$%.0f"),
    },
)
new_over = {r["Code"]: float(r["COO Adjusted"]) for r in edited if r["COO Adjusted"] is not None}
if new_over != ss.overrides:
    ss.overrides = new_over
    st.rerun()

# --- export -----------------------------------------------------------------------------
st.subheader("Export")
if st.button("Build Workbook", type="primary"):
    with st.spinner("Generating 73 sheets…"):
        data = workbook_bytes(
            year=YEAR, stores=STORES, weights=ss.weights, holidays=holidays,
            recommended_plan=ss.recommended_plan,
            store_overrides={c: {"plan_base": v} for c, v in ss.overrides.items()},
        )
    st.download_button(
        "Download POC_Prototype_2027_Planned_Sales_Workbook_2027.xlsx",
        data=data,
        file_name="POC_Prototype_2027_Planned_Sales_Workbook_2027.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
