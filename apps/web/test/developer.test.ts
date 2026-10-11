// Unit tests for the developer-mode helpers in src/lib/developer.ts, in English and Arabic.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import {
  busyProjects, canInstallWorkflow, checksLabel, confirmPrompt, defaultProjectId, draftFromProject, emptyProjectDraft, filterProjects,
  isApproveIntent, matchProjectAnswer, mentionsProtected, newlyReady, orList, parseConfirmation, parseDeveloperCommand, parseProtectedPaths,
  parseRepository, pollDelay, projectPayload, projectState, readyAnnouncement, requestBody, sendableProjects, setupSteps, statusLabel,
  statusTone, summaryText, whichProjectPrompt, type DevProject, type DevRequest, type DevStatus, type ProjectRef,
} from '../src/lib/developer.ts';
import { FSI, PDI, setLocale } from '../src/lib/i18n.ts';

afterEach(() => setLocale('en'));

const request = (id: string, status: DevStatus, extra: Partial<DevRequest> = {}): DevRequest => ({
  id, created_by: 'owner', text: 'Add a dark/light toggle to settings', source: 'text', status, allow_protected_paths: false,
  branch: `anum-dev/${id}`, project_id: 'devproj_anum', project_name: 'ANUM', repository: 'acme/anum', run_id: null, run_url: null,
  pr_number: null, pr_url: null, pr_title: null, pr_draft: null, head_sha: null, checks: null, failure_reason: null, merge_error: null,
  approval_id: null, merge_commit_sha: null, created_at: '2026-10-11T09:00:00Z', updated_at: '2026-10-11T09:00:00Z', merged_at: null,
  timeline: [], summary: '', approval: null, ...extra,
});

const project = (id: string, name: string, extra: Partial<DevProject> = {}): DevProject => ({
  id, name, github_owner: 'acme', github_repo: name.toLowerCase(), repository: `acme/${name.toLowerCase()}`, default_branch: 'main',
  description: '', enabled: true, protected_paths: [], workflow_file: 'anum-developer.yml', token_source: 'project', token_configured: true,
  token_updated_at: null, last_check: null, install_pr_number: null, install_pr_url: null, ready: true, problems: [], created_by: 'owner',
  created_at: '2026-10-11T09:00:00Z', updated_at: '2026-10-11T09:00:00Z', ...extra,
});

const ref = (name: string, repository = `acme/${name.toLowerCase()}`): ProjectRef => ({ id: `devproj_${name.toLowerCase()}`, name, repository, description: '' });

describe('developer triggers', () => {
  test('recognises developer, build, Anum build and "ANUM, in <project>" at the start', () => {
    assert.equal(parseDeveloperCommand('developer, add a dark/light toggle to settings'), 'add a dark/light toggle to settings');
    assert.equal(parseDeveloperCommand('Build a dark/light toggle in settings'), 'a dark/light toggle in settings');
    assert.equal(parseDeveloperCommand('Anum build: show pending approvals on Tasks'), 'show pending approvals on Tasks');
    assert.equal(parseDeveloperCommand('hey anum, developer please add an export button'), 'add an export button');
    assert.equal(parseDeveloperCommand('build a login page in TyrePulse'), 'a login page in TyrePulse');
    assert.equal(parseDeveloperCommand('ANUM, in TyrePulse add a fleet export'), 'in TyrePulse add a fleet export');
  });

  test('ignores other questions and empty commands', () => {
    assert.equal(parseDeveloperCommand('What is waiting for my approval?'), null);
    assert.equal(parseDeveloperCommand('Can you rebuild the report?'), null);
    assert.equal(parseDeveloperCommand('build'), null);
    assert.equal(parseDeveloperCommand('developer: fix'), null);
    assert.equal(parseDeveloperCommand('Anum, what is the weather in Riyadh'), null);
  });

  test('confirmation and approval answers', () => {
    for (const yes of ['yes', 'Start', 'go ahead.', 'Do it!', 'نعم']) assert.equal(parseConfirmation(yes), 'yes', yes);
    for (const no of ['no', 'Cancel', 'not now']) assert.equal(parseConfirmation(no), 'no', no);
    assert.equal(parseConfirmation('yes and also add a footer'), null);
    assert.ok(isApproveIntent('approve'));
    assert.ok(isApproveIntent('Approve and merge.'));
    assert.ok(isApproveIntent('merge it'));
    assert.ok(!isApproveIntent('approve the budget increase for marketing'));
  });

  test('always names the project in the confirmation, and asks which one when unclear', () => {
    assert.equal(confirmPrompt('add a login page', 'TyrePulse'), "I’ll ask the developer to: add a login page in TyrePulse. Start?");
    assert.equal(whichProjectPrompt([ref('ANUM'), ref('TyrePulse')]), 'Which project is this for: ANUM or TyrePulse?');
    assert.equal(orList(['A', 'B', 'C']), 'A, B or C');
  });

  test('matches a spoken project answer among the candidates only when it is unambiguous', () => {
    const candidates = [ref('ANUM'), ref('TyrePulse'), ref('Tyre Shop', 'acme/tyre-shop')];
    assert.equal(matchProjectAnswer('TyrePulse', candidates)?.name, 'TyrePulse');
    assert.equal(matchProjectAnswer('the tyrepulse one please', candidates)?.name, 'TyrePulse');
    assert.equal(matchProjectAnswer('Tyre Pulse', candidates)?.name, 'TyrePulse');
    assert.equal(matchProjectAnswer('anum.', candidates)?.name, 'ANUM');
    assert.equal(matchProjectAnswer('tyre-shop', candidates)?.name, 'Tyre Shop');
    assert.equal(matchProjectAnswer('tyre', candidates), null);
    assert.equal(matchProjectAnswer('something else', candidates), null);
    assert.equal(matchProjectAnswer('', candidates), null);
  });
});

describe('request body', () => {
  test('trims, checks length and project, and refuses protected topics unless allowed', () => {
    assert.deepEqual(requestBody('  Add a dark/light toggle  ', false, 'devproj_a'), { ok: true, value: { text: 'Add a dark/light toggle', allow_protected_paths: false, project_id: 'devproj_a' } });
    assert.deepEqual(requestBody('  Add a dark/light toggle  '), { ok: true, value: { text: 'Add a dark/light toggle', allow_protected_paths: false } });
    assert.deepEqual(requestBody('Add a dark/light toggle', false, ''), { ok: false, error: 'Choose the project this request is for.' });
    assert.equal(requestBody('short').ok, false);
    assert.equal(requestBody('x'.repeat(4001)).ok, false);
    assert.equal(requestBody('Rotate the deploy secrets in GitHub Actions').ok, false);
    assert.equal(requestBody('Rotate the deploy secrets in GitHub Actions', true).ok, true);
    assert.ok(!mentionsProtected('Use the shared design tokens for the toggle'));
    assert.ok(mentionsProtected('Edit .github/workflows/ci.yml'));
  });
});

describe('status helpers', () => {
  test('announces only requests that just became ready, with the project', () => {
    const before = [request('a', 'ci_running'), request('b', 'waiting_approval')];
    const after = [request('a', 'waiting_approval', { pr_title: 'Dark mode toggle' }), request('b', 'waiting_approval'), request('c', 'waiting_approval')];
    assert.deepEqual(newlyReady(null, after), []);
    assert.deepEqual(newlyReady(before, after).map((item) => item.id), ['a', 'c']);
    assert.equal(readyAnnouncement(after[0]), 'The change is ready for review in ANUM: Dark mode toggle. Say approve to merge.');
    assert.equal(readyAnnouncement({ ...after[0], project_name: null }), 'The change is ready for review: Dark mode toggle. Say approve to merge.');
  });

  test('polls while work is moving and stops when nothing is', () => {
    assert.equal(pollDelay([request('a', 'coding')]), 20_000);
    assert.equal(pollDelay([request('a', 'waiting_approval')]), 60_000);
    assert.equal(pollDelay([request('a', 'merged'), request('b', 'failed')]), null);
    assert.equal(pollDelay([]), null);
  });

  test('one active request per project', () => {
    const busy = busyProjects([request('a', 'coding'), request('b', 'queued', { project_id: 'devproj_b' }), request('c', 'merged', { project_id: 'devproj_c' })]);
    assert.deepEqual([...busy].sort(), ['devproj_anum', 'devproj_b']);
  });

  test('labels checks, statuses and summaries', () => {
    assert.equal(checksLabel(null), 'No CI results yet');
    assert.equal(checksLabel({ total: 3, passed: 1, failed: 1, pending: 1, failing: ['Web and contracts'] }), '1 of 3 checks passed, 1 running, 1 failing (Web and contracts)');
    assert.equal(statusTone('merged'), 'success');
    assert.equal(statusTone('ci_failed'), 'dangerTone');
    assert.equal(statusTone('coding'), 'warning');
    assert.equal(statusLabel('waiting_approval'), 'Waiting for approval');
    assert.equal(summaryText(request('a', 'ci_failed', { pr_title: 'Toggle', checks: { total: 2, passed: 1, failed: 1, pending: 0, failing: ['Web'] } })), 'CI failed on Toggle (Web).');
    assert.equal(summaryText(request('a', 'failed', { failure_reason: 'Refused: protected paths.' })), 'Refused: protected paths.');
  });

  test('setup steps name the missing settings only', () => {
    const steps = setupSteps({ configured: false, enabled_here: false, repository: null, workflow: 'anum-developer.yml', base_branch: 'main', missing: ['ANUM_DEVELOPER_TENANT_ID', 'SOMETHING_NEW'], message: '', max_requests_per_day: 10, requests_today: 0 });
    assert.deepEqual(steps.map((step) => step.name), ['ANUM_DEVELOPER_TENANT_ID', 'SOMETHING_NEW']);
    assert.equal(steps[0].purpose, 'the organization allowed to use developer mode.');
    assert.equal(steps[1].purpose, 'see docs/developer-mode.md.');
  });
});

describe('projects', () => {
  test('parses a repository from owner/repo or a GitHub address', () => {
    assert.deepEqual(parseRepository('acme/tyrepulse'), { owner: 'acme', repo: 'tyrepulse' });
    assert.deepEqual(parseRepository(' https://github.com/acme/tyre-pulse.git '), { owner: 'acme', repo: 'tyre-pulse' });
    assert.deepEqual(parseRepository('git@github.com:acme/app.js.git'), { owner: 'acme', repo: 'app.js' });
    assert.equal(parseRepository('acme'), null);
    assert.equal(parseRepository('acme/../x'), null);
    assert.equal(parseRepository('-acme/x'), null);
  });

  test('builds the payload from a draft and says which field is wrong', () => {
    const draft = { ...emptyProjectDraft(), name: '  Tyre  Pulse ', repository: 'https://github.com/acme/tyrepulse', description: 'Fleet tyres', protected_paths: 'docs/legal/\n./billing.py, billing.py', token: 'example-fine-grained-value' };
    const result = projectPayload(draft);
    assert.ok(result.ok);
    assert.deepEqual(result.value, {
      name: 'Tyre Pulse', github_owner: 'acme', github_repo: 'tyrepulse', default_branch: 'main', description: 'Fleet tyres',
      protected_paths: ['docs/legal/', 'billing.py'], enabled: true, token_source: 'project', workflow_file: 'anum-developer.yml', token: 'example-fine-grained-value',
    });
    const environment = projectPayload({ ...draft, token_source: 'environment' });
    assert.ok(environment.ok && !('token' in environment.value));
    for (const [change, field] of [
      [{ name: ' ' }, 'name'], [{ repository: 'nope' }, 'repository'], [{ default_branch: 'a..b' }, 'default_branch'],
      [{ protected_paths: 'src/*.py' }, 'protected_paths'], [{ protected_paths: '../etc' }, 'protected_paths'], [{ token: 'short' }, 'token'],
      [{ workflow_file: 'ci.json' }, 'workflow_file'],
    ] as const) {
      const bad = projectPayload({ ...draft, ...change });
      assert.ok(!bad.ok && bad.field === field, field);
    }
    assert.deepEqual(parseProtectedPaths(' a/ \n\n b ,a/'), ['a/', 'b']);
  });

  test('editing keeps the stored token unless a new one is typed or removal is ticked', () => {
    const existing = project('devproj_t', 'TyrePulse', { protected_paths: ['docs/'] });
    const draft = draftFromProject(existing);
    assert.equal(draft.token, '');
    const kept = projectPayload(draft);
    assert.ok(kept.ok && !('token' in kept.value) && !('clear_token' in kept.value));
    const cleared = projectPayload({ ...draft, clear_token: true });
    assert.ok(cleared.ok && cleared.value.clear_token === true);
  });

  test('states, install offer, default selection and filtering', () => {
    const ready = project('a', 'ANUM', { last_check: { ok: true, checked_at: '2026-10-11T09:00:00Z', repository_reachable: true, default_branch_exists: true, workflow_present: true, actions_readable: true, github_default_branch: 'main', message: 'Ready' } });
    const untested = project('b', 'TyrePulse', { description: 'Fleet tyres' });
    const off = project('c', 'Old', { enabled: false, ready: false });
    const broken = project('d', 'Broken', { ready: false, problems: ['No token'], token_configured: false });
    assert.deepEqual([ready, untested, off, broken].map(projectState), ['ready', 'untested', 'off', 'attention']);
    assert.equal(canInstallWorkflow(ready), false);
    assert.equal(canInstallWorkflow(untested), true);
    assert.equal(canInstallWorkflow(broken), false);
    assert.deepEqual(sendableProjects([ready, untested, off, broken]).map((item) => item.id), ['a', 'b']);
    assert.equal(defaultProjectId([ready, off], null), 'a');
    assert.equal(defaultProjectId([ready, untested], 'b'), 'b');
    assert.equal(defaultProjectId([ready, untested], 'gone'), '');
    assert.deepEqual(filterProjects([ready, untested], 'fleet').map((item) => item.id), ['b']);
    assert.deepEqual(filterProjects([ready, untested], '').length, 2);
  });
});

describe('Arabic', () => {
  test('confirmation, project question and statuses read in Arabic with Latin names isolated', () => {
    setLocale('ar');
    assert.equal(confirmPrompt('add a login page', 'TyrePulse'), `سأطلب من المطوّر: ${FSI}add a login page${PDI} في ${FSI}TyrePulse${PDI}. هل أبدأ؟`);
    assert.equal(whichProjectPrompt([ref('ANUM'), ref('TyrePulse')]), `لأي مشروع هذا الطلب: ${FSI}${FSI}ANUM${PDI} أو ${FSI}TyrePulse${PDI}${PDI}؟`);
    assert.equal(statusLabel('merged'), 'مدموج');
    assert.equal(checksLabel(null), 'لا نتائج CI بعد');
    assert.equal(requestBody('short').ok ? '' : (requestBody('short') as { error: string }).error, 'صِف التغيير في 10 أحرف على الأقل.');
    assert.equal(projectPayload({ ...emptyProjectDraft(), name: 'x', repository: 'bad' }).ok ? '' : 'error', 'error');
  });
});
