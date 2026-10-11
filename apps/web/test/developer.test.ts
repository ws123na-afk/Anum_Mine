// Unit tests for the developer-mode helpers in src/lib/developer.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  checksLabel, confirmPrompt, isApproveIntent, mentionsProtected, newlyReady, parseConfirmation, parseDeveloperCommand, pollDelay,
  readyAnnouncement, requestBody, setupSteps, statusTone, type DevRequest, type DevStatus,
} from '../src/lib/developer.ts';

const request = (id: string, status: DevStatus, extra: Partial<DevRequest> = {}): DevRequest => ({
  id, created_by: 'owner', text: 'Add a dark/light toggle to settings', source: 'text', status, allow_protected_paths: false,
  branch: `anum-dev/${id}`, run_id: null, run_url: null, pr_number: null, pr_url: null, pr_title: null, pr_draft: null, head_sha: null,
  checks: null, failure_reason: null, merge_error: null, approval_id: null, merge_commit_sha: null, created_at: '2026-10-11T09:00:00Z',
  updated_at: '2026-10-11T09:00:00Z', merged_at: null, timeline: [], summary: '', approval: null, ...extra,
});

describe('developer triggers', () => {
  test('recognises developer, build and Anum build at the start', () => {
    assert.equal(parseDeveloperCommand('developer, add a dark/light toggle to settings'), 'add a dark/light toggle to settings');
    assert.equal(parseDeveloperCommand('Build a dark/light toggle in settings'), 'a dark/light toggle in settings');
    assert.equal(parseDeveloperCommand('Anum build: show pending approvals on Tasks'), 'show pending approvals on Tasks');
    assert.equal(parseDeveloperCommand('hey anum, developer please add an export button'), 'add an export button');
  });

  test('ignores other questions and empty commands', () => {
    assert.equal(parseDeveloperCommand('What is waiting for my approval?'), null);
    assert.equal(parseDeveloperCommand('Can you rebuild the report?'), null);
    assert.equal(parseDeveloperCommand('build'), null);
    assert.equal(parseDeveloperCommand('developer: fix'), null);
  });

  test('confirmation and approval answers', () => {
    for (const yes of ['yes', 'Start', 'go ahead.', 'Do it!', 'نعم']) assert.equal(parseConfirmation(yes), 'yes', yes);
    for (const no of ['no', 'Cancel', 'not now']) assert.equal(parseConfirmation(no), 'no', no);
    assert.equal(parseConfirmation('yes and also add a footer'), null);
    assert.ok(isApproveIntent('approve'));
    assert.ok(isApproveIntent('Approve and merge.'));
    assert.ok(isApproveIntent('merge it'));
    assert.ok(!isApproveIntent('approve the budget increase for marketing'));
    assert.equal(confirmPrompt('add a toggle'), "I'll ask the developer to: add a toggle. Start?");
  });
});

describe('request body', () => {
  test('trims, checks length and refuses protected topics unless allowed', () => {
    assert.deepEqual(requestBody('  Add a dark/light toggle  '), { ok: true, value: { text: 'Add a dark/light toggle', allow_protected_paths: false } });
    assert.equal(requestBody('short').ok, false);
    assert.equal(requestBody('x'.repeat(4001)).ok, false);
    assert.equal(requestBody('Rotate the deploy secrets in GitHub Actions').ok, false);
    assert.deepEqual(requestBody('Rotate the deploy secrets in GitHub Actions', true), { ok: true, value: { text: 'Rotate the deploy secrets in GitHub Actions', allow_protected_paths: true } });
    assert.ok(!mentionsProtected('Use the shared design tokens for the toggle'));
    assert.ok(mentionsProtected('Edit .github/workflows/ci.yml'));
  });
});

describe('status helpers', () => {
  test('announces only requests that just became ready', () => {
    const before = [request('a', 'ci_running'), request('b', 'waiting_approval')];
    const after = [request('a', 'waiting_approval', { pr_title: 'Dark mode toggle' }), request('b', 'waiting_approval'), request('c', 'waiting_approval')];
    assert.deepEqual(newlyReady(null, after), []);
    assert.deepEqual(newlyReady(before, after).map((item) => item.id), ['a', 'c']);
    assert.equal(readyAnnouncement(after[0]), 'The change is ready for review: Dark mode toggle. Say approve to merge.');
  });

  test('polls while work is moving and stops when nothing is', () => {
    assert.equal(pollDelay([request('a', 'coding')]), 20_000);
    assert.equal(pollDelay([request('a', 'waiting_approval')]), 60_000);
    assert.equal(pollDelay([request('a', 'merged'), request('b', 'failed')]), null);
    assert.equal(pollDelay([]), null);
  });

  test('labels checks and tones', () => {
    assert.equal(checksLabel(null), 'No CI results yet');
    assert.equal(checksLabel({ total: 3, passed: 1, failed: 1, pending: 1, failing: ['Web and contracts'] }), '1 of 3 checks passed, 1 running, 1 failing (Web and contracts)');
    assert.equal(statusTone('merged'), 'success');
    assert.equal(statusTone('ci_failed'), 'dangerTone');
    assert.equal(statusTone('coding'), 'warning');
  });

  test('setup steps name the missing settings only', () => {
    const steps = setupSteps({ configured: false, enabled_here: false, repository: null, workflow: 'anum-developer.yml', base_branch: 'main', missing: ['ANUM_DEVELOPER_GITHUB_TOKEN'], message: '', max_requests_per_day: 10, requests_today: 0 });
    assert.equal(steps.length, 1);
    assert.ok(steps[0].startsWith('ANUM_DEVELOPER_GITHUB_TOKEN:'));
  });
});
