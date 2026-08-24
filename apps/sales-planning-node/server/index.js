// NOTE: this project is ESM ("type": "module" in package.json). `require` and `__dirname`
// are NOT available here — use imports and fileURLToPath as below.
import 'dotenv/config';
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { authMode, executeStatement, getAggSalesForecast, isDatabricksConfigured, publishScenario } from './databricks.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.join(__dirname, '..', 'dist');

const app = express();
app.use(express.json({ limit: '5mb' }));

app.get('/api/databricks/status', (_req, res) => {
  res.json({ connected: isDatabricksConfigured() });
});

// Deployment probe: reports how auth resolved and whether the UI build is actually
// present, so a misconfigured deploy is visible without reading container logs.
app.get('/api/health', (_req, res) => {
  const uiBuilt = fs.existsSync(path.join(distDir, 'index.html'));
  res.json({
    status: uiBuilt ? 'ok' : 'degraded',
    auth: authMode(),
    host: process.env.DATABRICKS_HOST ?? 'not set',
    httpPath: process.env.DATABRICKS_HTTP_PATH ?? 'not set',
    uiBuilt,
    node: process.version,
  });
});

app.post('/api/databricks/query', async (req, res) => {
  const { statement } = req.body ?? {};
  if (typeof statement !== 'string' || !statement.trim()) {
    res.status(400).json({ error: 'Request body must include a non-empty "statement" string.' });
    return;
  }
  try {
    const result = await executeStatement(statement);
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: err instanceof Error ? err.message : 'Databricks query failed' });
  }
});

app.post('/api/databricks/run-forecast', async (_req, res) => {
  try {
    const result = await getAggSalesForecast();
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: err instanceof Error ? err.message : 'Databricks forecast query failed' });
  }
});

app.post('/api/databricks/publish-scenario', async (req, res) => {
  const scenario = req.body;
  if (!scenario || typeof scenario !== 'object' || !scenario.id) {
    res.status(400).json({ error: 'Request body must be a scenario object with an "id" field.' });
    return;
  }
  try {
    const result = await publishScenario(scenario);
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: err instanceof Error ? err.message : 'Databricks publish failed' });
  }
});

// Databricks Apps runs a single process and serves the SPA + API from it — no separate
// frontend/backend containers. Static files must come from the Vite production build.
app.use(express.static(distDir));
app.get('*', (_req, res) => {
  const indexHtml = path.join(distDir, 'index.html');
  if (!fs.existsSync(indexHtml)) {
    res.status(503).send(
      '<h1>UI build missing</h1><p>dist/ was not found. Run <code>npm run build</code> before '
      + 'deploying, and make sure dist/ is included in the deployed source.</p>',
    );
    return;
  }
  res.sendFile(indexHtml);
});

// Databricks Apps supplies DATABRICKS_APP_PORT and requires binding all interfaces.
const port = process.env.DATABRICKS_APP_PORT || process.env.PORT || 8000;
app.listen(port, '0.0.0.0', () => {
  console.log(`Sales Planning App listening on 0.0.0.0:${port}`);
  console.log(`Auth mode: ${authMode()}`);
  if (!fs.existsSync(path.join(distDir, 'index.html'))) {
    console.warn('WARNING: dist/index.html not found — the UI will not serve. Run `npm run build`.');
  }
});
