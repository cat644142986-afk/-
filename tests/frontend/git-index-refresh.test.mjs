import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

function git(repository, ...args) {
  return spawnSync('git', ['-C', repository, ...args], {
    encoding: 'utf8',
    windowsHide: true,
  });
}

test('Git index refresh clears metadata rewrites without hiding real content changes', () => {
  const repository = fs.mkdtempSync(path.join(os.tmpdir(), 'pa-index-refresh-'));
  const trackedPath = path.join(repository, 'tracked.txt');
  const skippedPath = path.join(repository, 'skipped.txt');
  const assumedPath = path.join(repository, 'assumed.txt');
  try {
    assert.equal(git(repository, 'init', '--quiet').status, 0);
    assert.equal(git(repository, 'config', 'user.name', 'Product Atelier Tests').status, 0);
    assert.equal(git(repository, 'config', 'user.email', 'tests@product-atelier.invalid').status, 0);

    fs.writeFileSync(trackedPath, 'baseline\n', 'utf8');
    fs.writeFileSync(skippedPath, 'baseline\n', 'utf8');
    fs.writeFileSync(assumedPath, 'baseline\n', 'utf8');
    assert.equal(git(repository, 'add', 'tracked.txt', 'skipped.txt', 'assumed.txt').status, 0);
    assert.equal(git(repository, 'commit', '--quiet', '-m', 'fixture').status, 0);

    fs.writeFileSync(trackedPath, 'baseline\n', 'utf8');
    const futureMtime = new Date(fs.statSync(trackedPath).mtimeMs + 60_000);
    fs.utimesSync(trackedPath, futureMtime, futureMtime);
    assert.equal(
      git(repository, 'diff-files', '--quiet').status,
      1,
      'the fixture must be stat-dirty before refresh',
    );
    const metadataRefresh = git(repository, 'update-index', '--really-refresh');
    assert.equal(metadataRefresh.status, 0, metadataRefresh.stderr || metadataRefresh.stdout);
    assert.equal(git(repository, 'status', '--porcelain=v1').stdout, '');

    fs.writeFileSync(trackedPath, 'real change\n', 'utf8');
    const contentRefresh = git(repository, 'update-index', '--really-refresh');
    assert.equal(contentRefresh.status, 1, contentRefresh.stderr || contentRefresh.stdout);
    assert.match(
      `${contentRefresh.stdout}${contentRefresh.stderr}`,
      /tracked\.txt: needs update/,
    );
    assert.match(git(repository, 'status', '--porcelain=v1').stdout, / M tracked\.txt/);
    assert.match(git(repository, 'diff', '--', 'tracked.txt').stdout, /\+real change/);

    assert.equal(git(repository, 'add', 'tracked.txt').status, 0);
    const stagedRefresh = git(repository, 'update-index', '--really-refresh');
    assert.equal(stagedRefresh.status, 0, stagedRefresh.stderr || stagedRefresh.stdout);
    assert.match(git(repository, 'status', '--porcelain=v1').stdout, /M  tracked\.txt/);
    assert.match(git(repository, 'diff', 'HEAD', '--', 'tracked.txt').stdout, /\+real change/);

    assert.equal(git(repository, 'update-index', '--skip-worktree', 'skipped.txt').status, 0);
    assert.equal(git(repository, 'update-index', '--assume-unchanged', 'assumed.txt').status, 0);
    fs.writeFileSync(skippedPath, 'hidden real change\n', 'utf8');
    fs.writeFileSync(assumedPath, 'hidden real change\n', 'utf8');
    const hiddenEntries = git(repository, 'ls-files', '-v').stdout;
    assert.match(hiddenEntries, /^S skipped\.txt$/m);
    assert.match(hiddenEntries, /^h assumed\.txt$/m);
    assert.doesNotMatch(git(repository, 'status', '--porcelain=v1').stdout, /(?:skipped|assumed)\.txt/);
  } finally {
    fs.rmSync(repository, { force: true, recursive: true });
  }
});
