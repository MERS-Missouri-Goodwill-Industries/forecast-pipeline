# FY2027 Sales Planning — Databricks Apps

Two implementations of the same tool. Both read per-store forecasts from Unity Catalog and
emit the 73-sheet, fully formula-driven planning workbook the COO edits offline.

| | `sales-planning-node` | `sales-planning-python` |
|---|---|---|
| UI | React 19 + Vite (the built dashboard) | Streamlit |
| Server | Express | Streamlit |
| Excel | ExcelJS | openpyxl |
| Deploy path | ⚠️ `npm install` behaviour on Databricks Apps is **undocumented** | ✅ `requirements.txt` is installed automatically |
| Tests | 12 workbook criteria + 9 deploy checks | 20 tests incl. deploy checks |

**Deploy the Python app first.** It is the certain path, and it proves the risky unknowns —
resource binding, OAuth, and whether the `aggregate_sales_forecast` store codes actually
join. Once that works, deploy the Node app if you want the richer React UI.

---

## The deployment error this guards against

```
No command to run and no Python file found. Please add a 'command' field to your
app.yml file.
```

Causes, all asserted by the deploy-config tests in both apps:

| Cause | Fix |
|---|---|
| File named `app.yml` | Databricks reads **`app.yaml`** |
| No `command:` field | Add it — it is mandatory |
| `command` names a file not in the deployed source | Point it at a real path |
| Hardcoded port | Bind `$DATABRICKS_APP_PORT` |
| Binds `127.0.0.1` | Bind `0.0.0.0` |

Run before every deploy:

```bash
cd apps/sales-planning-node    && npm run test:deploy
cd apps/sales-planning-python  && python tests/test_acceptance.py
```

CI runs both on every push to `apps/**`.

---

## Deploy from source

Set repo secrets: `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`
(a service principal with `CAN_MANAGE` on apps). Then **Actions → Validate and deploy
Databricks App → Run workflow** and pick `node` or `python`.

Manually:

```bash
databricks auth login --host https://adb-201205741376717.17.azuredatabricks.net

APP=fy2027-sales-planning
SRC=apps/sales-planning-python          # or apps/sales-planning-node

databricks apps create "$APP"
databricks sync "$SRC" "/Workspace/Shared/$APP" --full
databricks apps deploy "$APP" --source-code-path "/Workspace/Shared/$APP"
```

For the Node app, run `npm run build` first — `dist/` must exist. It is committed (the
repo's global `dist/` ignore is negated for this one path) so the platform never needs a
build step.

### After the first deploy — required

1. Apps UI → your app → **+ Add resource → SQL warehouse** → Serverless Starter →
   grant the app's service principal `CAN_USE`.
2. Grant that principal `SELECT` on `gold.retail_data_science`.

That injects `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`. Both apps detect them
and switch from PAT/mock to OAuth on their own — no code change, no token in the repo.

Requires a **Premium tier** workspace.

---

## Verifying a deploy

The Node app exposes `/api/health`:

```json
{ "status": "ok", "auth": "oauth", "uiBuilt": true, "node": "v22.x" }
```

`"auth": "mock"` means the warehouse resource is not bound. `"uiBuilt": false` means
`dist/` did not ship.

Then click **Run Forecast**. Both apps report **`Matched N of 65`** and turn the message
red when `N < 65`, listing the unmatched codes. Watch that number — a store-code mismatch
would otherwise fail silently, with every store quietly falling back to a proportional
share.

---

## Cost

Medium compute = 0.5 DBU/hr, billed **per hour while running** — provisioned, no
scale-to-zero. Roughly **$200–350/month if left up 24/7**. This is a seasonal tool for a
handful of people, so **stop the app between planning sessions**; redeploy takes seconds.

---

## Local development

```bash
# Node
cd apps/sales-planning-node
npm install
npm run build && npm start          # http://localhost:8000
npm run dev                         # Vite dev server on :3000

# Python -- lives at the repo root, not under apps/
pip install -r requirements.txt
streamlit run app.py
```

Both run in **mock mode** without credentials.

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

If PATs are allowed for you, the simpler option — for the Python app, set `.env` directly
(nothing loads it automatically; `export $(grep -v '^#' .env | xargs)` before running); for
the Node app, create `.env` from `.env.example` (never commit it — `.gitignore` covers it):

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
