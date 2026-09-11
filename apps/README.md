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

## Deploy

**Merging to `main` is the deploy.** The app is

| | |
|---|---|
| Name | `sales-forecast-workbook` |
| URL | https://sales-forecast-workbook-201205741376717.17.azure.databricksapps.com |
| Source | this repo, branch `main`, `SNAPSHOT` mode |
| Compute | MEDIUM (0.5 DBU/hr) |

It is bound to the repository in the Databricks Apps UI, so it resolves the head commit
and redeploys itself. Nothing in CI deploys; the GitHub workflow only validates.

Check what is actually live:

```bash
databricks auth login --host https://adb-201205741376717.17.azuredatabricks.net
databricks apps get sales-forecast-workbook          # compute + app status
databricks apps list-deployments sales-forecast-workbook
databricks apps get-deployment sales-forecast-workbook <deployment-id>   # resolved_commit
```

`resolved_commit` is the ground truth for what the app is running — compare it to
`git rev-parse main`.

> **Do not `databricks apps create`.** A second app is a second MEDIUM compute, billed in
> parallel at roughly $200–350/month, and deploying with `--source-code-path` switches the
> app off git-source mode onto workspace files.

### Required once — not yet done

As of 2026-09-11 the app reports `resources: null` — **no SQL warehouse is bound**, so it
runs in mock mode and Run Forecast returns seeded numbers, not the model's.

The service principal to grant is **`app-1rh467 sales-forecast-workbook`**
(client id `3405259b-22a7-4e1f-9dba-d725b074735e`).

1. Apps UI → the app → **+ Add resource → SQL warehouse** → Serverless Starter →
   grant that service principal `CAN_USE`.
2. Grant it `SELECT` on `gold.retail_data_science`.
3. Confirm with `databricks apps get sales-forecast-workbook` — `resources` stops being
   `null`.

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
