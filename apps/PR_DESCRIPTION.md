## Summary

Two deployable implementations of the FY2027 store sales planning tool, plus the design record. Both read per-store forecasts from Unity Catalog and emit the 73-sheet, fully formula-driven planning workbook the COO edits offline.

| | `sales-planning-node` | `sales-planning-python` |
|---|---|---|
| UI | React 19 + Vite | Streamlit |
| Excel | ExcelJS | openpyxl |
| Deploy path | :warning: `npm install` behaviour undocumented | :white_check_mark: `requirements.txt` documented |
| Tests | 9/9 deploy + 12/12 workbook | 20/20 |

**Deploy the Python app first.** It proves the risky unknowns — App Resource binding, OAuth M2M, and whether the `aggregate_sales_forecast` store codes actually join the roster.

## Guards the deployment failure

Both apps test for `No command to run and no Python file found` before it can reach the platform. Each failure mode was verified by deliberately breaking the config:

- named `app.yml` instead of `app.yaml`
- missing `command:` field
- `command` pointing at a file not in the deployed source
- hardcoded port instead of `$DATABRICKS_APP_PORT`
- binding `127.0.0.1` instead of `0.0.0.0`
- missing `requirements.txt`
- a token committed into `app.yaml`

## Notable decisions

- **`dist/` is committed** for the Node app (the repo's global `dist/` ignore is negated for that one path) so no build step is needed at deploy time.
- **Runtime deps cut to three** — `express`, `@databricks/sql`, `dotenv`. React/Recharts/ExcelJS are bundled into `dist/` by Vite at build time and moved to `devDependencies`, keeping a production install small enough to vendor if npm-install turns out not to run.
- **Workbook math**: one annual denominator for `Day % of Annual`, so the seven weekday shares are identical in every month and for every store, and the column totals exactly 1.0.
- **Two-track store tabs**: Recommended and COO Adjusted side by side. Row 3 sums the daily COO column, which is what lets any run of days be deleted and have the month, the year and the Diff follow automatically.

## Follow-ups (not in this PR)

- `published_planning_scenarios` schema is unconfirmed — current code MERGEs `(id, payload)`; an alternative draft used `(scenario_name, store_code, plan_base, coo_adjusted, published_at)`. Needs checking against the real table.
- Forecast-implied YoY growth is a labelled placeholder pending the model run.
- Six store codes remain unmapped (Bridgeton Outlet, the O'Fallon MO/IL split, Springfield Battlefield/Chestnut Crossing).

## Test plan

```bash
cd apps/sales-planning-python && python tests/test_acceptance.py   # 20/20
cd apps/sales-planning-node   && npm ci && npm run build \
                              && npm run test:deploy && npm run test:excel
```

CI validates both on every push to `apps/**`; deploy is manual via workflow_dispatch.

:robot: Generated with [Claude Code](https://claude.com/claude-code)
