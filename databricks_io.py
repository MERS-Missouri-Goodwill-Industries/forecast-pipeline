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
                    fallback when nothing is configured. Requires PATs to be usable is NOT
                    required -- this is the option when PATs are disabled org-wide.
  4. Mock        -- nothing configured; returns empty results so the app still runs.
"""

from __future__ import annotations

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


def fetch_store_forecasts() -> dict:
    """Read the latest per-store annual forecast."""
    return execute(f"SELECT * FROM {FORECAST_TABLE}")


def parse_store_forecasts(result: dict) -> tuple[dict[str, float], str | None]:
    """Map the query result to {store_code: annual_forecast}.

    Column names are matched loosely because the schema is not yet fixed. Returns
    (mapping, warning). A non-None warning means the caller should surface it -- a code
    mismatch otherwise fails silently and every store quietly keeps its fallback share.
    """
    cols = result.get("columns") or []
    if not cols:
        return {}, None

    code_col = next((c for c in cols if re.search(r"code", c, re.I)), None)
    value_col = next((c for c in cols if re.search(r"sales|forecast|planned", c, re.I)), None)
    if not code_col or not value_col:
        return {}, (f"Returned {len(result.get('rows', []))} row(s) but no store-code / forecast "
                    f"column could be identified. Columns seen: {', '.join(cols)}")

    out: dict[str, float] = {}
    for row in result.get("rows", []):
        code = row.get(code_col)
        try:
            value = float(row.get(value_col))
        except (TypeError, ValueError):
            continue
        if isinstance(code, str) and code.strip():
            out[code.strip()] = value
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
