import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// What ANUM learned (docs/learning.md): the switch, the style profile, learned preferences, routines
// that become paused automations, weekly improvement notes and the feedback list, plus the
// Assistant's Helpful / Not helpful / Edit to teach. The API is mocked and keeps state.
const now = '2026-10-11T09:00:00.000Z';
const style = (samples = 3) => ({ tone: 'formal', length: 'short', target_words: 24, greeting: 'Dear', sign_off: 'Best regards', language: 'en', contact_languages: { 'supplier@example.com': 'ar' }, emoji: false, edit_length_ratio: 0.7, samples });

interface Options {
  overview?: number;
  state?: string;
  empty?: boolean;
  member?: boolean;
  signals?: number;
}

interface Sent { ratings: unknown[]; puts: unknown[]; prefs: unknown[]; prefUpdates: unknown[]; deletes: string[]; detects: unknown[]; routineActions: string[]; applied: string[]; feedback: unknown[]; signalQueries: string[]; settings: unknown[] }

async function mockApi(page: Page, options: Options = {}): Promise<Sent> {
  const sent: Sent = { ratings: [], puts: [], prefs: [], prefUpdates: [], deletes: [], detects: [], routineActions: [], applied: [], feedback: [], signalQueries: [], settings: [] };
  let overviewFailures = options.overview === 500 ? 1 : 0;
  let enabled = true;
  let overrides: Record<string, unknown> = {};
  let preferences = options.empty ? [] : [{ id: 'pref_1', scope: 'surface:whatsapp', text: 'Reply to customers in Arabic.', source: 'user', suggestion_id: null, created_at: now, updated_at: now }];
  const routine = {
    id: 'routine_sunday', fingerprint: 'weekly', status: 'proposed', title: 'Every Sunday around 09:00: Check Amazon orders, then Email the supplier',
    cadence: 'weekly', cron: '0 9 * * 0', timezone: 'Asia/Riyadh', occurrences: 4, weeks_observed: 4, confidence: 1, last_seen_at: now, workflow_id: null as string | null, schedule_id: null as string | null, detected_at: now, decided_at: null,
    steps: [{ key: 'a', label: 'Check Amazon orders', prompt: 'Check Amazon orders', source: 'task' }, { key: 'b', label: 'Email the supplier about restock', prompt: 'Email the supplier about restock', source: 'task' }],
  };
  let routines = options.empty ? [] : [routine];
  const suggestions = options.empty ? [] : [
    { id: 'suggestion_rule', week: '2026-W41', fingerprint: 'a', rule: 'approval_rejections', kind: 'instruction', title: 'You rejected external.action 2 times', body: 'Applying adds this instruction: Confirm the recipient first.', target: 'tool:external.action', change: { type: 'preference', scope: 'tool:external.action', text: 'Confirm the recipient first.' }, evidence: {}, status: 'open', created_at: now, updated_at: now, decided_at: null, needs_permission: null },
    { id: 'suggestion_autonomy', week: '2026-W41', fingerprint: 'b', rule: 'autonomy_loosen', kind: 'autonomy', title: 'Let medium-risk actions run without asking', body: 'You approved all 6 medium-risk actions in the last four weeks.', target: 'approval_policy:medium_risk', change: { type: 'approval_policy', medium_risk_requires_approval: false }, evidence: {}, status: 'open', created_at: now, updated_at: now, decided_at: null, needs_permission: options.member ? 'policy:manage' : null },
  ];
  const note = { week: '2026-W41', period_start: now, period_end: now, generated_at: now, summary: { ratings_up: 2, ratings_down: 1, drafts_accepted: 0, drafts_edited: 3, approvals_approved: 6, approvals_rejected: 2, retries: 1, run_failures: 0 } };
  const longText = `Dear supplier,\n${'The order of 40 Michelin tyres is late. '.repeat(30)}\nBest regards`;
  let signals = Array.from({ length: options.empty ? 0 : options.signals ?? 3 }, (_, index) => index === 0
    ? { id: 'signal_0', kind: 'draft_edited', surface: 'email', subject_id: null, skill: null, tool: null, contact: 'supplier@example.com', language: 'en', original_text: 'Hello, the order is late.', final_text: longText.slice(0, 500), reason: null, metadata: { edit: { words_before: 5, words_after: 90, words_added: 85, words_removed: 1, changed_ratio: 0.5, greeting_before: 'Hello', greeting_after: 'Dear', sign_off_before: null, sign_off_after: 'Best regards' } }, created_at: now, truncated: true }
    : { id: `signal_${index}`, kind: index % 2 ? 'rating_up' : 'approval_rejected', surface: index % 2 ? 'chat' : 'approval', subject_id: `turn_${index}`, skill: null, tool: index % 2 ? null : 'external.action', contact: null, language: null, original_text: null, final_text: null, reason: index % 2 ? null : 'Wrong buyer', metadata: {}, created_at: now, truncated: false });
  const overview = () => ({
    state: options.state ?? (enabled ? 'on' : 'off_by_user'),
    counts: { signals: { draft_edited: 1, rating_up: Math.max(0, signals.length - 1) }, preferences: preferences.length, routines_proposed: routines.filter((r) => r.status === 'proposed').length, suggestions_open: suggestions.filter((s) => s.status === 'open').length },
    profile: { enabled, derived: style(options.empty ? 0 : 3), overrides, effective: style(), derived_at: options.empty ? null : now, updated_at: now },
    can_manage_automations: !options.member, can_apply_autonomy: !options.member,
  });

  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'owner', active: true }]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.member ? 'member' : 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/learning') {
      if (options.overview === 403) return json({ error: { code: 'forbidden', message: 'Permission denied' } }, 403);
      if (overviewFailures > 0) { overviewFailures -= 1; return json({ error: { code: 'internal_error', message: 'Database unavailable' } }, 500); }
      return json(overview());
    }
    if (path === '/api/v1/learning/settings') { const body = request.postDataJSON() as { enabled: boolean }; sent.settings.push(body); enabled = body.enabled; return json(overview()); }
    if (path === '/api/v1/learning/profile' && method === 'PUT') { overrides = request.postDataJSON(); sent.puts.push(overrides); return json(overview().profile); }
    if (path === '/api/v1/learning/profile/reset') return json({ profile: overview().profile, signals_deleted: 1 });
    if (path === '/api/v1/learning/preferences' && method === 'GET') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const found = preferences.filter((p) => !q || p.text.toLowerCase().includes(q));
      return json({ items: found, total: found.length, limit: 10, offset: 0 });
    }
    if (path === '/api/v1/learning/preferences' && method === 'POST') {
      const body = request.postDataJSON() as { scope: string; text: string };
      sent.prefs.push(body);
      preferences = [{ id: `pref_${preferences.length + 2}`, ...body, source: 'user', suggestion_id: null, created_at: now, updated_at: now }, ...preferences];
      return json(preferences[0], 201);
    }
    const prefMatch = path.match(/^\/api\/v1\/learning\/preferences\/(.+)$/);
    if (prefMatch && method === 'PUT') { const body = request.postDataJSON() as { scope: string; text: string }; sent.prefUpdates.push(body); preferences = preferences.map((p) => p.id === prefMatch[1] ? { ...p, ...body } : p); return json(preferences.find((p) => p.id === prefMatch[1])); }
    if (prefMatch && method === 'DELETE') { sent.deletes.push(prefMatch[1]); preferences = preferences.filter((p) => p.id !== prefMatch[1]); return route.fulfill({ status: 204 }); }
    if (path === '/api/v1/learning/routines' && method === 'GET') return json({ items: routines, detected: 0, state: 'on' });
    if (path === '/api/v1/learning/routines/detect') { sent.detects.push(request.postDataJSON()); if (!routines.length) routines = [routine]; return json({ items: routines, detected: 1, state: 'on' }); }
    const routineMatch = path.match(/^\/api\/v1\/learning\/routines\/([^/]+)\/(accept|confirm|dismiss)$/);
    if (routineMatch) {
      sent.routineActions.push(routineMatch[2]);
      if (options.member) return json({ error: { code: 'forbidden', message: 'Missing permission automation:manage' } }, 403);
      const status = { accept: 'accepted', confirm: 'confirmed', dismiss: 'dismissed' }[routineMatch[2] as 'accept'];
      routines = routines.map((r) => ({ ...r, status, workflow_id: 'workflow_1', schedule_id: 'schedule_1' }));
      return json(routines[0]);
    }
    if (path === '/api/v1/learning/notes') return json({ items: suggestions.length || !options.empty ? [{ note, suggestions }] : [], total: options.empty ? 0 : 1, state: 'on' });
    if (path === '/api/v1/learning/notes/refresh') return json({ note, suggestions });
    const suggestionMatch = path.match(/^\/api\/v1\/learning\/suggestions\/([^/]+)\/(apply|dismiss)$/);
    if (suggestionMatch) {
      sent.applied.push(`${suggestionMatch[2]}:${suggestionMatch[1]}`);
      const target = suggestions.find((s) => s.id === suggestionMatch[1])!;
      if (target.needs_permission && suggestionMatch[2] === 'apply') return json({ error: { code: 'forbidden', message: 'Missing permission policy:manage' } }, 403);
      target.status = suggestionMatch[2] === 'apply' ? 'applied' : 'dismissed';
      if (target.change.type === 'preference' && suggestionMatch[2] === 'apply') preferences = [{ id: 'pref_s', scope: target.change.scope, text: target.change.text, source: 'suggestion', suggestion_id: target.id, created_at: now, updated_at: now }, ...preferences];
      return json(target);
    }
    if (path === '/api/v1/learning/signals' && method === 'GET') {
      sent.signalQueries.push(url.search);
      const q = (url.searchParams.get('q') ?? '').toLowerCase(), kind = url.searchParams.get('kind') ?? '';
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 10);
      const found = signals.filter((s) => (!kind || s.kind === kind) && (!q || JSON.stringify(s).toLowerCase().includes(q)));
      return json({ items: found.slice(offset, offset + limit), total: found.length, limit, offset });
    }
    const signalMatch = path.match(/^\/api\/v1\/learning\/signals\/(.+)$/);
    if (signalMatch && method === 'GET') return json({ ...signals.find((s) => s.id === signalMatch[1]), final_text: longText, truncated: false });
    if (signalMatch && method === 'DELETE') { sent.deletes.push(signalMatch[1]); signals = signals.filter((s) => s.id !== signalMatch[1]); return route.fulfill({ status: 204 }); }
    // The Assistant, for answer feedback.
    if (path === '/api/v1/conversations' && method === 'GET') return json([{ id: 'conv_1', title: 'Order status', created_at: now, updated_at: now }]);
    if (path === '/api/v1/conversations/conv_1') {
      return json({ id: 'conv_1', title: 'Order status', created_at: now, updated_at: now, turns: [
        { id: 't1', conversation_id: 'conv_1', role: 'user', content: 'Where is order 42?', status: 'complete', created_at: now },
        { id: 't2', conversation_id: 'conv_1', role: 'assistant', content: 'Order 42 ships on Sunday from the Riyadh warehouse.', status: 'complete', created_at: now },
      ] });
    }
    if (path === '/api/v1/learning/ratings') return json({ ratings: [] });
    // Thumbs are stored on the answer; the server forwards them to learning while it is on.
    if (path === '/api/v1/conversations/conv_1/turns/t2/feedback' && method === 'PUT') {
      const body = request.postDataJSON() as { rating: string | null; reason?: string };
      sent.ratings.push(body);
      const off = options.state === 'off_by_user';
      return json({ turn: { id: 't2', feedback: body.rating }, learning_recorded: !off && body.rating !== null, learning_message: off ? 'Learning is off. Turn on Learn from my activity to use this.' : null });
    }
    if (path === '/api/v1/learning/feedback') {
      const body = request.postDataJSON();
      sent.feedback.push(body);
      if (options.state === 'off_by_user') return json({ recorded: false, state: 'off_by_user', signal: null, message: 'Learning is off. Turn on Learn from my activity to use this.' });
      return json({ recorded: true, state: 'on', signal: signals[0] ?? null, message: null });
    }
    return route.abort();
  });
  return sent;
}

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('the main path: switch, style, preferences, routine to paused automation, a suggestion and the feedback list', async ({ page }) => {
  const sent = await mockApi(page);
  await page.goto('/#learning');
  await expect(page.getByRole('heading', { level: 1, name: 'What ANUM learned' })).toBeVisible();
  const state = page.getByRole('region', { name: 'Learning from your activity' });
  await expect(state.getByTestId('learning-state')).toHaveText('Learning');

  // The off switch, and back on.
  const toggle = state.getByRole('checkbox', { name: 'Learn from my activity' });
  await toggle.uncheck();
  await expect(state.getByText('Learning is off. Nothing new will be learned.')).toBeVisible();
  await expect(state.getByTestId('learning-state')).toHaveText('Off');
  await toggle.check();
  await expect.poll(() => sent.settings).toEqual([{ enabled: false }, { enabled: true }]);

  // Style: what was learned, then the user's own choices.
  const styleCard = page.getByRole('region', { name: 'Your writing style' });
  await expect(styleCard.getByText('Learned from 3 drafts you sent or edited.', { exact: false })).toBeVisible();
  await expect(styleCard.getByText('Learned: Formal')).toBeVisible();
  await styleCard.getByLabel('Tone').selectOption('casual');
  await styleCard.getByRole('checkbox', { name: 'No sign-off' }).check();
  await styleCard.getByRole('button', { name: 'Add a contact' }).click();
  await styleCard.getByRole('textbox', { name: 'Email address or phone number' }).fill('+966500000000');
  await styleCard.getByRole('button', { name: 'Save my style' }).click();
  await expect(styleCard.getByText('Saved. Drafts use your style from now on.')).toBeVisible();
  expect(sent.puts).toEqual([{ tone: 'casual', length: null, greeting: null, sign_off: '', language: null, notes: null, contact_languages: { '+966500000000': 'ar' } }]);

  // Preferences: add, edit, delete.
  const prefs = page.getByRole('region', { name: 'Learned preferences' });
  await prefs.getByLabel('Applies to').last().selectOption('surface:email');
  await prefs.getByRole('textbox', { name: 'Preference' }).last().fill('Always include the order number.');
  await prefs.getByRole('button', { name: 'Add preference' }).click();
  await expect(prefs.getByText('Always include the order number.')).toBeVisible();
  expect(sent.prefs).toEqual([{ scope: 'surface:email', text: 'Always include the order number.' }]);
  await prefs.getByRole('button', { name: 'Edit "Always include the order number."' }).click();
  await prefs.locator('.learningEdit').getByRole('textbox', { name: 'Preference' }).fill('Always include the Amazon order number.');
  await prefs.locator('.learningEdit').getByRole('button', { name: 'Save' }).click();
  await expect(prefs.getByText('Always include the Amazon order number.')).toBeVisible();
  await prefs.getByRole('button', { name: 'Delete "Reply to customers in Arabic."' }).click();
  await expect(prefs.getByText('Reply to customers in Arabic.')).toHaveCount(0);
  expect(sent.deletes).toEqual(['pref_1']);

  // A routine becomes a paused automation, then its schedule starts.
  const routines = page.getByRole('region', { name: 'Routines' });
  await routines.getByRole('button', { name: 'Find routines' }).click();
  await expect(routines.getByText('Found 1 new routine.')).toBeVisible();
  expect((sent.detects[0] as { timezone: string }).timezone).toBeTruthy();
  await routines.getByRole('button', { name: 'Create paused automation' }).click();
  await expect(routines.getByText('Paused automation', { exact: true })).toBeVisible();
  await expect(routines.getByRole('button', { name: 'Open in the Workbench' })).toBeVisible();
  await routines.getByRole('button', { name: 'Start the schedule' }).click();
  await expect(routines.getByText('Scheduled', { exact: true })).toBeVisible();
  expect(sent.routineActions).toEqual(['accept', 'confirm']);

  // A weekly suggestion, applied with one click, becomes a preference.
  const notes = page.getByRole('region', { name: 'Weekly improvement notes' });
  await expect(notes.getByRole('heading', { name: /Week 2026-W41/ })).toBeVisible();
  await notes.locator('.learningSuggestion').filter({ hasText: 'You rejected external.action' }).getByRole('button', { name: 'Apply' }).click();
  await expect(notes.getByText('Applied. It is now one of your learned preferences.')).toBeVisible();
  await expect(prefs.getByText('Confirm the recipient first.')).toBeVisible();

  // Feedback: the full text on demand, and delete.
  const feedback = page.getByRole('region', { name: 'Feedback ANUM learned from' });
  await expect(feedback.getByText('Changed 50% · 5 → 90 words', { exact: false })).toBeVisible();
  await feedback.getByRole('button', { name: 'Show text' }).click();
  await expect(feedback.getByText('Best regards', { exact: false }).last()).toBeVisible();
  await feedback.getByRole('button', { name: 'Delete this feedback (Draft edited)' }).click();
  await expect(feedback.getByText('Deleted. ANUM no longer uses it.')).toBeVisible();
  expect(sent.deletes).toContain('signal_0');
});

test('feedback is paged and searchable', async ({ page }) => {
  const sent = await mockApi(page, { signals: 23 });
  await page.goto('/#learning');
  const feedback = page.getByRole('region', { name: 'Feedback ANUM learned from' });
  await expect(feedback.getByText('1 to 10 of 23')).toBeVisible();
  await feedback.getByRole('button', { name: 'Next' }).click();
  await expect(feedback.getByText('11 to 20 of 23')).toBeVisible();
  await feedback.getByLabel('Kind of feedback').selectOption('approval_rejected');
  await expect(feedback.getByText('1 to 10 of 11')).toBeVisible();
  await feedback.getByRole('searchbox', { name: 'Search feedback' }).fill('nothing-like-this');
  await feedback.getByRole('button', { name: 'Search' }).click();
  await expect(feedback.getByText('Nothing matches. Clear the search or choose All kinds.')).toBeVisible();
  await expect.poll(() => sent.signalQueries.at(-1)).toContain('q=nothing-like-this');
});

test('empty, error and permission states each say what to do next', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#learning');
  await expect(page.getByText('Nothing learned yet. Edit a draft before sending, or use Edit to teach on an Assistant answer.')).toBeVisible();
  await expect(page.getByText('No preferences yet. Add one below, or apply a suggestion from the weekly notes.')).toBeVisible();
  await expect(page.getByText('No routines yet. After a few weeks of tasks, use Find routines.')).toBeVisible();
  await expect(page.getByText('No notes yet. The first one is written the first week learning is on.')).toBeVisible();
  await expect(page.getByText('No feedback yet. Rate an Assistant answer, or edit a draft before sending.')).toBeVisible();
});

test('a failed load says what failed and retries', async ({ page }) => {
  await mockApi(page, { overview: 500 });
  await page.goto('/#learning');
  await expect(page.getByRole('alert')).toContainText('Could not load what ANUM learned: The server could not reach its database.');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByTestId('learning-state')).toHaveText('Learning');
});

test('without a workspace role the page explains who can help', async ({ page }) => {
  await mockApi(page, { overview: 403 });
  await page.goto('/#learning');
  await expect(page.getByRole('alert')).toContainText('Ask a workspace owner to restore your membership');
});

test('a member cannot create automations or apply autonomy changes, and is told who can', async ({ page }) => {
  await mockApi(page, { member: true });
  await page.goto('/#learning');
  const routines = page.getByRole('region', { name: 'Routines' });
  await expect(routines.getByText('Only members and owners can create automations.', { exact: false })).toBeVisible();
  await expect(routines.getByRole('button', { name: 'Create paused automation' })).toBeDisabled();
  const autonomy = page.locator('.learningSuggestion').filter({ hasText: 'Let medium-risk actions run without asking' });
  await expect(autonomy.getByText('Only a workspace owner can apply autonomy changes.', { exact: false })).toBeVisible();
  await expect(autonomy.getByRole('button', { name: 'Apply' })).toBeDisabled();
});

test('with chat memory off, learning is off and the page leads to the switch', async ({ page }) => {
  await mockApi(page, { state: 'off_chat_memory' });
  await page.goto('/#learning');
  await expect(page.getByTestId('learning-state')).toHaveText('Off with chat memory');
  await expect(page.getByRole('button', { name: 'Find routines' })).toBeDisabled();
  await page.getByRole('button', { name: 'Open chat memory in Settings' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
});

test('Assistant answers take Helpful, Not helpful with a reason, and Edit to teach', async ({ page }) => {
  const sent = await mockApi(page);
  await page.goto('/#assistant');
  const answer = page.locator('.chatTurn.assistant').first();
  await expect(answer).toContainText('Order 42 ships on Sunday');
  await answer.getByRole('button', { name: 'Helpful', exact: true }).click();
  await expect(answer.getByRole('button', { name: 'Helpful', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await answer.getByRole('button', { name: 'Not helpful' }).click();
  await answer.getByRole('textbox', { name: 'What was wrong? (optional)' }).fill('Missing the tracking number');
  await answer.getByRole('button', { name: 'Send' }).click();
  await answer.getByRole('button', { name: 'Edit to teach' }).click();
  await answer.getByRole('textbox', { name: /Rewrite the answer/ }).fill('Order 42 ships Sunday. Tracking: SA123.');
  await answer.getByRole('button', { name: 'Save my version' }).click();
  await expect(answer.getByText('Saved. ANUM will learn your style from this edit.')).toBeVisible();
  // Ratings go to the answer itself (stored whatever the learning setting); Edit to teach goes to learning.
  expect(sent.ratings).toEqual([{ rating: 'up' }, { rating: 'down' }, { rating: 'down', reason: 'Missing the tracking number' }]);
  expect(sent.feedback).toEqual([
    { surface: 'chat', subject_id: 't2', original: 'Order 42 ships on Sunday from the Riyadh warehouse.', final: 'Order 42 ships Sunday. Tracking: SA123.' },
  ]);
});

test('with learning off, a rating is saved on the answer and says ANUM did not learn from it', async ({ page }) => {
  const sent = await mockApi(page, { state: 'off_by_user' });
  await page.goto('/#assistant');
  const answer = page.locator('.chatTurn.assistant').first();
  await answer.getByRole('button', { name: 'Helpful', exact: true }).click();
  await expect(answer.getByRole('status').filter({ hasText: 'Saved on this answer' })).toHaveText('Saved on this answer. ANUM did not learn from it: Learning is off. Turn on Learn from my activity to use this.');
  await expect(answer.getByRole('button', { name: 'Helpful', exact: true })).toHaveAttribute('aria-pressed', 'true');
  // Pressing it again clears the rating.
  await answer.getByRole('button', { name: 'Helpful', exact: true }).click();
  await expect(answer.getByRole('button', { name: 'Helpful', exact: true })).toHaveAttribute('aria-pressed', 'false');
  expect(sent.ratings).toEqual([{ rating: 'up' }, { rating: null }]);
});

test.describe('Arabic', () => {
  test('the page reads right to left in Arabic without overflow', async ({ page }) => {
    await mockApi(page);
    await useSavedLocale(page, 'ar');
    await page.goto('/#learning');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.getByRole('heading', { level: 1, name: 'ما تعلّمه ANUM' })).toBeVisible();
    await expect(page.getByTestId('learning-state')).toHaveText('يتعلّم');
    await expect(page.getByText('تعلّمه من 3 مسودات أرسلتها أو عدّلتها.', { exact: false })).toBeVisible();
    await expect(page.getByRole('region', { name: 'الروتينات' }).getByRole('button', { name: 'إنشاء أتمتة متوقفة' })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
  });
});

for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
  test(`What ANUM learned and answer feedback fit ${size.width}x${size.height} without horizontal overflow`, async ({ page }) => {
    await mockApi(page);
    await page.setViewportSize(size);
    await page.goto('/#learning');
    await expect(page.locator('.learningRoutine').first()).toBeVisible();
    await page.getByRole('region', { name: 'Feedback ANUM learned from' }).getByRole('button', { name: 'Show text' }).click();
    expect(await noHorizontalOverflow(page)).toBe(true);
    await page.goto('/#assistant');
    await page.reload();
    const answer = page.locator('.chatTurn.assistant').first();
    await answer.getByRole('button', { name: 'Edit to teach' }).click();
    await expect(answer.getByRole('textbox', { name: /Rewrite the answer/ })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
  });
}
