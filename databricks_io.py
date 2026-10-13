"""Unity Catalog access.

Auth resolves in this order:
  1. OAuth M2M   -- DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET, injected by the
                    platform once a SQL warehouse is bound as an App Resource. Production.
  2. PAT         -- DATABRICKS_TOKEN from a local .env. Local dev, only if PATs are allowed
                    for this workspace/user.
  3. OAuth U2M   -- DATABRICKS_AUTH_TYPE=u2m, no secret of any kind. Opens a browser for you
                    to sign in with your own Databricks identity; the SQL connector caches
                    the resulting token locally. Local dev only -- a deployed app's container
                    has no browser and no human to click "Allow", so this must never be the
                    fallback when nothing is configured. This is the option when PATs are
                    disabled org-wide.
  4. Mock        -- nothing configured; returns empty results so the app still runs.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from datetime import datetime, timezone

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

SCHEMA = "gold.retail_data_science"
FORECAST_TABLE = f"{SCHEMA}.test_agg_sales_forecast"
SCENARIOS_TABLE = f"{SCHEMA}.published_planning_scenarios"


def _host() -> str | None:
    h = os.environ.get("DATABRICKS_HOST")
    return re.sub(r"^https?://", "", h).rstrip("/") if h else None


def _http_path() -> str | None:
    p = os.environ.get("DATABRICKS_HTTP_PATH")
    if p:
        return p
    wid = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    return f"/sql/1.0/warehouses/{wid}" if wid else None


def auth_mode() -> str:
    if not (_host() and _http_path()):
        return "mock"
    if os.environ.get("DATABRICKS_CLIENT_ID") and os.environ.get("DATABRICKS_CLIENT_SECRET"):
        return "oauth"
    if os.environ.get("DATABRICKS_TOKEN"):
        return "pat"
    # Opt-in only -- this must never be a silent fallback. A deployed app's container has
    # no browser and no human to complete the sign-in, so it would just hang.
    if os.environ.get("DATABRICKS_AUTH_TYPE", "").strip().lower() in ("u2m", "oauth-u2m", "browser"):
        return "oauth-u2m"
    return "mock"


def is_configured() -> bool:
    return auth_mode() != "mock"


def _connect():
    from databricks import sql

    mode = auth_mode()
    if mode == "oauth":
        return sql.connect(
            server_hostname=_host(),
            http_path=_http_path(),
            credentials_provider=_oauth_provider,
        )
    if mode == "oauth-u2m":
        # Browser-based sign-in with your own identity -- no token of any kind stored in
        # this repo or in .env. The connector opens a tab, you approve, it caches the
        # result (keyed to host + http_path) for reuse on the next run.
        return sql.connect(
            server_hostname=_host(),
            http_path=_http_path(),
            auth_type="databricks-oauth",
        )
    return sql.connect(
        server_hostname=_host(),
        http_path=_http_path(),
        access_token=os.environ["DATABRICKS_TOKEN"],
    )


def _oauth_provider():
    from databricks.sdk.core import Config

    # Bare Config() auto-detects DATABRICKS_HOST / DATABRICKS_CLIENT_ID /
    # DATABRICKS_CLIENT_SECRET from the environment -- exactly the names the platform
    # injects once a SQL warehouse is bound as an App Resource. auth_mode() already
    # confirmed both client vars are present before this is called.
    return Config().authenticate


def execute(statement: str) -> dict:
    """Run a statement. Returns {columns, rows, source, run_timestamp}."""
    stamp = datetime.now(timezone.utc).isoformat()
    if not is_configured():
        return {"columns": [], "rows": [], "source": "mock", "run_timestamp": stamp,
                "note": "Databricks credentials not configured."}

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(statement)
        cols = [d[0] for d in (cur.description or [])]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {"columns": cols, "rows": rows, "source": "live", "run_timestamp": stamp}


# The forward-looking rows. The other value seen in this table is "test", a backtest
# holdout scored against known actuals -- planning off those would be planning off history.
FORECAST_SPLIT = "forecast"

# One champion model is already chosen per store upstream (verified: every unique_id has
# exactly one model_name), so no model or segment filter is needed here. If that ever stops
# being true, coverage_report() reports more days per store than the window can hold and
# the horizon guard refuses the run.
# The year filter is not optional. Once the horizon reaches into the plan year, the forecast
# spans two calendar years, and summing every forecast row gives roughly a third of the
# prior year on top of a full plan year -- about 130% of an annual figure, presented as an
# annual figure. The horizon guard would not catch it either, because the plan year really
# is fully covered; the total is simply too big.
_FORECAST_QUERY = """
SELECT unique_id      AS store_code,
       SUM(forecast)  AS forecast_total,
       MIN(date)      AS first_date,
       MAX(date)      AS last_date,
       COUNT(*)       AS n_days
FROM {table}
WHERE split = '{split}' AND YEAR(date) = {year}
GROUP BY unique_id
"""


def fetch_store_forecasts(plan_year: int) -> dict:
    """Per-store forecast totals for one plan year, aggregated in SQL."""
    return execute(_FORECAST_QUERY.format(table=FORECAST_TABLE, split=FORECAST_SPLIT,
                                          year=int(plan_year)))


def _as_date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def horizon_check(first: dt.date | None, last: dt.date | None,
                  plan_year: int, *, min_days: int = 360) -> tuple[bool, str]:
    """Does the forecast window actually cover the year being planned?

    A 90-day window summed and treated as an annual figure understates the plan by roughly
    75% -- and would do it quietly, since the number still looks like money. This is the
    difference between a forecast the workbook can be built on and one it cannot.
    """
    if first is None or last is None:
        return False, "The forecast rows carry no usable date range."

    year_start, year_end = dt.date(plan_year, 1, 1), dt.date(plan_year, 12, 31)
    covered_start, covered_end = max(first, year_start), min(last, year_end)
    covered = (covered_end - covered_start).days + 1 if covered_start <= covered_end else 0
    in_year = (year_end - year_start).days + 1

    if covered <= 0:
        return False, (
            f"The forecast covers {first} to {last}, which does not overlap the {plan_year} "
            f"plan year at all. Nothing here can be used as a {plan_year} annual figure."
        )
    if covered < min_days:
        return False, (
            f"The forecast covers {first} to {last} — {covered} of the {in_year} days in "
            f"{plan_year}. Summing it would understate a {plan_year} annual plan by about "
            f"{100 * (1 - covered / in_year):.0f}%. Run Forecast needs a full-year horizon."
        )
    return True, f"Forecast covers {first} to {last} ({covered} days of {plan_year})."


# The model's own day-of-year shape, if the pipeline has started emitting it. Checked at
# runtime rather than assumed -- this column did not exist in the table as of 2026-09-10.
DAY_PCT_COLUMN = "day_of_year_forecast_pct"


def fetch_day_pct_forecast(plan_year: int | None = None) -> dict:
    """Offer the model's day-of-year percentages in place of the weekday-weight curve.

    Returns {"status", "message", "day_pct", "stores", "columns"} where status is one of
    "mock" | "missing_column" | "unusable" | "ok". Probes for the column first, because it
    is not yet in the table and a hard-coded SELECT would surface as a raw SQL error.
    """
    probe = execute(f"SELECT * FROM {FORECAST_TABLE} LIMIT 1")
    cols = probe.get("columns") or []

    if probe.get("source") == "mock":
        return {"status": "mock", "message": "Databricks not connected.",
                "day_pct": {}, "stores": 0, "columns": cols}

    if DAY_PCT_COLUMN not in cols:
        return {
            "status": "missing_column",
            "message": (
                f"The forecast table has no '{DAY_PCT_COLUMN}' column yet, so there is no "
                f"model day-mix to use. Columns available: {', '.join(cols)}. "
                "The weekday weights on this page are still driving the daily split."
            ),
            "day_pct": {}, "stores": 0, "columns": cols,
        }

    # Scoped to the plan year for the same reason the totals query is: a day percentage is a
    # share of its own year, so summing across a forecast that spans two years reads well
    # over 100% and looks like corrupt data when it is nothing of the kind.
    year_filter = f" AND YEAR(date) = {int(plan_year)}" if plan_year is not None else ""
    result = execute(
        f"SELECT unique_id AS store_code, date, {DAY_PCT_COLUMN} AS day_pct, "
        f"       MIN(date) OVER (PARTITION BY unique_id) AS first_date, "
        f"       MAX(date) OVER (PARTITION BY unique_id) AS last_date "
        f"FROM {FORECAST_TABLE} WHERE split = '{FORECAST_SPLIT}'{year_filter}"
    )
    rows = result.get("rows", [])
    by_store: dict[str, dict[str, float]] = {}
    firsts, lasts = [], []
    for row in rows:
        code, day = row.get("store_code"), _as_date(row.get("date"))
        try:
            pct = float(row.get("day_pct"))
        except (TypeError, ValueError):
            continue
        if isinstance(code, str) and code.strip() and day:
            by_store.setdefault(code.strip(), {})[day.isoformat()] = pct
        f, l = _as_date(row.get("first_date")), _as_date(row.get("last_date"))
        if f:
            firsts.append(f)
        if l:
            lasts.append(l)

    if not by_store:
        return {"status": "unusable", "message": f"'{DAY_PCT_COLUMN}' exists but returned no "
                "usable rows.", "day_pct": {}, "stores": 0, "columns": cols}

    # The column may arrive on a 0-1 fraction scale or a 0-100 percent scale. Decide from a
    # single day's typical magnitude, not from the per-store sum: the sum only reaches its
    # full value once the whole year is covered, so judging scale by the sum would call a
    # correct-but-partial table "wrong".
    all_days = [v for store in by_store.values() for v in store.values()]
    typical = sorted(all_days)[len(all_days) // 2] if all_days else 0.0
    percent_scale = typical > 0.05          # ~0.0027 as a fraction vs ~0.27 as a percent
    divisor = 100.0 if percent_scale else 1.0
    by_store = {c: {d: v / divisor for d, v in days.items()} for c, days in by_store.items()}

    # Now every store's sum is the share of the year its rows actually cover.
    coverage = {c: sum(v.values()) for c, v in by_store.items()}
    lo, hi = min(coverage.values()), max(coverage.values())
    scale_note = "0-100 percent scale" if percent_scale else "0-1 fraction scale"

    if hi < 0.99:
        return {
            "status": "unusable",
            "message": (
                f"The day percentages are present and look right — read on a {scale_note}, "
                f"they are genuine day-of-year shares. They only cover "
                f"{lo * 100:.1f}%–{hi * 100:.1f}% of the year, though "
                f"(~{lo * 365:.0f}–{hi * 365:.0f} days of 365), because the forecast still "
                f"runs {len(next(iter(by_store.values())))} days rather than a full year. "
                "Applying them would leave most of the calendar with no share at all, so the "
                "weekday weights are still driving the daily split. Extend the forecast "
                "horizon and this will work as-is."
            ),
            "day_pct": {}, "stores": len(coverage), "columns": cols,
        }

    off = {c: t for c, t in coverage.items() if abs(t - 1.0) > 0.01}
    if off:
        sample = ", ".join(f"{c} covers {t * 100:.1f}%" for c, t in list(off.items())[:3])
        over = hi > 1.01
        cause = (
            " Reading over 100% usually means the rows span more than one calendar year: a "
            "day percentage is a share of its own year, so a forecast running into the plan "
            "year carries part of the prior one too."
            + ("" if plan_year is None else
               f" This query was already scoped to {plan_year}, so that is not the cause "
               "here — the percentages themselves do not add up.")
        ) if over else ""
        return {
            "status": "unusable",
            "message": (f"{len(off)} of {len(coverage)} stores have day percentages that do "
                        f"not total 100% of the year ({sample}). Using them would rescale "
                        f"every daily figure, so they have not been applied.{cause}"),
            "day_pct": {}, "stores": len(coverage), "columns": cols,
        }

    if plan_year is not None:
        ok, msg = horizon_check(min(firsts) if firsts else None,
                               max(lasts) if lasts else None, plan_year)
        if not ok:
            return {"status": "unusable", "message": msg,
                    "day_pct": {}, "stores": len(by_store), "columns": cols}

    return {
        "status": "ok",
        "message": (f"Loaded model day percentages for {len(by_store)} stores "
                    f"(read on a {scale_note}); each totals 100% across the year."),
        "day_pct": by_store, "stores": len(by_store), "columns": cols,
    }


def fetch_monthly_forecast_band(plan_year: int | None = None) -> dict:
    """Monthly network forecast with its prediction interval, for the comparison chart.

    Returns {"status", "message", "months": [{month, month_num, year, forecast, lower,
    upper}]}. status is "mock" | "unusable" | "ok".

    Caveat carried in the message, not buried here: summing per-store, per-day 10th/90th
    percentiles overstates the interval for a network total, because it assumes every store
    misses in the same direction on the same day. Real errors partly cancel, so the true
    band on the total is narrower. It is a usable picture of relative uncertainty, not a
    calibrated confidence interval.
    """
    probe = execute(f"SELECT * FROM {FORECAST_TABLE} LIMIT 1")
    cols = probe.get("columns") or []
    if probe.get("source") == "mock":
        return {"status": "mock", "message": "Databricks not connected.", "months": []}

    needed = {"forecast_lower", "forecast_upper"}
    if not needed.issubset(set(cols)):
        return {"status": "unusable",
                "message": ("No prediction-interval columns (forecast_lower / forecast_upper) "
                            f"in the forecast table. Columns seen: {', '.join(cols)}."),
                "months": []}

    # One year at a time. The chart's x-axis is month names, so a forecast spanning two
    # years would put Aug 2026 and Aug 2027 on the same tick and silently draw one over the
    # other.
    year_filter = f" AND YEAR(date) = {int(plan_year)}" if plan_year is not None else ""
    result = execute(
        "SELECT YEAR(date) AS yr, MONTH(date) AS mo, "
        "       SUM(forecast) AS forecast, "
        "       SUM(forecast_lower) AS lower, "
        "       SUM(forecast_upper) AS upper, "
        "       COUNT(DISTINCT date) AS n_days "
        f"FROM {FORECAST_TABLE} WHERE split = '{FORECAST_SPLIT}'{year_filter} "
        "GROUP BY YEAR(date), MONTH(date) ORDER BY yr, mo"
    )

    months = []
    for row in result.get("rows", []):
        try:
            months.append({
                "year": int(row["yr"]), "month_num": int(row["mo"]),
                "month": MONTH_ABBR[int(row["mo"]) - 1],
                "forecast": float(row["forecast"]),
                "lower": float(row["lower"]), "upper": float(row["upper"]),
                "n_days": int(row["n_days"]),
            })
        except (TypeError, ValueError, KeyError, IndexError):
            continue

    if not months:
        return {"status": "unusable", "message": "The forecast returned no monthly rows.",
                "months": []}

    years = sorted({m["year"] for m in months})
    span = f"{months[0]['month']} {years[0]}–{months[-1]['month']} {years[-1]}"
    partial = [m for m in months if m["n_days"] < 28]
    return {
        "status": "ok",
        "months": months,
        "message": (
            f"Model forecast spread covers {span} ({len(months)} months). The band is the sum "
            "of each store-day's forecast_lower and forecast_upper, which overstates the "
            "interval for a network total — individual misses partly cancel in reality, so "
            "read it as relative uncertainty, not a calibrated confidence interval."
            + (f" {len(partial)} month(s) are partial and will read low."
               if partial else "")
        ),
    }


def parse_store_forecasts(result: dict,
                          plan_year: int | None = None) -> tuple[dict[str, float], str | None]:
    """Map the aggregated query result to {store_code: forecast_total}.

    Returns (mapping, warning). A non-None warning means nothing was applied and the caller
    must surface it -- a silent empty result would leave every store on its fallback share
    with no indication anything went wrong.
    """
    cols = result.get("columns") or []
    rows = result.get("rows", [])
    if not cols:
        return {}, None

    required = {"store_code", "forecast_total"}
    if not required.issubset(set(cols)):
        return {}, (
            f"Returned {len(rows)} row(s) but the expected aggregated columns "
            f"({', '.join(sorted(required))}) are not present. Columns seen: {', '.join(cols)}. "
            f"The upstream table schema has probably changed."
        )

    out: dict[str, float] = {}
    firsts, lasts = [], []
    for row in rows:
        code = row.get("store_code")
        try:
            value = float(row.get("forecast_total"))
        except (TypeError, ValueError):
            continue
        if isinstance(code, str) and code.strip():
            out[code.strip()] = value
        f, l = _as_date(row.get("first_date")), _as_date(row.get("last_date"))
        if f:
            firsts.append(f)
        if l:
            lasts.append(l)

    if plan_year is not None:
        ok, message = horizon_check(min(firsts) if firsts else None,
                                    max(lasts) if lasts else None, plan_year)
        if not ok:
            return {}, message

    return out, None


def reconcile(forecasts: dict[str, float], store_codes: list[str]) -> dict:
    """Compare returned codes against the roster so a mismatch cannot pass unnoticed."""
    known = set(store_codes)
    matched = sorted(set(forecasts) & known)
    return {
        "matched": len(matched),
        "expected": len(known),
        "unmatched_from_databricks": sorted(set(forecasts) - known)[:10],
        "missing_from_databricks": sorted(known - set(forecasts))[:10],
    }


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


OVERRIDES_TABLE = f"{SCHEMA}.sales_forecast_overrides"
_OVERRIDE_COLUMNS = (
    "unique_id", "date", "store_name", "division", "original_forecast", "adjusted_forecast",
    "adjustment_reason", "adjusted_by", "store_code", "region", "location_type", "status",
    "growth_pct", "recommended", "variance",
)


def build_override_rows(year: int, stores: list[dict], outlet_codes, forecasted: dict,
                        store_rate, recommended: dict, overrides: dict,
                        reason: str = "") -> list[dict]:
    """One row per store for the plan year, shaped like sales_forecast_overrides.

    `date` is the first day of the plan year: the Stores table is an annual figure per store,
    so it has no finer date than the period it covers. `adjusted_forecast` is the COO Adjusted
    value and is NULL for a store nobody overrode (the number it carries is `recommended`).
    `growth_pct` is the percent as the Stores table shows it (1.0 means 1%).
    """
    rows = []
    for s in stores:
        code = s["code"]
        rec = round(recommended.get(code, 0.0), 2)
        adj = round(float(overrides[code]), 2) if code in overrides else None
        rows.append({
            "unique_id": code,
            "date": dt.date(int(year), 1, 1),
            "store_name": s["name"],
            "division": s["region"],   # East / West; `region` carries the same value
            "original_forecast": round(forecasted.get(code, 0.0), 2),
            "adjusted_forecast": adj,
            "adjustment_reason": (reason.strip() or None) if adj is not None else None,
            "store_code": code,
            "region": s["region"],
            "location_type": "Outlet" if code in outlet_codes else "Store",
            "status": s["status"],
            "growth_pct": round(store_rate(code) * 100, 4),
            "recommended": rec,
            "variance": round((adj if adj is not None else rec) - rec, 2),
        })
    return rows


def _lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, dt.date):
        return f"DATE'{value.isoformat()}'"
    if isinstance(value, (int, float)):
        return repr(float(value))
    return _sql_literal(value)


def build_overrides_merge(rows: list[dict], adjusted_by: str) -> str:
    """MERGE keyed on (unique_id, date): saving again replaces that year's rows, never doubles."""
    if not rows:
        raise ValueError("Nothing to save.")
    by = _sql_literal(adjusted_by)
    values = ",\n".join(
        "(" + ", ".join(_lit(r[c]) if c != "adjusted_by" else by for c in _OVERRIDE_COLUMNS) + ")"
        for r in rows
    )
    return (
        f"MERGE INTO {OVERRIDES_TABLE} AS t USING (\n"
        "SELECT CAST(unique_id AS STRING) AS unique_id, CAST(date AS DATE) AS date,\n"
        "  CAST(store_name AS STRING) AS store_name, CAST(division AS STRING) AS division,\n"
        "  CAST(original_forecast AS DOUBLE) AS original_forecast,\n"
        "  CAST(adjusted_forecast AS DOUBLE) AS adjusted_forecast,\n"
        "  CAST(adjustment_reason AS STRING) AS adjustment_reason,\n"
        "  CAST(adjusted_by AS STRING) AS adjusted_by, current_timestamp() AS adjusted_at,\n"
        "  CAST(store_code AS STRING) AS store_code, CAST(region AS STRING) AS region,\n"
        "  CAST(location_type AS STRING) AS location_type, CAST(status AS STRING) AS status,\n"
        "  CAST(growth_pct AS DOUBLE) AS growth_pct, CAST(recommended AS DOUBLE) AS recommended,\n"
        "  CAST(variance AS DOUBLE) AS variance\n"
        f"FROM VALUES\n{values}\n"
        f"AS v({', '.join(_OVERRIDE_COLUMNS)})\n"
        ") AS s ON t.unique_id = s.unique_id AND t.date = s.date\n"
        "WHEN MATCHED THEN UPDATE SET *\n"
        "WHEN NOT MATCHED THEN INSERT *"
    )


def save_overrides(rows: list[dict], adjusted_by: str) -> dict:
    if not is_configured():
        return {"status": "skipped", "reason": "Databricks credentials not configured."}
    execute(build_overrides_merge(rows, adjusted_by))
    return {"status": "saved", "rows": len(rows)}


_SAVED_QUERY = """
SELECT store_code, original_forecast, adjusted_forecast, adjustment_reason, growth_pct,
       recommended, adjusted_by, adjusted_at
FROM {table}
WHERE YEAR(date) = {year}
"""


def fetch_saved_overrides(plan_year: int) -> dict:
    return execute(_SAVED_QUERY.format(table=OVERRIDES_TABLE, year=int(plan_year)))


def parse_saved_overrides(rows: list[dict], default_store_growth: float) -> dict:
    """Turn saved rows back into the app's state: overrides, per-store growth, network rate.

    The table has no column for the all-stores rate (Plan_Inputs B9), but every row carries
    `recommended` = original x (1 + store rate) x (1 + network rate), so the network rate is
    recoverable from any store nobody overrode. The median across stores ignores a row that
    was edited by hand. Returns {} when there is nothing saved.
    """
    if not rows:
        return {}
    overrides, store_growth, implied = {}, {}, []
    for r in rows:
        code = r.get("store_code")
        if not code:
            continue
        if r.get("adjusted_forecast") is not None:
            overrides[code] = float(r["adjusted_forecast"])
        rate = (float(r["growth_pct"]) / 100.0) if r.get("growth_pct") is not None \
            else default_store_growth
        if abs(rate - default_store_growth) > 1e-9:
            store_growth[code] = rate
        orig, rec = r.get("original_forecast"), r.get("recommended")
        if orig and rec is not None and float(orig) > 0:
            implied.append(float(rec) / (float(orig) * (1 + rate)) - 1)
    implied.sort()
    n = len(implied)
    network = (implied[n // 2] if n % 2 else (implied[n // 2 - 1] + implied[n // 2]) / 2) if n else 0.0
    stamp = max((r["adjusted_at"] for r in rows if r.get("adjusted_at")), default=None)
    who = next((r.get("adjusted_by") for r in rows if r.get("adjusted_at") == stamp), None)
    return {"overrides": overrides, "store_growth": store_growth,
            "growth_rate": round(network, 6), "adjusted_at": stamp, "adjusted_by": who,
            "stores": len(rows)}


def publish_scenario(scenario: dict) -> dict:
    scenario_id = scenario.get("id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(scenario_id)):
        raise ValueError("Scenario id must be alphanumeric/dash/underscore, max 128 chars.")
    if not is_configured():
        return {"status": "skipped", "reason": "Databricks credentials not configured."}

    sid = _sql_literal(scenario_id)
    payload = _sql_literal(json.dumps(scenario))
    return execute(
        f"MERGE INTO {SCENARIOS_TABLE} AS t "
        f"USING (SELECT {sid} AS id) AS s ON t.id = s.id "
        f"WHEN MATCHED THEN UPDATE SET t.payload = {payload} "
        f"WHEN NOT MATCHED THEN INSERT (id, payload) VALUES (s.id, {payload})"
    )
