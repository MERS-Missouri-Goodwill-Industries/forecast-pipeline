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
_FORECAST_QUERY = """
SELECT unique_id      AS store_code,
       SUM(forecast)  AS forecast_total,
       MIN(date)      AS first_date,
       MAX(date)      AS last_date,
       COUNT(*)       AS n_days
FROM {table}
WHERE split = '{split}'
GROUP BY unique_id
"""


def fetch_store_forecasts() -> dict:
    """Per-store forecast totals, aggregated in SQL rather than pulled row by row."""
    return execute(_FORECAST_QUERY.format(table=FORECAST_TABLE, split=FORECAST_SPLIT))


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
