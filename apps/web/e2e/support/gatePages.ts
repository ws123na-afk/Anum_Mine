import type { Page } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';
import { mockMarketing } from './marketingApi';
import { mockWhatsApp } from './whatsappApi';
import { mockCallsApi } from './callsApi';
import { mockBusiness } from './businessApi';
import { mockTyre } from './tyreApi';
import { mockMailApi } from './mailApi';
import { mockDeveloper } from './developerApi';

// Every page the web app can open (src/lib/shell.ts VIEWS), plus the tabs and deep links that draw a
// different layout, for the UI quality gates: e2e/page-layout.spec.ts (overflow and clipped controls),
// e2e/accessibility.spec.ts (axe) and e2e/journey.spec.ts (no dead buttons). Each entry names the mock
// that fills the page and a selector that is only visible once the page shows its data, so a check
// never runs against a loading state. gate-coverage in page-layout.spec.ts fails when a view in
// shell.ts has no entry here.
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
  { hash: 'business-pipeline', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-proposals', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-projects', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-catalog', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
  { hash: 'business-settings', view: 'business', mock: mockBusiness, ready: '.bzTabs [aria-current="page"]' },
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
];

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
}

/** How many pixels the document scrolls sideways; 0 when nothing overflows. */
export const horizontalOverflow = (page: Page): Promise<number> => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

/**
 * Visible controls (buttons, links, fields) that are cut off: outside the viewport sideways, or cut
 * by an ancestor that hides its overflow without scrolling. A control inside a container that
 * scrolls sideways (a tab strip, a wide table) can be scrolled to, so it is not clipped. Each entry
 * names the control so the CI log says what to fix.
 */
export const clippedControls = (page: Page): Promise<string[]> => page.evaluate(() => {
  const found: string[] = [];
  const width = document.documentElement.clientWidth;
  const describe = (el: Element) => {
    const name = (el.getAttribute('aria-label') || (el as HTMLElement).innerText || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    const cls = typeof el.className === 'string' && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 2).join('.')}` : '';
    return `${el.tagName.toLowerCase()}${cls} "${name}"`;
  };
  const scrollsSideways = (el: Element) => {
    const style = getComputedStyle(el);
    return (style.overflowX === 'auto' || style.overflowX === 'scroll') && el.scrollWidth > el.clientWidth + 1;
  };
  const controls = document.querySelectorAll('button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="tab"]');
  for (const el of controls) {
    const box = el.getBoundingClientRect();
    if (box.width <= 2 || box.height <= 2) continue; // visually hidden (sr-only) or collapsed
    // Not drawn: display none, visibility hidden, transparent, or inside a closed <details>.
    if (!el.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
    const style = getComputedStyle(el);
    if (el.closest('[hidden], [inert], [aria-hidden="true"], details:not([open]) > :not(summary), dialog:not([open])')) continue;
    let scrolled = false;
    let clipped = false;
    let fixed = false;
    for (let parent = el.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      if (getComputedStyle(parent).position === 'fixed') fixed = true;
    }
    // A closed drawer (the narrow-screen sidebar) waits off screen; it is not a clipped control.
    if ((fixed || style.position === 'fixed') && (box.right <= 0 || box.left >= width)) continue;
    for (let parent = el.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      if (scrollsSideways(parent)) { scrolled = true; break; }
      const parentStyle = getComputedStyle(parent);
      if (parentStyle.overflowX === 'hidden' || parentStyle.overflowX === 'clip') {
        const clip = parent.getBoundingClientRect();
        if (box.left < clip.left - 1 || box.right > clip.right + 1) { clipped = true; break; }
      }
    }
    if (scrolled) continue;
    if (clipped) { found.push(`${describe(el)} is cut off by its container`); continue; }
    if (box.left < -1 || box.right > width + 1) found.push(`${describe(el)} runs past the screen edge (${Math.round(box.left)} to ${Math.round(box.right)} of ${width})`);
  }
  return found;
});
