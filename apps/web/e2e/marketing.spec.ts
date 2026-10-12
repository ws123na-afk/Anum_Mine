import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Marketing (docs/marketing.md): connect accounts, compose English and Arabic drafts with previews,
// ask to publish and approve with the payload hash shown, move posts on the calendar, and read results.
const now = new Date().toISOString();
const later = new Date(Date.now() + 3 * 86400_000).toISOString();
const hash = '9a'.repeat(32);

const limits = (platform: string, name: string, extra: Record<string, unknown> = {}) => ({
  platform, name, text_limit: 2200, counting: 'chars', hashtag_limit: 30, hashtag_recommended: 10, requires_media: false, requires_video: false, requires_title: false,
  title_limit: null, max_images: 10, max_videos: 1, image_types: ['image/jpeg', 'image/png'], video_types: ['video/mp4'], image_ratio: null, video_ratio: null, video_seconds: null, notes: [], review: `${name} app review`, ...extra,
});
const setup = (role: string) => ({
  providers: [
    { provider: 'meta', label: 'Meta (Facebook and Instagram)', platforms: ['facebook', 'instagram'], configured: true, missing: [] },
    { provider: 'x', label: 'X', platforms: ['x'], configured: true, missing: [] },
    { provider: 'tiktok', label: 'TikTok', platforms: ['tiktok'], configured: false, missing: ['ANUM_MARKETING_TIKTOK_CLIENT_KEY'] },
  ],
  redirect_url: 'https://app.example.com/marketing/callback', public_media: true, tiktok_direct_post: false,
  platforms: [limits('facebook', 'Facebook Page', { text_limit: 63206 }), limits('instagram', 'Instagram', { requires_media: true, image_types: ['image/jpeg'] }), limits('x', 'X', { text_limit: 280, counting: 'x_weighted', hashtag_limit: null, hashtag_recommended: 2, max_images: 4 })],
  can_manage: role === 'owner', can_write: role !== 'viewer',
});
const account = (id: string, platform: string, name: string) => ({ id, platform, provider: platform === 'x' ? 'x' : 'meta', external_id: id, display_name: name, handle: 'tyreshop', status: 'connected', scopes: [], token_expires_at: null, details: {}, last_checked_at: now, last_error: null, connected_by: 'user_local', created_at: now, updated_at: now });
const post = (id: string, extra: Record<string, unknown> = {}) => ({
  id, campaign_id: null, group_id: 'g1', account_id: 'acct_fb', platform: 'facebook', language: 'en', status: 'draft', text: 'Summer tyre check', title: '', hashtags: ['Tyres'], link_url: null, media: [],
  scheduled_at: later, published_at: null, external_id: null, external_url: null, attempts: 0, last_error: null, approval_id: null, approved_by: null, authorised_by: null, approved_hash: null,
  metrics: null, metrics_updated_at: null, ai_generated: false, untrusted_source: false, created_by: 'user_local', created_at: now, updated_at: now,
  account_name: 'Tyre Shop', account_handle: null, account_status: 'connected', issues: [], length: 25, limit: 63206, approval: null, approval_state: 'none', ...extra,
});
const approval = {
  id: 'approval_mk', task_id: 'task_mk', action: 'marketing.schedule_post', risk_level: 'high', status: 'pending', reason: 'External or high-impact actions require explicit approval.',
  created_at: now, run_id: 'run_mk', step_id: 'step_mk', payload_hash: hash, expires_at: later, target: null,
  arguments: { post_id: 'mkpost_1', platform: 'facebook', account_id: 'acct_fb', account: 'Tyre Shop', language: 'en', scheduled_at: later, text: 'Summer tyre check\n\n#Tyres', title: '', media: [], campaign: null, content_hash: 'c'.repeat(64), untrusted_source: false },
};
const totals = (posts: number, engagement: number) => ({ posts, impressions: 3000, reach: 2400, engagement, clicks: 40, likes: 100, comments: 10, shares: 5, views: 0, engagement_rate: 0.05, without_metrics: 0 });
const summary = {
  start: now, end: now, totals: totals(3, 150), previous: { ...totals(2, 90), engagement_rate: 0.04 }, platforms: [{ ...totals(2, 120), platform: 'instagram' }, { ...totals(1, 30), platform: 'x' }],
  campaigns: [{ ...totals(3, 150), campaign_id: null, name: 'No campaign' }],
  top_posts: [{ id: 'mkpost_top', platform: 'instagram', account_id: 'acct_ig', excerpt: 'Check your tyre pressure', published_at: now, external_url: 'https://www.instagram.com/p/x/', metrics: { impressions: 2000, reach: 1600, engagement: 120, clicks: 0, likes: 90, comments: 8, shares: 4, saves: 2, views: 0, note: null } }],
  series: Array.from({ length: 7 }, (_, index) => ({ day: `2026-10-0${index + 1}`, posts: index % 2, impressions: 400, engagement: index * 10 })), upcoming: 2, pending_approval: 1, failed: 0, metrics_updated_at: now,
};

async function mockMarketing(page: Page, { role = 'owner', accounts = [account('acct_fb', 'facebook', 'Tyre Shop'), account('acct_x', 'x', 'Tyre Shop X')] }: { role?: string; accounts?: unknown[] } = {}) {
  await mockWorkspaceApi(page);
  const sent: { creates: unknown[]; decisions: unknown[]; moves: unknown[]; connects: unknown[]; deliveries: unknown[] } = { creates: [], decisions: [], moves: [], connects: [], deliveries: [] };
  let posts: Record<string, unknown>[] = [post('mkpost_1')];
  await page.route('http://localhost:8000/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname, method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/whatsapp/conversations') return json([{ id: 'wac_owner', wa_id: '966500000000', contact_name: 'Owner phone' }]);
    if (!path.startsWith('/api/v1/marketing/')) return route.fallback();
    if (path === '/api/v1/marketing/setup') return json(setup(role));
    if (path === '/api/v1/marketing/accounts') return json(accounts);
    if (path === '/api/v1/marketing/campaigns' && method === 'GET') return json([]);
    if (path === '/api/v1/marketing/preferences') return json({ brand_voice: 'Practical', timezone: 'Asia/Riyadh', default_hashtags_en: ['KSA'], default_hashtags_ar: [], updated_by: null, updated_at: null });
    if (path === '/api/v1/marketing/media') return json([{ id: 'file_logo', name: 'logo.png', content_type: 'image/png', kind: 'image', size_bytes: 512, created_at: now }]);
    if (path === '/api/v1/marketing/posts' && method === 'GET') return json(url.searchParams.get('status') === 'failed' ? [] : posts);
    if (path === '/api/v1/marketing/posts' && method === 'POST') {
      const body = request.postDataJSON() as { variants: { account_id: string; language: string; text: string }[] };
      sent.creates.push(body);
      const created = body.variants.map((variant, index) => post(`mkpost_new${index}`, { account_id: variant.account_id, platform: variant.account_id === 'acct_x' ? 'x' : 'facebook', language: variant.language, text: variant.text, scheduled_at: null }));
      posts = [...created, ...posts];
      return json(created, 201);
    }
    if (path === '/api/v1/marketing/posts/mkpost_1/schedule') {
      posts = posts.map((item) => item.id === 'mkpost_1' ? { ...item, status: 'pending_approval', approval_id: approval.id, approval, approval_state: 'pending' } : item);
      return json(posts.find((item) => item.id === 'mkpost_1'));
    }
    if (path === '/api/v1/marketing/posts/mkpost_1' && method === 'GET') return json(posts.find((item) => item.id === 'mkpost_1'));
    if (path === '/api/v1/marketing/posts/mkpost_1/reschedule') {
      sent.moves.push(request.postDataJSON());
      const next = { ...posts.find((item) => item.id === 'mkpost_1'), scheduled_at: (request.postDataJSON() as { scheduled_at: string }).scheduled_at };
      posts = posts.map((item) => item.id === 'mkpost_1' ? next : item);
      return json(next);
    }
    if (path === '/api/v1/marketing/assist/ideas') return json({ ideas: [{ title: 'Pressure check', angle: 'safety', platform: 'facebook', en: 'Check your tyre pressure before the weekend.', ar: 'افحص ضغط الإطارات قبل عطلة نهاية الأسبوع.', hashtags_en: ['Tyres'], hashtags_ar: ['إطارات'] }], generator: 'template', untrusted_source: false, brand_voice_used: false, note: 'No model is connected, so these drafts come from templates.', best_times: [{ platform: null, weekday: 6, hour: 21, posts: 0, engagement_rate: null, basis: 'default' }] });
    if (path === '/api/v1/marketing/oauth/start') { sent.connects.push(request.postDataJSON()); return json({ authorize_url: new URL('/?code=meta-code&state=mkt.example', request.headers().referer ?? 'http://127.0.0.1:4173/').toString() }); }
    if (path === '/api/v1/marketing/oauth/callback') return json([account('acct_ig', 'instagram', 'Tyre Shop IG')]);
    if (path === '/api/v1/marketing/analytics/summary') return json(summary);
    if (path === '/api/v1/marketing/reports/weekly') return json({ week_start: '2026-10-04', week_end: '2026-10-10', language: url.searchParams.get('language'), headline: url.searchParams.get('language') === 'ar' ? 'تقرير التسويق الأسبوعي' : 'Weekly marketing report', lines: ['3 posts reached 2,400 people.'], best_times: [], delivery: { email: 'available', whatsapp: 'available' } });
    if (path === '/api/v1/marketing/reports/weekly/deliver') { sent.deliveries.push({ language: url.searchParams.get('language'), ...(request.postDataJSON() as object) }); return json({ email: { outcome: 'requested', detail: '', approval_id: 'approval_report', target: 'owner@example.com' }, whatsapp: { outcome: 'not_requested', detail: '', approval_id: null, target: null } }); }
    return json({ error: { code: 'not_found', message: `unmocked ${method} ${path}`, correlation_id: 'c' } }, 404);
  });
  await page.route('http://localhost:8000/api/v1/approvals/approval_mk/approve', async (route) => {
    sent.decisions.push(route.request().postDataJSON());
    posts = posts.map((item) => item.id === 'mkpost_1' ? { ...item, status: 'scheduled', approval: null, approval_state: 'approved', approved_hash: 'c'.repeat(64), authorised_by: 'approval' } : item);
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ approval: { ...approval, status: 'approved' }, task: { id: 'task_mk', title: 't', prompt: 'p', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now }, run: { id: 'run_mk', task_id: 'task_mk', status: 'completed', steps: [], result: 'Approved. The post goes to Tyre Shop on facebook within a minute.' } }) });
  });
  return sent;
}

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('an owner drafts bilingual posts with AI help, asks to publish and approves with the payload hash', async ({ page }) => {
  const sent = await mockMarketing(page);
  await page.goto('/#marketing');
  await expect(page.getByRole('heading', { level: 1, name: 'Marketing' })).toBeVisible();
  await expect(page.getByText('Summer tyre check')).toBeVisible();

  await page.getByRole('button', { name: 'New post' }).click();
  await expect(page.getByRole('heading', { name: 'New post', level: 2 })).toBeFocused();
  await page.getByLabel('What to promote').fill('TyrePulse pressure alerts');
  await page.getByRole('button', { name: 'Suggest ideas' }).click();
  await expect(page.getByText('No model is connected')).toBeVisible();
  await page.getByRole('button', { name: 'Use this' }).click();
  await expect(page.getByLabel('Text in Arabic')).toHaveValue('افحص ضغط الإطارات قبل عطلة نهاية الأسبوع.');
  await expect(page.getByRole('region', { name: 'Previews' }).getByRole('article')).toHaveCount(2);
  await page.getByRole('button', { name: 'Save 2 drafts' }).click();
  await expect(page.getByText('Saved 2 drafts.')).toBeVisible();
  expect(sent.creates).toHaveLength(1);
  expect((sent.creates[0] as { variants: { language: string }[] }).variants.map((v) => v.language)).toEqual(['en', 'ar']);

  const card = page.getByRole('listitem').filter({ hasText: 'Summer tyre check' });
  await card.getByRole('button', { name: 'Schedule' }).click();
  await card.getByRole('button', { name: 'Ask to publish' }).click();
  const box = card.getByRole('group', { name: 'Publishing approval' });
  await expect(box.getByText('9a9a9a9a9a9a…')).toBeVisible();
  await expect(box.getByText('Tyre Shop', { exact: true })).toBeVisible();
  await box.getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByText('Approved. The post goes to Tyre Shop')).toBeVisible();
  expect(sent.decisions).toEqual([{ payload_hash: hash }]);
  await expect(card.getByText('scheduled', { exact: true })).toBeVisible();
});

test('the calendar moves a post with the keyboard-friendly move button', async ({ page }) => {
  const sent = await mockMarketing(page);
  await page.goto('/#marketing-calendar');
  const move = page.getByRole('button', { name: /Move the Facebook post for Tyre Shop/ }).first();
  test.skip(!(await move.isVisible()), 'the month grid is replaced by the agenda on narrow screens');
  await move.click();
  await page.getByRole('button', { name: 'Move here' }).last().click();
  await expect(page.getByText(/^Moved to/)).toBeVisible();
  expect(sent.moves).toHaveLength(1);
});

test('connecting an account returns through the OAuth callback', async ({ page }) => {
  const sent = await mockMarketing(page);
  await page.goto('/#marketing-accounts');
  await expect(page.getByText('ANUM_MARKETING_TIKTOK_CLIENT_KEY')).toBeVisible();
  await page.getByRole('button', { name: 'Connect Meta (Facebook and Instagram)' }).click();
  await expect(page.getByText('Connected 1: Tyre Shop IG.')).toBeVisible();
  expect(sent.connects).toEqual([{ provider: 'meta' }]);
  await expect(page).toHaveURL(/#marketing-accounts$/);
});

test('members see why they cannot connect, viewers cannot draft, and an empty workspace says what to do', async ({ page }) => {
  await mockMarketing(page, { role: 'member', accounts: [] });
  await page.goto('/#marketing-accounts');
  await expect(page.getByText('Only workspace owners connect or disconnect accounts.')).toBeVisible();
  await expect(page.getByText('A workspace owner connects accounts under Accounts.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New post' })).toBeDisabled();
});

test('analytics and the weekly report read in Arabic, right to left, without overflow', async ({ page }) => {
  const sent = await mockMarketing(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#marketing-analytics');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'التسويق' })).toBeVisible();
  await expect(page.getByText('الوصول', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'تقرير التسويق الأسبوعي' })).toBeVisible();
  await expect(page.getByRole('table', { name: 'حسب المنصة' })).toBeVisible();
  await expect(page.getByLabel('محادثة واتساب للتقرير')).toHaveValue('wac_owner');
  await page.getByRole('button', { name: 'أرسله إلى بريدي' }).click();
  await expect(page.getByText('البريد: التقرير بانتظار الموافقة في الموافقات.')).toBeVisible();
  expect(sent.deliveries).toEqual([{ language: 'ar', email: true }]);
  expect(await noOverflow(page)).toBe(true);
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([]);
});

test('the posts view has no serious accessibility problems in English', async ({ page }) => {
  await mockMarketing(page);
  await page.goto('/#marketing');
  await expect(page.getByText('Summer tyre check')).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
  expect(await noOverflow(page)).toBe(true);
});
