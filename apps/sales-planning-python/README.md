# FY2027 Sales Planning — Databricks App

Streamlit app that reads per-store forecasts from Unity Catalog and emits the 73-sheet,
fully formula-driven planning workbook the COO edits offline.

## Files

```
app.yaml                    Deployment config — the `command` field is mandatory
requirements.txt            Installed automatically on deploy
app.py                      Streamlit entry point (named in app.yaml)
forecast_engine.py          Day-of-week math
workbook.py                 73-sheet openpyxl generator
databricks_io.py            SQL access, auth, forecast parsing + reconciliation
seed_data.json              65 stores, DOW presets, FY2027 holidays
tests/test_acceptance.py    20 tests, incl. the deploy-config guard
```

## Run the tests first

```bash
python tests/test_acceptance.py
```

`test_deploy_config` is the one that matters before every deploy. It reproduces the
failure *"No command to run and no Python file found"* locally instead of on the platform,
and is verified to catch all seven causes:

| Broken how | Caught with |
|---|---|
| Named `app.yml` instead of `app.yaml` | `app.yaml missing from the deployed root directory` |
| No `command:` field | `app.yaml has no 'command' field -- this is the exact failure` |
| `command` names a file not in the source | `command points at 'src/main.py', which is not in the source` |
| Hardcoded port | `command must bind $DATABRICKS_APP_PORT` |
| Binds `127.0.0.1` | `server must listen on 0.0.0.0, not localhost` |
| No `requirements.txt` | `requirements.txt missing -- dependencies will not be installed` |
| Token pasted into `app.yaml` | `app.yaml must not carry a token` |

## Local development

```bash
pip install -r requirements.txt
streamlit run app.py
```

Runs in **mock mode** without credentials — the UI works, Run Forecast returns nothing.
For live data, create `.env` (never deploy it) and export the vars:

```
DATABRICKS_HOST=adb-201205741376717.17.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/3d33bbee9a23df31
DATABRICKS_TOKEN=<personal access token>
```

## Deploy

```bash
databricks auth login --host https://adb-201205741376717.17.azuredatabricks.net
databricks apps create fy2027-sales-planning
databricks sync . /Workspace/Users/<you>/fy2027-sales-planning
databricks apps deploy fy2027-sales-planning \
  --source-code-path /Workspace/Users/<you>/fy2027-sales-planning
```

Then, in the Apps UI: **+ Add resource → SQL warehouse**, pick the Serverless Starter
warehouse, and grant the app's service principal `CAN_USE`. That injects
`DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`, and `databricks_io.auth_mode()`
switches to `oauth` on its own — no code change, no token in the deployment.

Also grant `SELECT` on `gold.retail_data_science` to that service principal.

Requires a **Premium tier** workspace.

## Cost

Medium compute = 0.5 DBU/hr, billed **per hour while running** (provisioned; there is no
scale-to-zero). Roughly **$200–350/month if left up 24/7**. This is a seasonal tool for a
handful of people — **stop the app between planning sessions** and it costs almost nothing.
Redeploy takes seconds.

## Before the first live run

The forecast table's column names are not yet fixed, so `parse_store_forecasts` matches
loosely: first column containing `code`, first matching `sales|forecast|planned`.

**Watch the match count.** A store-code mismatch is the most likely failure and it would
otherwise be silent — every row ignored, every store quietly falling back to a proportional
share. The app reports `Matched N of 65` and turns the message red when `N < 65`, listing
the unmatched codes. If you see that, send me the real column names and codes and the loose
matching can be replaced with exact references.

## Notes

- Every money cell in the workbook is a live **formula**, never a computed value — the COO
  edits the file after export.
- `E3:P3` on each store tab sums the daily COO column. That is what lets any run of cells in
  the COO Adjusted column be deleted and have the month, the year and the Diff follow.
- Easter 2027 is **28 March**. (The older standalone FY2027 workbook has it as 18 April,
  which is wrong.)
