import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const prototypeRoot = path.join(root, 'prototypes', 'experience-shell2');
const html = fs.readFileSync(path.join(prototypeRoot, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(prototypeRoot, 'prototype.css'), 'utf8');
const js = fs.readFileSync(path.join(prototypeRoot, 'prototype.js'), 'utf8');

test('experience shell prototype follows the latest capsule navigation decision', () => {
  assert.match(html, /rail-capsule rail-primary/);
  assert.match(html, /rail-capsule rail-utility/);
  assert.match(html, /rail-capsule rail-profile/);
  assert.match(html, /aria-label="退出应用（原型不执行）"/);
  assert.match(css, /--radius-shell:\s*36px/);
  assert.match(css, /\.rail-button\.is-active[\s\S]*border-radius:\s*50%/);
  assert.doesNotMatch(html, />Studio<\/button>/);
});

test('prototype exposes Chinese-first studio, review, knowledge, task, and settings states', () => {
  for (const copy of [
    '创作工作台',
    '会话与项目',
    '成长与知识',
    '为什么这样设计',
    '只重试失败项',
    '后台资源已释放',
    '选择终稿，也告诉系统为什么',
    'D:\\知识库',
    '自定义交付位置',
  ]) {
    assert.ok(html.includes(copy), `missing prototype copy: ${copy}`);
  }
});

test('knowledge motion remains semantic, cancellable, and reduced-motion aware', () => {
  assert.match(html, /class="dna-map"/);
  assert.match(html, /data-knowledge-action="approve"/);
  assert.match(html, /形成待审核建议/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /transform:\s*scaleX\(var\(--progress\)\)/);
  assert.match(js, /clearGenerationTimers/);
  assert.match(js, /motionReduced\.matches/);
  assert.match(js, /成功项目不会重复执行/);
});

test('icon-only controls keep accessible Chinese names and overlay focus handling', () => {
  assert.match(html, /aria-label="创作工作台"/);
  assert.match(html, /aria-label="打开任务中心"/);
  assert.match(html, /aria-label="成长与知识"/);
  assert.match(js, /event\.key === 'Escape'/);
  assert.match(js, /event\.key !== 'Tab'/);
  assert.match(js, /button svg'[\s\S]*aria-hidden/);
  assert.match(css, /button:focus-visible/);
});
