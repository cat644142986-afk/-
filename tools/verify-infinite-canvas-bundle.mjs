import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = new URL('../', import.meta.url);
const distPath = new URL('dist/', root);
const manifest = JSON.parse(readFileSync(new URL('.vite/manifest.json', distPath), 'utf8'));
const html = readFileSync(new URL('index.html', distPath), 'utf8');
const entry = manifest['index.html'];
const islandKey = entry?.dynamicImports?.find((key) => {
  const candidate = manifest[key];
  return candidate?.isDynamicEntry
    && candidate.css?.length
    && candidate.assets?.some((asset) => asset.includes('Assistant-'));
});
const island = manifest[islandKey];

assert.ok(entry?.isEntry, 'production index entry is missing from the Vite manifest');
assert.ok(island?.isDynamicEntry, 'infinite canvas must remain a dynamic Vite entry');
assert.ok(entry.dynamicImports?.includes(islandKey), 'index entry must dynamically import the canvas island');
assert.doesNotMatch(html, /modulepreload/i, 'dynamic canvas dependencies must not be preloaded on quick startup');
assert.ok(!html.includes(island.file), 'canvas island must not be referenced by the initial HTML');
for (const cssFile of island.css || []) {
  assert.ok(!html.includes(cssFile), `canvas stylesheet ${cssFile} must load only with the island`);
}

function directoryBytes(path) {
  return readdirSync(path).reduce((total, name) => {
    const child = join(path, name);
    return total + (statSync(child).isDirectory() ? directoryBytes(child) : statSync(child).size);
  }, 0);
}

const distBytes = directoryBytes(fileURLToPath(distPath));
const projectedFormalMiB = 358.56 + (distBytes / 1024 / 1024);
assert.ok(projectedFormalMiB < 450, `projected formal portable size is ${projectedFormalMiB.toFixed(2)} MiB`);

console.log(JSON.stringify({
  contract: 'infinite-canvas-lazy-boundary-v1',
  entry_file: entry.file,
  island_manifest_key: islandKey,
  island_file: island.file,
  island_css: island.css || [],
  initial_modulepreload_count: 0,
  dist_bytes: distBytes,
  projected_formal_mib: Number(projectedFormalMiB.toFixed(2)),
}, null, 2));
