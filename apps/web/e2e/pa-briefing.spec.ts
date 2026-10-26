import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { useSavedLocale } from './support/workspaceApi';

// PA daily briefing (docs/ANUM_MINE_EXECUTIVE_PA.md#daily-briefing): typed and voice "brief me" build a
// read-only briefing from the workspace's real records; every line opens its source; empty sections say
// "Nothing to report", unreadable ones "Not connected"; the assistant never decides an approval.
// The API is mocked at http://localhost:8000; the sentences in the fixtures are the server's own templates.
const stamp = '2026-10-25T09:00:00Z';
type Row = Record<string, unknown>;
type Variant = 'full' | 'quiet' | 'partial' | 'error' | 'denied';

const item = (kind: string, id: string, label: string, view: string, detail_text: string | null = null, at: string | null = null, source = kind) =>
  ({ kind, id, label, detail: detail_text, detail_text, at, source, link: { view, id } });
const section = (key: string, title: string, headline: string, state: string, count: number | null, items: Row[] = [], note_texts: string[] = []) =>
  ({ key, title, headline, state, count, items, facts: {}, truncated: false, notes: [], note_texts });

const FULL = {
  en: [
    section('approvals', 'Approvals', '2 approvals are waiting for a decision.', 'ok', 2, [item('approval', 'ap_1', 'send_email', 'approvals', 'high'), item('approval', 'ap_2', 'delete_file', 'approvals', 'medium')]),
    section('blocked', 'Blocked work', '1 task is blocked (waiting for approval or failed).', 'ok', 1, [item('task', 'task_7', 'Order tyres', 'tasks', 'waiting_approval')]),
    section('due', 'Tasks due', '1 overdue and 1 due today.', 'ok', 2, [item('task', 'task_3', 'File fleet report', 'tasks', 'overdue', '2026-10-24T08:00:00Z'), item('task', 'task_4', 'Review supplier plan', 'tasks', 'due today', '2026-10-25T15:00:00Z')]),
    section('running', 'Agent runs', '1 agent run is running now.', 'ok', 1, [item('task', 'task_9', 'Crunch numbers', 'tasks')]),
    section('meetings', 'Meetings today', '1 meeting today.', 'ok', 1, [item('event', 'evt_1', 'Fleet review', 'calendar', 'calendar', '2026-10-25T11:00:00Z')], ['Live meeting sessions: not connected.']),
    section('agents', 'Agents needing attention', '1 agent needs attention.', 'ok', 1, [item('agent', 'agent_a', 'Scout', 'agents', 'paused')]),
    section('cost', 'Cost against budget', 'USD 12.50 spent this month of a USD 50.00 limit.', 'ok', null, [item('budget', 'workspace', 'workspace', 'budgets')]),
  ],
  ar: [
    section('approvals', 'الموافقات', '2 موافقات بانتظار قرار.', 'ok', 2, [item('approval', 'ap_1', 'send_email', 'approvals', 'high'), item('approval', 'ap_2', 'delete_file', 'approvals', 'medium')]),
    section('blocked', 'العمل المعطل', 'مهمة واحدة معطلة (بانتظار الموافقة أو فاشلة).', 'ok', 1, [item('task', 'task_7', 'طلب الإطارات', 'tasks', 'waiting_approval')]),
    section('due', 'المهام المستحقة', '1 متأخرة و1 مستحقة اليوم.', 'ok', 2, [item('task', 'task_3', 'تقديم تقرير الأسطول', 'tasks', 'متأخرة', '2026-10-24T08:00:00Z')]),
    section('running', 'تشغيلات الوكلاء', 'تشغيل واحد للوكلاء قيد العمل الآن.', 'ok', 1, [item('task', 'task_9', 'حساب الأرقام', 'tasks')]),
    section('meetings', 'اجتماعات اليوم', 'اجتماع واحد اليوم.', 'ok', 1, [item('event', 'evt_1', 'مراجعة الأسطول', 'calendar', 'التقويم', '2026-10-25T11:00:00Z')], ['جلسات الاجتماعات المباشرة: غير متصلة.']),
    section('agents', 'وكلاء يحتاجون إلى انتباه', 'وكيل واحد يحتاج إلى انتباه.', 'ok', 1, [item('agent', 'agent_a', 'الكشاف', 'agents', 'متوقف مؤقتا')]),
    section('cost', 'التكلفة مقابل الميزانية', 'أُنفق 12.50 دولارا هذا الشهر من حد قدره 50.00 دولارا.', 'ok', null, [item('budget', 'workspace', 'workspace', 'budgets')]),
  ],
} as const;
const TITLES = { en: ['Approvals', 'Blocked work', 'Tasks due', 'Agent runs', 'Meetings today', 'Agents needing attention', 'Cost against budget'], ar: ['الموافقات', 'العمل المعطل', 'المهام المستحقة', 'تشغيلات الوكلاء', 'اجتماعات اليوم', 'وكلاء يحتاجون إلى انتباه', 'التكلفة مقابل الميزانية'] } as const;
const KEYS = ['approvals', 'blocked', 'due', 'running', 'meetings', 'agents', 'cost'];
const quiet = (lang: 'en' | 'ar') => KEYS.map((key, i) => section(key, TITLES[lang][i], lang === 'en' ? 'Nothing to report.' : 'لا شيء للإبلاغ عنه.', 'empty', 0));
const partial = (lang: 'en' | 'ar') => KEYS.map((key, i) => key === 'meetings' ? section(key, TITLES[lang][i], lang === 'en' ? 'Not connected.' : 'غير متصل.', 'not_connected', null)
  : key === 'cost' ? section(key, TITLES[lang][i], lang === 'en' ? 'Not available for your role.' : 'غير متاح لدورك.', 'restricted', null)
    : section(key, TITLES[lang][i], lang === 'en' ? 'Nothing to report.' : 'لا شيء للإبلاغ عنه.', 'empty', 0));

function briefingFor(variant: Variant, lang: 'en' | 'ar') {
  const sections = variant === 'full' ? FULL[lang] : variant === 'partial' ? partial(lang) : quiet(lang);
  return { version: 1, lang, generated_at: stamp, date: '2026-10-25', title: lang === 'en' ? 'Daily briefing' : 'الإحاطة اليومية', sections, text: '', reportable: sections.filter((s) => s.state === 'ok').length };
}

const tools = [['navigate', 'read'], ['open_panel', 'read'], ['summarise_workspace', 'read'], ['list_blocked_work', 'read'], ['list_pending_approvals', 'read'], ['daily_briefing', 'read'], ['start_task', 'write']]
  .map(([name, kind]) => ({ name, kind, label: name, risk: kind === 'read' ? 'low' : 'medium', autonomy: kind === 'read' ? 'automatic' : 'confirm_each', governed_by: 'the governed API', permissions: [], allowed: true }));
const stops = [{ building_id: 'hq', building_name: 'Headquarters', floor_id: 'f0', floor_name: 'Ground', level: 0, room_id: 'r_rec', room_name: 'Reception', room_kind: 'reception', capacity: 6, desks: 0 }];

async function mockApi(page: Page, variant: Variant) {
  const seen = { invokes: [] as Row[], briefingGets: [] as string[] };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: stamp, updated_at: stamp });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: stamp, updated_at: stamp, archived_at: null, archived_by: null });
    if (path === '/api/v1/executive/overview') return json({ version: 1, tenant_id: 'tenant_local', home_workspace_id: 'workspace_foundation', generated_at: stamp, truncated: false, workspaces: [] });
    if (path === '/api/v1/executive/grants') return json({ version: 1, items: [] });
    if (path === '/api/v1/executive-pa/tools') return json({ tools });
    if (path === '/api/v1/executive-pa/tour') return json({ status: 'ready', stops });
    if (path === '/api/v1/executive-pa/briefing') {
      seen.briefingGets.push(request.url());
      return json({ error: { code: 'x', message: 'unused' } }, 404);
    }
    if (path === '/api/v1/executive-pa/invoke') {
      const body = JSON.parse(request.postData() ?? '{}') as Row;
      seen.invokes.push(body);
      if (body.tool === 'daily_briefing') {
        const lang = (body.arguments as Row).lang === 'ar' ? 'ar' : 'en';
        if (variant === 'error') return json({ error: { code: 'unavailable', message: 'The briefing service is down' } }, 503);
        if (variant === 'denied') return json({ tool: 'daily_briefing', status: 'denied', missing: ['task:read'] });
        return json({ tool: 'daily_briefing', status: 'ok', briefing: briefingFor(variant, lang) });
      }
    }
    return json({ error: { code: 'not_found', message: 'unmocked' } }, 404);
  });
  return seen;
}

const dock = (page: Page) => page.locator('.paDock');
const card = (page: Page) => page.getByRole('region', { name: /Daily briefing|الإحاطة اليومية/ });
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

async function ask(page: Page, sentence: string) {
  await page.getByLabel(/Tell the assistant what to do|أخبر المساعد بما تريد/).fill(sentence);
  await page.getByRole('button', { name: /^(Send|إرسال)$/ }).click();
}

test('typed "brief me": every section from real records, each line opens its source, no approval can be decided', async ({ page }) => {
  const seen = await mockApi(page, 'full');
  await page.goto('/#executive');
  await ask(page, 'brief me');
  await expect(card(page)).toBeVisible();
  for (const title of TITLES.en) await expect(card(page).getByRole('heading', { name: title })).toBeVisible();
  await expect(card(page)).toContainText('2 approvals are waiting for a decision.');
  await expect(card(page)).toContainText('USD 12.50 spent this month of a USD 50.00 limit.');
  await expect(card(page)).toContainText('Live meeting sessions: not connected.');
  expect(seen.invokes.at(-1)).toMatchObject({ tool: 'daily_briefing', input_mode: 'typed', arguments: { lang: 'en' } });
  expect('confirm_hash' in (seen.invokes.at(-1) as Row)).toBe(false);
  // No control in the briefing approves, rejects or confirms anything.
  await expect(card(page).getByRole('button', { name: /approve|reject|confirm/i })).toHaveCount(0);
  await expect(card(page)).toContainText('You decide approvals on the Approvals screen.');
  // A line opens its source: the approval opens Approvals, a calendar event opens the Calendar.
  await card(page).getByRole('button', { name: 'Open send_email' }).click();
  await expect(page).toHaveURL(/#approvals/);
  await page.goto('/#executive');
  await ask(page, 'brief me');
  await card(page).getByRole('button', { name: 'Open Fleet review' }).click();
  await expect(page).toHaveURL(/#calendar/);
});

test('spoken "brief me" is sent as voice input and still only reads', async ({ page }) => {
  await page.addInitScript(() => {
    class Recognizer {
      continuous = false; interimResults = false; lang = 'en-US';
      onresult: ((event: unknown) => void) | null = null; onerror: ((event: unknown) => void) | null = null; onend: (() => void) | null = null;
      start() {
        window.setTimeout(() => this.onresult?.({ results: [Object.assign([{ transcript: 'brief me' }], { isFinal: true })] }), 20);
        window.setTimeout(() => this.onend?.(), 60);
      }
      stop() { /* ended by the stub */ }
      abort() { /* ended by the stub */ }
    }
    Object.assign(window, { SpeechRecognition: Recognizer, webkitSpeechRecognition: Recognizer });
  });
  const seen = await mockApi(page, 'full');
  await page.goto('/#executive');
  await page.getByRole('button', { name: 'Speak' }).click();
  await expect(page.getByLabel('Tell the assistant what to do')).toHaveValue('brief me');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(card(page)).toBeVisible();
  expect(seen.invokes.at(-1)).toMatchObject({ tool: 'daily_briefing', input_mode: 'voice' });
  expect('confirm_hash' in (seen.invokes.at(-1) as Row)).toBe(false);
});

test('states: nothing to report, not connected, restricted, failed (with retry) and denied', async ({ page }) => {
  await mockApi(page, 'quiet');
  await page.goto('/#executive');
  await dock(page).getByRole('button', { name: 'Brief me' }).first().click();
  await expect(card(page).getByText('Nothing to report.')).toHaveCount(7);
  await expect(card(page).getByRole('status').filter({ hasText: 'Nothing to report today.' })).toBeVisible();

  await page.unrouteAll();
  await mockApi(page, 'partial');
  await page.reload();
  await dock(page).getByRole('button', { name: 'Brief me' }).first().click();
  await expect(card(page).getByText('Not connected.')).toBeVisible();
  await expect(card(page).getByText('Not available for your role.')).toBeVisible();

  await page.unrouteAll();
  await mockApi(page, 'error');
  await page.reload();
  await dock(page).getByRole('button', { name: 'Brief me' }).first().click();
  await expect(dock(page).getByRole('alert').filter({ hasText: 'The briefing could not be built' })).toBeVisible();
  await expect(dock(page).getByRole('alert').getByRole('button', { name: 'Retry' })).toBeVisible();

  await page.unrouteAll();
  await mockApi(page, 'denied');
  await page.reload();
  await dock(page).getByRole('button', { name: 'Brief me' }).first().click();
  await expect(dock(page).getByRole('alert').filter({ hasText: 'Your role cannot ask for the briefing' })).toBeVisible();
});

test('loading state shows while the briefing is built', async ({ page }) => {
  await mockApi(page, 'full');
  await page.route('http://localhost:8000/api/v1/executive-pa/invoke', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    await route.fallback();
  });
  await page.goto('/#executive');
  await dock(page).getByRole('button', { name: 'Brief me' }).first().click();
  await expect(dock(page).getByRole('status').filter({ hasText: 'Working' }).first()).toBeVisible();
  await expect(card(page)).toBeVisible();
});

test.describe('Arabic', () => {
  test.beforeEach(async ({ page }) => { await useSavedLocale(page, 'ar'); });

  test('the briefing is asked for and shown in Arabic, right to left', async ({ page }) => {
    const seen = await mockApi(page, 'full');
    await page.goto('/#executive');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await ask(page, 'أحطني علما');
    await expect(card(page)).toBeVisible();
    expect(seen.invokes.at(-1)).toMatchObject({ tool: 'daily_briefing', arguments: { lang: 'ar' } });
    for (const title of TITLES.ar) await expect(card(page).getByRole('heading', { name: title })).toBeVisible();
    await expect(card(page)).toContainText('مهمة واحدة معطلة');
    await expect(card(page).getByRole('button', { name: 'فتح الكشاف' })).toBeVisible();
    await noOverflow(page);
  });
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const lang of ['en', 'ar'] as const) {
  test(`@gate the briefing fits all five sizes in ${lang} without overflow or clipped controls`, async ({ page }) => {
    await useSavedLocale(page, lang);
    await mockApi(page, 'full');
    await page.goto('/#executive');
    await ask(page, lang === 'en' ? 'brief me' : 'أحطني علما');
    await expect(card(page)).toBeVisible();
    const shots = process.env.PA_SHOTS;
    if (shots) mkdirSync(shots, { recursive: true });
    for (const [width, height] of SIZES) {
      await page.setViewportSize({ width, height });
      await expect(card(page)).toBeVisible();
      await noOverflow(page);
      const clipped = await card(page).locator('button').evaluateAll((els) => els.filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1); }).length);
      expect(clipped).toBe(0);
      await card(page).scrollIntoViewIfNeeded();
      if (shots) {
        await page.screenshot({ path: `${shots}/briefing-${lang}-${width}x${height}.png`, fullPage: false });
        await card(page).screenshot({ path: `${shots}/briefing-card-${lang}-${width}x${height}.png` });
      }
    }
  });
}
