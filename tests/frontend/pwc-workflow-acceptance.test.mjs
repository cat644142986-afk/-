import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const contractPath = path.join(root, 'docs/contracts/pwc-workflow-acceptance-v1.json');
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));

test('PWC contract binds the audited source and formal release identities', () => {
  assert.equal(contract.contract_id, 'product-atelier-pwc-v1');
  assert.equal(contract.status, 'p1_closed');
  assert.match(contract.baseline.role, /historical audit starting point/);
  assert.match(contract.baseline.commit, /^[0-9a-f]{40}$/);
  assert.equal(contract.formal_release.git_commit, 'ba4671b76ff3de54c1f99d5d6fa02018c83e3511');
  assert.match(contract.formal_release.app_sha256, /^[0-9A-F]{64}$/);
  assert.match(contract.formal_release.sidecar_sha256, /^[0-9A-F]{64}$/);
  assert.match(contract.formal_release.manifest_sha256, /^[0-9A-F]{64}$/);
  assert.match(contract.formal_release.tree_sha256, /^[0-9A-F]{64}$/);
  assert.match(contract.formal_release.candidate_identity_sha256, /^[0-9A-F]{64}$/);
  assert.equal(contract.formal_release.contract_version, '2026-09-02.4');
  assert.equal(contract.formal_release.ledger_schema_version, 9);
});

test('P1 closure is additive, hash-bound, and leaves P2 non-blocking', () => {
  const closure = contract.p1_closure;
  const receiptPath = path.join(root, closure.closure_receipt.path);
  const receiptBytes = fs.readFileSync(receiptPath);
  const receipt = JSON.parse(receiptBytes.toString('utf8'));
  const receiptSha256 = createHash('sha256').update(receiptBytes).digest('hex').toUpperCase();

  assert.equal(closure.status, 'closed');
  assert.equal(closure.product_release_git_commit, contract.formal_release.git_commit);
  assert.equal(closure.closure_receipt.status, 'validated');
  assert.equal(receiptSha256, closure.closure_receipt.sha256);
  assert.equal(receipt.kind, 'product-atelier-p1-closure-validation');
  assert.equal(receipt.status, 'validated');
  assert.equal(receipt.product_release.git_commit, contract.formal_release.git_commit);
  assert.equal(receipt.product_release.tree_sha256, contract.formal_release.tree_sha256);
  assert.equal(receipt.closure_decision.p1, 'closed');
  assert.equal(receipt.closure_decision.real_implementation_gap_found, false);
  assert.equal(receipt.closure_decision.provider_calls_during_closure, 0);
  assert.equal(receipt.closure_decision.product_runtime_code_changed, false);
  assert.equal(receipt.closure_decision.formal_package_changed, false);
  assert.equal(receipt.closure_decision.p2_public_signed_distribution, 'open_non_blocking');
  assert.equal(closure.p2_public_signed_distribution, 'open_non_blocking');
  assert.match(closure.closure_method, /sealed base Validated receipt remains immutable/);
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
    assert.equal(workflow.status, 'closed');
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
  assert.equal(plan.status, 'completed');
  assert.equal(plan.historical, true);
  assert.equal(plan.implementation_started, true);
  assert.equal(plan.implementation_completed, true);
  assert.equal(plan.superseded_for_status_tracking_by, 'p1_closure');
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
