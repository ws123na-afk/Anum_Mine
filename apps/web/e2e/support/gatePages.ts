import type { Page } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';
import { mockMarketing } from './marketingApi';
import { mockWhatsApp } from './whatsappApi';
import { mockCallsApi } from './callsApi';
import { mockBusiness } from './businessApi';
import { mockInvoices } from './businessInvoicesApi';
import { mockTyre } from './tyreApi';
import { mockHq } from './hqApi';
import { mockMailApi } from './mailApi';
import { mockDeveloper } from './developerApi';
import { mockBrevo } from './brevoApi';
import { mockBilling } from './billingApi';
import { VIEWS } from '../../src/lib/shell.ts';
import { TABS as BUSINESS_TABS } from '../../src/lib/business.ts';
import { TABS as CALLS_TABS } from '../../src/lib/calls.ts';
import { COMMERCE_TABS } from '../../src/lib/commerce.ts';
import { FINANCE_TABS } from '../../src/lib/finance.ts';
import { MARKETING_TABS } from '../../src/lib/marketing.ts';

// Every page the web app can open, for the UI quality gates: e2e/page-layout.spec.ts (overflow and
// clipped controls), e2e/accessibility.spec.ts (axe) and e2e/journey.spec.ts (no dead buttons).
// DEEP_LINKS is generated from the source: every view in src/lib/shell.ts, every tab a module opens by
// link (the TABS lists in src/lib/business.ts, calls.ts, commerce.ts, finance.ts and marketing.ts) and
// the Settings and Integrations links viewFromHash understands. The hand-written entries below name a
// selector that is only visible once the page shows its data; any deep link without one is added
// with the module's mock and waits for the page heading and its requests. The coverage test in
// page-layout.spec.ts fails when a deep link has no entry.
export interface GatePage {
  /** The URL hash without "#". */
  hash: string;
  /** The view this hash opens (a key of shell.ts VIEWS). */
  view: string;
  mock: (page: Page) => Promise<unknown>;
  /** CSS selector of something that appears only once the page has its data. */
  ready: string;
}

const workspace = (page: Page) => mockWorkspaceApi(page);
const tyre = async (page: Page) => { await mockWorkspaceApi(page); await mockTyre(page); };
const mail = async (page: Page) => { await mockWorkspaceApi(page); await mockMailApi(page); };
const callsWithProvider = (page: Page) => mockCallsApi(page, { provider: true });
const headquarters = async (page: Page) => { await mockHq(page, { commerce: 'connected', tyre: 'none', meetings: 'live' }); };
const billing = async (page: Page) => { await mockWorkspaceApi(page); await mockBilling(page); };

/** Invoices with every state in the list: sent and overdue, sent on time, a draft and one waiting for approval. */
async function mockInvoicesWithData(page: Page) {
  const mock = await mockInvoices(page);
  mock.seedSent({ customer_reference: 'PO 17, a long customer reference to check wrapping', issue_date: '2026-01-01', due_date: '2026-01-15' });
  mock.seedSent();
  mock.seedSent({ status: 'draft', number: null, issue_date: null, due_date: null, seller: null });
  mock.seedSent({ status: 'pending_approval', number: null, issue_date: null, due_date: null, seller: null });
  return mock;
}

export const GATE_PAGES: GatePage[] = [
  { hash: 'home', view: 'home', mock: workspace, ready: '.commandPanel .listRow' },
  { hash: 'command', view: 'command', mock: workspace, ready: '.commandStage' },
  { hash: 'assistant', view: 'assistant', mock: workspace, ready: '.chatTurn.assistant' },
  { hash: 'agents', view: 'agents', mock: workspace, ready: '.agentRow' },
  { hash: 'voice', view: 'voice', mock: workspace, ready: '.voiceSpaceTalk' },
  { hash: 'call', view: 'call', mock: workspace, ready: '.callStart' },
  { hash: 'automation', view: 'automation', mock: workspace, ready: '.autoRows' },
  { hash: 'tasks', view: 'tasks', mock: workspace, ready: '.recordButton' },
  { hash: 'email', view: 'email', mock: mail, ready: '.mailRow' },
  { hash: 'calls', view: 'calls', mock: mockCallsApi, ready: '.callsRow' },
  { hash: 'calls-contacts?contact=contact_omar', view: 'calls', mock: mockCallsApi, ready: '.callsDetail .callsMeta' },
  { hash: 'calls-dial', view: 'calls', mock: mockCallsApi, ready: '.callsKeypad' },
  { hash: 'calls-ai', view: 'calls', mock: callsWithProvider, ready: '.callsAiNew textarea' },
  { hash: 'calls-provider', view: 'calls', mock: callsWithProvider, ready: '.callsUrl code' },
  { hash: 'meetings', view: 'meetings', mock: workspace, ready: '.meetingRows li' },
  { hash: 'meetings?meeting=meeting_pmv', view: 'meetings', mock: workspace, ready: '.transcriptLines li' },
  { hash: 'calendar', view: 'calendar', mock: workspace, ready: '.calendarEvents' },
  { hash: 'team', view: 'team', mock: workspace, ready: '.workRow' },
  { hash: 'assistants', view: 'assistants', mock: workspace, ready: '.assistantsTable tbody tr' },
  { hash: 'integrations', view: 'integrations', mock: workspace, ready: 'li.intRow' },
  { hash: 'tyre', view: 'tyre', mock: tyre, ready: '.tyreMetric' },
  { hash: 'commerce', view: 'commerce', mock: workspace, ready: '.cmChart svg' },
  { hash: 'finance', view: 'finance', mock: workspace, ready: '.finChart svg' },
  { hash: 'finance-transactions', view: 'finance', mock: workspace, ready: '.finTxTable tbody tr' },
  { hash: 'finance-import', view: 'finance', mock: workspace, ready: '.finMapping, input[type="file"]' },
  { hash: 'finance-safe', view: 'finance', mock: workspace, ready: '.finBuild' },
  { hash: 'finance-guardian', view: 'finance', mock: workspace, ready: '.finRules' },
  { hash: 'finance-connections', view: 'finance', mock: workspace, ready: '.finSource' },
  { hash: 'finance-trading', view: 'finance', mock: workspace, ready: '.finTradeTable tbody tr' },
  { hash: 'finance-execution', view: 'finance', mock: workspace, ready: '.finPreconditions li' },
  { hash: 'marketing', view: 'marketing', mock: mockMarketing, ready: '.mkPostTable tbody tr' },
  { hash: 'marketing-accounts', view: 'marketing', mock: mockMarketing, ready: '.mkAccountTable tbody tr' },
  { hash: 'business', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-leads', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-accounts', view: 'business', mock: mockInvoices, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-pipeline', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-proposals', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-projects', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-catalog', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-invoices', view: 'business', mock: mockInvoicesWithData, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-settings', view: 'business', mock: mockInvoices, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'files', view: 'files', mock: workspace, ready: '.filesTable tbody tr' },
  { hash: 'memory', view: 'memory', mock: workspace, ready: '.memoryRow' },
  { hash: 'knowledge', view: 'knowledge', mock: workspace, ready: '.kbCollectionButton' },
  { hash: 'reports', view: 'reports', mock: workspace, ready: '.reportRow' },
  { hash: 'reports/report_tyre', view: 'reports', mock: workspace, ready: '.reportTable' },
  { hash: 'skills', view: 'skills', mock: workspace, ready: 'article.catalogCard' },
  { hash: 'favorites', view: 'favorites', mock: workspace, ready: '.favoritesTable tbody tr' },
  { hash: 'inbox', view: 'inbox', mock: workspace, ready: '.inboxRow' },
  { hash: 'whatsapp', view: 'whatsapp', mock: mockWhatsApp, ready: '.waConversation' },
  { hash: 'learning', view: 'learning', mock: workspace, ready: '.learningRoutine' },
  { hash: 'approvals', view: 'approvals', mock: workspace, ready: '.approvalDetail' },
  { hash: 'activity', view: 'activity', mock: workspace, ready: '.autonomyRow' },
  { hash: 'autonomy', view: 'autonomy', mock: workspace, ready: '.autonomyPanel' },
  { hash: 'settings', view: 'settings', mock: workspace, ready: '.chatMemorySetting input[type="checkbox"]' },
  { hash: 'notifications', view: 'settings', mock: workspace, ready: '.phoneList .phoneRow' },
  { hash: 'language', view: 'settings', mock: workspace, ready: '.languageChoices' },
  { hash: 'privacy', view: 'settings', mock: workspace, ready: '#privacy-stored' },
  { hash: 'governance', view: 'governance', mock: workspace, ready: '.govRow' },
  { hash: 'setup', view: 'setup', mock: workspace, ready: '.planRollout' },
  { hash: 'enterprise', view: 'enterprise', mock: workspace, ready: '.marketRow' },
  { hash: 'members', view: 'members', mock: workspace, ready: '.memberRow' },
  { hash: 'audit', view: 'audit', mock: workspace, ready: '.auditTable' },
  { hash: 'budgets', view: 'budgets', mock: workspace, ready: '.budgetCard' },
  { hash: 'policy', view: 'policy', mock: workspace, ready: '.policySwitch' },
  { hash: 'suppressions', view: 'suppressions', mock: workspace, ready: '.suppRow' },
  { hash: 'developer', view: 'developer', mock: mockDeveloper, ready: '.devLayout' },
  { hash: 'organisations', view: 'organisations', mock: workspace, ready: '.orgsTable tbody tr' },
  { hash: 'platformBilling', view: 'platformBilling', mock: billing, ready: '.blConsoleBar' },
  { hash: 'billing', view: 'settings', mock: billing, ready: '.blPlanNow' },
  { hash: 'office', view: 'office', mock: headquarters, ready: '.hqLive .hqBuildingCard, .hqLive .hqListBuilding' },
];

const brevo = async (page: Page) => { await mockMarketing(page); await mockBrevo(page); };

/** Tab links: `<view>-<tab>` for every tab but the module's first, which is the plain view hash. */
const tabLinks = (view: string, tabs: readonly string[]) => tabs.slice(1).map((tab) => `${view}-${tab}`);

/** Every hash the app opens as its own page or tab, from the source. */
export const DEEP_LINKS: readonly string[] = [
  ...VIEWS,
  ...tabLinks('business', BUSINESS_TABS),
  ...tabLinks('calls', CALLS_TABS),
  ...tabLinks('commerce', COMMERCE_TABS),
  ...tabLinks('finance', FINANCE_TABS),
  ...tabLinks('marketing', MARKETING_TABS),
  // Settings sections and the Integrations connection setup (shell.ts viewFromHash).
  'notifications', 'language', 'privacy', 'appearance', 'connections',
];

/** The page a deep link belongs to, and the mock that fills it. */
function familyOf(link: string): { view: string; mock: (page: Page) => Promise<unknown> } {
  if (link === 'marketing-email') return { view: 'marketing', mock: brevo };
  if (link.startsWith('marketing-')) return { view: 'marketing', mock: mockMarketing };
  if (link.startsWith('business-')) return { view: 'business', mock: mockBusiness };
  if (link.startsWith('calls-')) return { view: 'calls', mock: callsWithProvider };
  if (link.startsWith('commerce-')) return { view: 'commerce', mock: workspace };
  if (link.startsWith('finance-')) return { view: 'finance', mock: workspace };
  if (link === 'connections') return { view: 'integrations', mock: workspace };
  if (['notifications', 'language', 'privacy', 'appearance'].includes(link)) return { view: 'settings', mock: workspace };
  return { view: link, mock: workspace };
}

const pathOf = (hash: string) => hash.split('?')[0];
for (const link of DEEP_LINKS) {
  if (GATE_PAGES.some((item) => pathOf(item.hash) === link)) continue;
  GATE_PAGES.push({ hash: link, ...familyOf(link), ready: 'main h1' });
}

/** The gate's five sizes (CLAUDE.md, Responsive UI quality gate). */
export const GATE_SIZES = [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 1024, height: 768 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
] as const;

/** Let the page finish its requests after the ready selector shows (bounded: a page may poll). */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle', { timeout: 3_000 }).catch(() => undefined);
  await page.waitForFunction(() => !document.querySelector('main [aria-busy="true"]'), null, { timeout: 5_000 }).catch(() => undefined);
}

/** How many pixels the document scrolls sideways; 0 when nothing overflows. */
export const horizontalOverflow = (page: Page): Promise<number> => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

/**
 * Visible controls (buttons, links, fields) that are cut off, on either axis: outside the screen
 * sideways, above the top of the page, cut at the bottom of a fixed bar, or cut by an ancestor that
 * hides its overflow (`overflow-x` or `overflow-y` hidden or clip) without scrolling. A control inside
 * a container that really scrolls on that axis (a tab strip, a wide table, a scrolling list) can be
 * scrolled to, so it is not clipped on that axis. Sideways, any part outside counts; vertically a
 * control that is only partly cut counts, since a control moved wholly out of sight on purpose (a
 * collapsed panel, the skip link before it is focused) is hidden rather than clipped. Each entry
 * names the control so the CI log says what to fix.
 */
export const clippedControls = (page: Page): Promise<string[]> => page.evaluate(() => {
  const found: string[] = [];
  const width = document.documentElement.clientWidth;
  const height = window.innerHeight;
  const describe = (el: Element) => {
    const name = (el.getAttribute('aria-label') || (el as HTMLElement).innerText || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    const cls = typeof el.className === 'string' && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 2).join('.')}` : '';
    return `${el.tagName.toLowerCase()}${cls} "${name}"`;
  };
  const scrolls = (overflow: string, size: number, client: number) => (overflow === 'auto' || overflow === 'scroll') && size > client + 1;
  const hides = (overflow: string) => overflow === 'hidden' || overflow === 'clip';
  /** Partly inside and partly outside [start, end]. */
  const partlyOut = (low: number, high: number, start: number, end: number) => (low < start - 1 && high > start + 1) || (high > end + 1 && low < end - 1);
  const controls = document.querySelectorAll('button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="tab"]');
  for (const el of controls) {
    const box = el.getBoundingClientRect();
    if (box.width <= 2 || box.height <= 2) continue; // visually hidden (sr-only) or collapsed
    // Not drawn: display none, visibility hidden, transparent, or inside a closed <details>.
    if (!el.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
    if (el.closest('[hidden], [inert], [aria-hidden="true"], details:not([open]) > :not(summary), dialog:not([open])')) continue;
    let fixed = getComputedStyle(el).position === 'fixed';
    for (let parent = el.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      if (getComputedStyle(parent).position === 'fixed') fixed = true;
    }
    // A closed drawer (the narrow-screen sidebar) or a hidden toast waits off screen; it is not clipped.
    if (fixed && (box.right <= 0 || box.left >= width || box.bottom <= 0 || box.top >= height)) continue;
    let checkX = true;
    let checkY = true;
    let problem = '';
    for (let parent = el.parentElement; parent && parent !== document.body && (checkX || checkY); parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      const clip = parent.getBoundingClientRect();
      if (checkX) {
        if (scrolls(style.overflowX, parent.scrollWidth, parent.clientWidth)) checkX = false;
        else if (hides(style.overflowX) && (box.left < clip.left - 1 || box.right > clip.right + 1)) { problem = 'is cut off sideways by its container'; break; }
      }
      if (checkY) {
        if (scrolls(style.overflowY, parent.scrollHeight, parent.clientHeight)) checkY = false;
        else if (hides(style.overflowY) && partlyOut(box.top, box.bottom, clip.top, clip.bottom)) { problem = 'is cut off at the top or bottom by its container'; break; }
      }
    }
    if (!problem && checkX && (box.left < -1 || box.right > width + 1)) problem = `runs past the screen edge (${Math.round(box.left)} to ${Math.round(box.right)} of ${width})`;
    // The page scrolls down, so only the top of the page and a fixed bar's bottom edge can cut a control.
    const top = box.top + window.scrollY;
    if (!problem && checkY && top < -1 && top + box.height > 1) problem = `starts above the top of the page (${Math.round(top)})`;
    if (!problem && checkY && fixed && partlyOut(box.top, box.bottom, 0, height)) problem = `is cut off by the screen's top or bottom edge (${Math.round(box.top)} to ${Math.round(box.bottom)} of ${height})`;
    if (problem) found.push(`${describe(el)} ${problem}`);
  }
  return found;
});
