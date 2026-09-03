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

test('Git content gates ignore filtered rewrites and expose every real source change', () => {
  const repository = fs.mkdtempSync(path.join(os.tmpdir(), 'pa-index-refresh-'));
  const trackedPath = path.join(repository, 'tracked.txt');
  const skippedPath = path.join(repository, 'skipped.txt');
  const assumedPath = path.join(repository, 'assumed.txt');
  const untrackedPath = path.join(repository, 'untracked.txt');
  const ignoredPath = path.join(repository, 'ignored.txt');
  try {
    assert.equal(git(repository, 'init', '--quiet').status, 0);
    assert.equal(git(repository, 'config', 'user.name', 'Product Atelier Tests').status, 0);
    assert.equal(git(repository, 'config', 'user.email', 'tests@product-atelier.invalid').status, 0);
    assert.equal(git(repository, 'config', 'core.autocrlf', 'true').status, 0);

    fs.writeFileSync(trackedPath, 'baseline\n', 'utf8');
    fs.writeFileSync(skippedPath, 'baseline\n', 'utf8');
    fs.writeFileSync(assumedPath, 'baseline\n', 'utf8');
    fs.writeFileSync(path.join(repository, '.gitignore'), 'ignored.txt\n', 'utf8');
    assert.equal(
      git(repository, 'add', 'tracked.txt', 'skipped.txt', 'assumed.txt', '.gitignore').status,
      0,
    );
    assert.equal(git(repository, 'commit', '--quiet', '-m', 'fixture').status, 0);

    fs.writeFileSync(trackedPath, 'baseline\r\n', 'utf8');
    const futureMtime = new Date(fs.statSync(trackedPath).mtimeMs + 60_000);
    fs.utimesSync(trackedPath, futureMtime, futureMtime);
    assert.equal(
      git(repository, 'diff', '--cached', '--quiet', '--no-ext-diff', 'HEAD', '--').status,
      0,
      'the index must remain equal to HEAD after a filtered rewrite',
    );
    assert.equal(
      git(repository, 'diff', '--quiet', '--no-ext-diff', '--').status,
      0,
      'a CRLF/stat rewrite with identical filtered content must remain worktree-clean',
    );

    fs.writeFileSync(trackedPath, 'real change\r\n', 'utf8');
    assert.equal(
      git(repository, 'diff', '--cached', '--quiet', '--no-ext-diff', 'HEAD', '--').status,
      0,
    );
    assert.equal(git(repository, 'diff', '--quiet', '--no-ext-diff', '--').status, 1);
    assert.match(
      git(repository, 'diff', '--name-status', '--no-ext-diff', '--').stdout,
      /^M\s+tracked\.txt$/m,
    );

    assert.equal(git(repository, 'add', 'tracked.txt').status, 0);
    assert.equal(
      git(repository, 'diff', '--cached', '--quiet', '--no-ext-diff', 'HEAD', '--').status,
      1,
    );
    assert.equal(git(repository, 'diff', '--quiet', '--no-ext-diff', '--').status, 0);
    assert.match(
      git(
        repository,
        'diff',
        '--cached',
        '--name-status',
        '--no-ext-diff',
        'HEAD',
        '--',
      ).stdout,
      /^M\s+tracked\.txt$/m,
    );

    fs.writeFileSync(trackedPath, 'baseline\r\n', 'utf8');
    assert.equal(
      git(repository, 'diff', '--quiet', '--no-ext-diff', 'HEAD', '--').status,
      0,
      'the combined HEAD diff demonstrates the staged/worktree cancellation hazard',
    );
    assert.equal(
      git(repository, 'diff', '--cached', '--quiet', '--no-ext-diff', 'HEAD', '--').status,
      1,
      'the staged half of a cancellation must remain visible',
    );
    assert.equal(
      git(repository, 'diff', '--quiet', '--no-ext-diff', '--').status,
      1,
      'the unstaged half of a cancellation must remain visible',
    );

    assert.equal(git(repository, 'add', 'tracked.txt').status, 0);
    assert.equal(
      git(repository, 'diff', '--cached', '--quiet', '--no-ext-diff', 'HEAD', '--').status,
      0,
    );
    assert.equal(git(repository, 'diff', '--quiet', '--no-ext-diff', '--').status, 0);

    assert.equal(git(repository, 'update-index', '--skip-worktree', 'skipped.txt').status, 0);
    assert.equal(git(repository, 'update-index', '--assume-unchanged', 'assumed.txt').status, 0);
    fs.writeFileSync(skippedPath, 'hidden real change\n', 'utf8');
    fs.writeFileSync(assumedPath, 'hidden real change\n', 'utf8');
    const hiddenEntries = git(repository, 'ls-files', '-v').stdout;
    assert.match(hiddenEntries, /^S skipped\.txt$/m);
    assert.match(hiddenEntries, /^h assumed\.txt$/m);
    assert.equal(
      git(repository, 'diff', '--cached', '--quiet', '--no-ext-diff', 'HEAD', '--').status,
      0,
    );
    assert.equal(git(repository, 'diff', '--quiet', '--no-ext-diff', '--').status, 0);

    fs.writeFileSync(untrackedPath, 'must fail the untracked gate\n', 'utf8');
    fs.writeFileSync(ignoredPath, 'allowed build output\n', 'utf8');
    const untrackedEntries = git(
      repository,
      'ls-files',
      '--others',
      '--exclude-standard',
    ).stdout;
    assert.match(untrackedEntries, /^untracked\.txt$/m);
    assert.doesNotMatch(untrackedEntries, /ignored\.txt/);

    const intentPath = path.join(repository, 'intent-to-add.txt');
    fs.writeFileSync(intentPath, '', 'utf8');
    assert.equal(git(repository, 'add', '-N', 'intent-to-add.txt').status, 0);
    fs.rmSync(intentPath);
    assert.equal(
      git(repository, 'diff', '--cached', '--quiet', '--no-ext-diff', 'HEAD', '--').status,
      0,
      'Git hides a deleted intent-to-add entry unless the gate opts into visibility',
    );
    assert.equal(
      git(
        repository,
        'diff',
        '--cached',
        '--quiet',
        '--no-ext-diff',
        '--ita-visible-in-index',
        'HEAD',
        '--',
      ).status,
      1,
      'the gate must reject a deleted intent-to-add index entry',
    );
    assert.equal(git(repository, 'reset', '--', 'intent-to-add.txt').status, 0);

    const emptyRepository = fs.mkdtempSync(path.join(os.tmpdir(), 'pa-index-error-'));
    try {
      assert.equal(git(emptyRepository, 'init', '--quiet').status, 0);
      const invalidHead = git(
        emptyRepository,
        'diff',
        '--cached',
        '--quiet',
        '--no-ext-diff',
        'HEAD',
        '--',
      );
      assert.notEqual(invalidHead.status, 0);
      assert.notEqual(invalidHead.status, 1, 'Git operational failures must not look like a diff');
    } finally {
      fs.rmSync(emptyRepository, { force: true, recursive: true });
    }

    const submoduleFixture = fs.mkdtempSync(path.join(os.tmpdir(), 'pa-submodule-gate-'));
    const moduleSource = path.join(submoduleFixture, 'module-source');
    const superRepository = path.join(submoduleFixture, 'super-repository');
    try {
      fs.mkdirSync(moduleSource);
      fs.mkdirSync(superRepository);
      assert.equal(git(moduleSource, 'init', '--quiet').status, 0);
      assert.equal(git(moduleSource, 'config', 'user.name', 'Product Atelier Tests').status, 0);
      assert.equal(git(moduleSource, 'config', 'user.email', 'tests@product-atelier.invalid').status, 0);
      fs.writeFileSync(path.join(moduleSource, 'module.txt'), 'baseline\n', 'utf8');
      assert.equal(git(moduleSource, 'add', 'module.txt').status, 0);
      assert.equal(git(moduleSource, 'commit', '--quiet', '-m', 'module fixture').status, 0);

      assert.equal(git(superRepository, 'init', '--quiet').status, 0);
      assert.equal(git(superRepository, 'config', 'user.name', 'Product Atelier Tests').status, 0);
      assert.equal(git(superRepository, 'config', 'user.email', 'tests@product-atelier.invalid').status, 0);
      assert.equal(
        git(
          superRepository,
          '-c',
          'protocol.file.allow=always',
          'submodule',
          'add',
          '--quiet',
          moduleSource,
          'module',
        ).status,
        0,
      );
      assert.equal(
        git(superRepository, 'config', '-f', '.gitmodules', 'submodule.module.ignore', 'all').status,
        0,
      );
      assert.equal(git(superRepository, 'config', 'diff.ignoreSubmodules', 'all').status, 0);
      assert.equal(git(superRepository, 'add', '.gitmodules', 'module').status, 0);
      assert.equal(git(superRepository, 'commit', '--quiet', '-m', 'super fixture').status, 0);
      assert.match(
        git(superRepository, 'ls-files', '--stage').stdout,
        /^160000 [0-9a-f]{40} 0\s+module$/m,
        'the fail-closed source gate must identify and reject every gitlink',
      );

      fs.writeFileSync(path.join(superRepository, 'module', 'module.txt'), 'real change\n', 'utf8');
      assert.equal(
        git(superRepository, 'diff', '--quiet', '--no-ext-diff', '--').status,
        0,
        'repository configuration can otherwise hide a dirty submodule',
      );
      assert.equal(
        git(
          superRepository,
          'diff',
          '--quiet',
          '--no-ext-diff',
          '--ignore-submodules=none',
          '--',
        ).status,
        1,
        'the gate must override submodule ignore configuration',
      );
    } finally {
      fs.rmSync(submoduleFixture, { force: true, recursive: true });
    }
  } finally {
    fs.rmSync(repository, { force: true, recursive: true });
  }
});
