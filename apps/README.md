# FY2027 Sales Planning — Databricks Apps

The app is a **Streamlit app living at the repo root** — `app.py`, `workbook.py`,
`forecast_engine.py`, `databricks_io.py`, `seed_data.json`, `app.yaml`. It reads per-store
forecasts from Unity Catalog and emits the 73-sheet, fully formula-driven planning workbook
the COO edits offline.

> **Retired:** a React 19 + Vite + Express implementation of the same tool used to live in
> `apps/sales-planning-node`, and was a selectable deploy target in the workflow. It was
> removed in September 2026: it carried 525 npm packages and five open advisories — two of
> them high, on `thrift` beneath the Databricks SQL driver — for a UI the Python app already
> covers, and `npm install` behaviour on Databricks Apps was never documented. It is in git
> history if it is ever wanted back.

---

## The deployment error this guards against

```
No command to run and no Python file found. Please add a 'command' field to your
app.yml file.
```

Causes, all asserted by `tests/test_acceptance.py::test_deploy_config`:

| Cause | Fix |
|---|---|
| File named `app.yml` | Databricks reads **`app.yaml`** |
| No `command:` field | Add it — it is mandatory |
| `command` names a file not in the deployed source | Point it at a real path |
| Hardcoded port | Bind `$DATABRICKS_APP_PORT` |
| Binds `127.0.0.1` | Bind `0.0.0.0` |

Run before every deploy:

```bash
python tests/test_acceptance.py
```

CI runs it on every push that touches the app.

---

## Deploy from source

Set repo secrets: `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`
(a service principal with `CAN_MANAGE` on apps). Then **Actions → Validate and deploy
Databricks App → Run workflow**.

Manually:

```bash
databricks auth login --host https://adb-201205741376717.17.azuredatabricks.net

APP=fy2027-sales-planning

databricks apps create "$APP"
databricks sync . "/Workspace/Shared/$APP" --full
databricks apps deploy "$APP" --source-code-path "/Workspace/Shared/$APP"
```

### After the first deploy — required

1. Apps UI → your app → **+ Add resource → SQL warehouse** → Serverless Starter →
   grant the app's service principal `CAN_USE`.
2. Grant that principal `SELECT` on `gold.retail_data_science`.

That injects `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`. The app detects them and
switches from mock to OAuth on its own — no code change, no token in the repo.

Requires a **Premium tier** workspace.

---

## Verifying a deploy

The sidebar reports the auth mode. **`mock`** means the warehouse resource is not bound.

Then click **Run Forecast**. The app reports **`Matched N of 65`** and flags the message
when `N < 65`, listing the unmatched codes. Watch that number — a store-code mismatch would
otherwise fail silently, with every store quietly falling back to a proportional share.

---

## Cost

Medium compute = 0.5 DBU/hr, billed **per hour while running** — provisioned, no
scale-to-zero. Roughly **$200–350/month if left up 24/7**. This is a seasonal tool for a
handful of people, so **stop the app between planning sessions**; redeploy takes seconds.

---

## Local development

```bash
pip install -r requirements.txt
streamlit run app.py
```

Runs in **mock mode** without credentials.

**If personal access tokens are disabled for this workspace** (org policy — not every
workspace allows PATs), use OAuth U2M instead: a one-time browser sign-in with your own
Databricks identity, no secret in any file.

```
DATABRICKS_HOST=adb-201205741376717.17.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/3d33bbee9a23df31
DATABRICKS_AUTH_TYPE=u2m
```

The first query opens a browser tab to approve; the SQL connector caches the result for
reuse. This is opt-in only (`databricks_io.auth_mode()` never falls back to it on its own)
because a *deployed* app's container has no browser and no human to click Allow.

If PATs are allowed for you, the simpler option is `.env` at the repo root — nothing loads
it automatically, so `export $(grep -v '^#' .env | xargs)` before running. Never commit it;
`.gitignore` covers it, and `test_deploy_config` fails if it is present at deploy time:

```
DATABRICKS_HOST=adb-201205741376717.17.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/3d33bbee9a23df31
DATABRICKS_TOKEN=<personal access token>
```

---

## Docs

`docs/` holds the design record:

- `PROJECT_CONTEXT_FOR_AI_STUDIO.md` — business context, current state, open items
- `DATABRICKS_BUILD_SPEC.md` — implementation spec with exact formulas and cell maps
- `EXCEL_EXPORT_ACCEPTANCE_CRITERIA.md` — what each workbook test guards against
