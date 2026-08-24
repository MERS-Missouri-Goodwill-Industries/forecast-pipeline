/**
 * Guards the Databricks Apps deployment failure:
 *
 *   "No command to run and no Python file found. Please add a 'command' field to your
 *    app.yml file."
 *
 * That fires when app.yaml is missing, misnamed (app.yml), has no `command`, or the
 * command points at a file that is not in the deployed source. All of those are asserted
 * here so the failure surfaces locally instead of on deploy.
 *
 * Run: npm run test:deploy
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');
const exists = (p) => fs.existsSync(path.join(ROOT, p));

/** Minimal YAML reader for the `command:` list — avoids adding a dependency. */
function parseCommand(raw) {
  const out = [];
  let inCommand = false;
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim() || line.trim().startsWith('#')) continue;
    if (/^command:\s*$/.test(line)) { inCommand = true; continue; }
    if (/^command:\s*\[/.test(line)) {
      return line.slice(line.indexOf('[') + 1, line.lastIndexOf(']'))
        .split(',').map((s) => s.trim().replace(/^["']|["']$/g, '')).filter(Boolean);
    }
    if (/^\w[\w-]*:/.test(line)) { inCommand = false; continue; }
    if (inCommand) {
      const m = line.match(/^\s*-\s*["']?(.*?)["']?\s*$/);
      if (m) out.push(m[1]);
    }
  }
  return out;
}

const tests = {
  'app.yaml exists at the deployed root'() {
    assert.ok(exists('app.yaml'), 'app.yaml missing from the deployed root directory');
    assert.ok(!exists('app.yml'), 'app.yml found — Databricks Apps reads app.yaml. Rename it.');
  },

  'app.yaml declares a non-empty command'() {
    const cmd = parseCommand(read('app.yaml'));
    assert.ok(cmd.length > 0, "app.yaml has no 'command' field — this is the exact failure");
    assert.ok(cmd.every((p) => typeof p === 'string' && p.length),
      'every command entry must be a non-empty string');
  },

  'command points at a file that is actually in the source'() {
    const cmd = parseCommand(read('app.yaml'));
    const entry = cmd.find((p) => p.endsWith('.js') || p.endsWith('.mjs') || p.endsWith('.py'));
    assert.ok(entry, `command names no runnable entry point: ${JSON.stringify(cmd)}`);
    assert.ok(exists(entry), `command points at '${entry}', which is not in the source`);
  },

  'server is ESM-correct (no require / __dirname)'() {
    const pkg = JSON.parse(read('package.json'));
    assert.equal(pkg.type, 'module', 'package.json must declare "type": "module"');
    for (const file of ['server/index.js', 'server/databricks.js']) {
      const src = read(file);
      assert.ok(!/^\s*(?:const|let|var)\s+\w+\s*=\s*require\(/m.test(src),
        `${file} uses require() — fatal in an ESM project`);
      // __dirname is undefined in ESM. Using it is fine only if the file derives it first.
      const usesDirname = /\b__dirname\b/.test(src);
      const derivesDirname = /__dirname\s*=\s*path\.dirname\(\s*fileURLToPath\(\s*import\.meta\.url/.test(src);
      assert.ok(!usesDirname || derivesDirname,
        `${file} uses __dirname without deriving it from import.meta.url`);
    }
  },

  'server binds DATABRICKS_APP_PORT on all interfaces'() {
    const src = read('server/index.js');
    assert.ok(src.includes('DATABRICKS_APP_PORT'),
      'server must read DATABRICKS_APP_PORT — the platform assigns the port');
    assert.ok(/listen\([^)]*['"]0\.0\.0\.0['"]/.test(src),
      "server must listen on '0.0.0.0', not localhost");
  },

  'runtime dependencies stay minimal'() {
    const pkg = JSON.parse(read('package.json'));
    const deps = Object.keys(pkg.dependencies ?? {});
    for (const required of ['express', '@databricks/sql']) {
      assert.ok(deps.includes(required), `${required} must be a runtime dependency`);
    }
    for (const frontend of ['react', 'recharts', 'exceljs']) {
      assert.ok(!deps.includes(frontend),
        `${frontend} is bundled into dist/ at build time — keep it in devDependencies`);
    }
    assert.ok(pkg.scripts?.start, 'a "start" script is required for the npm-run-start fallback');
  },

  'UI build is present and will be served'() {
    assert.ok(exists('dist/index.html'),
      'dist/index.html missing — run `npm run build` before deploying');
    const src = read('server/index.js');
    assert.ok(src.includes('express.static'), 'server must serve the built UI');
  },

  'no credentials committed'() {
    assert.ok(!read('app.yaml').includes('DATABRICKS_TOKEN'),
      'app.yaml must not carry a token');
    assert.ok(!exists('.env'), '.env must not be present in the deployed source');
    const gitignore = exists('.gitignore') ? read('.gitignore') : '';
    assert.ok(gitignore.includes('.env'), '.gitignore must exclude .env');
  },

  'SQL is parameterised, not string-interpolated'() {
    const src = read('server/databricks.js');
    assert.ok(src.includes('sqlStringLiteral'),
      'databricks.js must escape values before embedding them in SQL');
    // Catch `'${...}'` inside a SQL template literal — the injection shape.
    const risky = src.match(/(INSERT|MERGE|UPDATE|DELETE)[\s\S]{0,400}?'\$\{(?!\w*Literal)/gi);
    assert.ok(!risky, `raw interpolation into SQL detected: ${risky && risky[0].slice(0, 80)}`);
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try {
    fn();
    console.log(`  PASS  ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`  FAIL  ${name}: ${err.message}`);
  }
}
console.log(failed ? `\n${failed} FAILURE(S)` : '\nALL DEPLOY CHECKS PASSED');
process.exit(failed ? 1 : 0);
