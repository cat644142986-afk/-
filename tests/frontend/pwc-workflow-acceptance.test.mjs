import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const contractPath = path.join(root, 'docs/contracts/pwc-workflow-acceptance-v1.json');
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));

test('PWC contract binds the audited source and formal release identities', () => {
  assert.equal(contract.contract_id, 'product-atelier-pwc-v1');
  assert.equal(contract.status, 'active');
  assert.match(contract.baseline.commit, /^[0-9a-f]{40}$/);
  assert.match(contract.formal_release.git_commit, /^[0-9a-f]{40}$/);
  assert.match(contract.formal_release.app_sha256, /^[0-9A-F]{64}$/);
  assert.match(contract.formal_release.sidecar_sha256, /^[0-9A-F]{64}$/);
  assert.equal(contract.formal_release.contract_version, '2026-09-02.4');
  assert.equal(contract.formal_release.ledger_schema_version, 8);
});

test('P0 and P1 release the feature freeze while P2 gates public distribution only', () => {
  assert.deepEqual(contract.gates.feature_freeze_release_requires, ['P0', 'P1']);
  assert.equal(contract.gates.p2_scope, 'public-distribution-only');
  assert.equal(contract.gates.p2_blocks_internal_follow_up_research_and_development, false);
  assert.deepEqual(contract.gates.promotion_sequence.slice(0, 4), [
    'Build',
    'Test',
    'Package',
    'Smoke',
  ]);
  assert.deepEqual(contract.gates.promotion_sequence.slice(-3), [
    'Validated',
    'Promote',
    'Update Desktop Entry',
  ]);
});

test('PWC-2 is gated by business-value durable and ephemeral state classification', () => {
  const policy = contract.state_policy;
  assert.match(policy.pwc2_entry_requirement, /Before PWC-2 implementation/);
  assert.ok(policy.durable_when_any.length >= 3);
  assert.ok(policy.ephemeral_when_all.length >= 3);
  assert.ok(policy.mandatory_durable_families.includes('paid-call authorization and provider-attempt receipt'));
  assert.ok(policy.default_ephemeral_examples.includes('hover and focus state'));
  assert.ok(policy.rules.includes('Do not persist UI state merely because it exists.'));
});

test('paid work requires traceable bounded authorization without mandatory modal prompts', () => {
  const policy = contract.paid_call_policy;
  assert.equal(policy.authorization_must_be_traceable_to_explicit_user_action, true);
  assert.equal(policy.per_call_confirmation_modal_required, false);
  assert.equal(policy.bounded_authorization_may_cover_multiple_calls, true);
  assert.equal(policy.hidden_paid_calls_allowed, false);
  assert.equal(policy.automatic_paid_retry_after_failure_allowed, false);
  assert.ok(policy.requirements.some((item) => /Every provider attempt references/.test(item)));
  assert.ok(policy.requirements.some((item) => /billing-unknown/.test(item)));
});

test('workflow coverage follows product boundaries instead of an arbitrary journey count', () => {
  const workflows = contract.workflows;
  assert.deepEqual(workflows.map((item) => item.id), [
    'core-image-iteration',
    'canvas-fine-edit-roundtrip',
    'failure-and-recovery',
    'exposed-multi-source-workflows',
    'profile-and-governed-learning',
  ]);
  assert.deepEqual(
    workflows.filter((item) => item.priority === 'P0').map((item) => item.id),
    ['core-image-iteration', 'canvas-fine-edit-roundtrip', 'failure-and-recovery'],
  );
  assert.deepEqual(
    workflows.filter((item) => item.priority === 'P1').map((item) => item.id),
    ['exposed-multi-source-workflows', 'profile-and-governed-learning'],
  );
  for (const workflow of workflows) {
    assert.ok(workflow.entry);
    assert.ok(workflow.exit);
    assert.ok(workflow.acceptance.length >= 3);
    assert.ok(workflow.evidence.includes('automated'));
    assert.ok(workflow.evidence.includes('packaged_manual'));
  }
});

test('P1 multi-source recovery separates local retry from new paid authorization', () => {
  const workflow = contract.workflows.find((item) => item.id === 'exposed-multi-source-workflows');
  const rule = workflow.acceptance.find((item) => /Local-only failed items/.test(item));
  assert.match(rule, /retry in place/);
  assert.match(rule, /new explicitly authorized task/);
  assert.match(rule, /never reuse the original authorization/);
});

test('P1 profile and governed learning freeze real execution inputs', () => {
  const workflow = contract.workflows.find((item) => item.id === 'profile-and-governed-learning');
  assert.ok(workflow.acceptance.some((item) => /SKU facts and protection constraints/.test(item)));
  assert.ok(workflow.acceptance.some((item) => /frozen when the task is created/.test(item)));
});

test('PWC-1 implementation follows the dependency-ordered recovery plan', () => {
  const plan = contract.pwc1_plan;
  assert.equal(plan.implementation_started, true);
  assert.equal(plan.estimated_engineering_days, '3-4');
  assert.deepEqual(plan.tasks.map((item) => item.id), [
    'PWC1-1',
    'PWC1-2',
    'PWC1-3',
    'PWC1-4',
    'PWC1-5',
  ]);
  const known = new Set();
  for (const task of plan.tasks) {
    assert.ok(task.title);
    assert.ok(task.deliverable);
    assert.ok(task.estimate_days > 0);
    for (const dependency of task.depends_on) assert.ok(known.has(dependency));
    known.add(task.id);
  }
});
