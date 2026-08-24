import { DBSQLClient } from '@databricks/sql';

const DATABRICKS_HOST = process.env.DATABRICKS_HOST;
const DATABRICKS_HTTP_PATH =
  process.env.DATABRICKS_HTTP_PATH ||
  (process.env.DATABRICKS_WAREHOUSE_ID ? `/sql/1.0/warehouses/${process.env.DATABRICKS_WAREHOUSE_ID}` : undefined);

// Auto-injected by Databricks Apps when a SQL warehouse resource is bound to the app — no PAT needed in production.
const DATABRICKS_CLIENT_ID = process.env.DATABRICKS_CLIENT_ID;
const DATABRICKS_CLIENT_SECRET = process.env.DATABRICKS_CLIENT_SECRET;

// Local-dev-only fallback: a personal access token, set in .env, never committed.
const DATABRICKS_TOKEN = process.env.DATABRICKS_TOKEN;

const SCHEMA = 'gold.retail_data_science';
export const FORECAST_TABLE = `${SCHEMA}.store_sales_forecast_2027`;
export const SCENARIOS_TABLE = `${SCHEMA}.published_planning_scenarios`;
export const AGG_SALES_FORECAST_TABLE = `${SCHEMA}.aggregate_sales_forecast`;

/** Which credential path is active: 'oauth' (deployed App), 'pat' (local dev), or 'mock'. */
export function authMode() {
  if (!DATABRICKS_HOST || !DATABRICKS_HTTP_PATH) return 'mock';
  if (DATABRICKS_CLIENT_ID && DATABRICKS_CLIENT_SECRET) return 'oauth';
  if (DATABRICKS_TOKEN) return 'pat';
  return 'mock';
}

export function isDatabricksConfigured() {
  return authMode() !== 'mock';
}

function authConfig() {
  if (DATABRICKS_CLIENT_ID && DATABRICKS_CLIENT_SECRET) {
    return { authType: 'databricks-oauth', oauthClientId: DATABRICKS_CLIENT_ID, oauthClientSecret: DATABRICKS_CLIENT_SECRET };
  }
  return { authType: 'access-token', token: DATABRICKS_TOKEN };
}

function mockQueryResult(statement) {
  return {
    columns: ['store_code', 'store_name', 'planned_sales'],
    rows: [],
    runTimestamp: new Date().toISOString(),
    source: 'mock',
    note: `Databricks credentials not configured; returning empty mock result for: ${statement.slice(0, 120)}`,
  };
}

/**
 * Runs a SQL statement against the configured warehouse via the official @databricks/sql driver.
 * Uses OAuth M2M (DATABRICKS_CLIENT_ID/SECRET, auto-injected by Databricks Apps for a bound
 * warehouse resource) when available, falling back to a PAT for local development. Falls back to
 * a mock result entirely when neither is configured, so the app runs standalone.
 */
export async function executeStatement(statement) {
  if (!isDatabricksConfigured()) {
    return mockQueryResult(statement);
  }

  const client = new DBSQLClient();
  try {
    const connection = await client.connect({
      host: DATABRICKS_HOST.replace(/^https?:\/\//, ''),
      path: DATABRICKS_HTTP_PATH,
      ...authConfig(),
    });
    const session = await connection.openSession();
    try {
      const operation = await session.executeStatement(statement, { runAsync: true });
      const rows = await operation.fetchAll();
      await operation.close();
      const columns = rows.length > 0 ? Object.keys(rows[0]) : [];
      return { columns, rows, runTimestamp: new Date().toISOString(), source: 'live' };
    } finally {
      await session.close();
    }
  } finally {
    await client.close();
  }
}

/** Runs the aggregate sales forecast query behind the "Run Forecast" button. */
export async function getAggSalesForecast() {
  return executeStatement(`SELECT * FROM ${AGG_SALES_FORECAST_TABLE}`);
}

function sqlStringLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

export async function publishScenario(scenarioPayload) {
  if (!isDatabricksConfigured()) {
    return { status: 'skipped', reason: 'Databricks credentials not configured', payloadPreview: scenarioPayload.id };
  }
  if (typeof scenarioPayload.id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(scenarioPayload.id)) {
    throw new Error('Invalid scenario id: must be alphanumeric/dash/underscore, max 128 chars.');
  }

  const idLiteral = sqlStringLiteral(scenarioPayload.id);
  const payloadLiteral = sqlStringLiteral(JSON.stringify(scenarioPayload));
  const mergeStatement = `MERGE INTO ${SCENARIOS_TABLE} AS target
USING (SELECT ${idLiteral} AS id) AS source
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET target.payload = ${payloadLiteral}
WHEN NOT MATCHED THEN INSERT (id, payload) VALUES (source.id, ${payloadLiteral})`;

  return executeStatement(mergeStatement);
}
