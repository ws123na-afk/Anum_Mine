// The Integrations page layout (docs/integrations-setup.md#the-integrations-page): one status
// summary with filters and search over both lists (connection setup and the services this
// deployment runs on), integrations grouped into a few categories, and one primary action per
// row. Pure, so `pnpm --filter @anum/web test:unit` covers the bucketing, grouping and wording.
import type { IntegrationState } from './catalog.ts';
import { groupName, itemName, itemText, type SetupItem, type SetupStatus } from './integrationSetup.ts';
import { CATALOGS, t, type MessageKey } from './i18n.ts';

/** The summary buckets. `off` (a service this deployment does not use) shows only under All. */
export type Bucket = 'connected' | 'setup' | 'error' | 'off';
export type PageFilter = 'all' | 'connected' | 'setup' | 'error';
export const PAGE_FILTERS: readonly PageFilter[] = ['all', 'connected', 'setup', 'error'];

/** Display categories, in page order. `services` holds the deployment's own service health. */
export const CATEGORY_ORDER = ['messaging', 'calls', 'ai', 'marketing', 'commerce', 'developer', 'platform', 'other', 'services'] as const;
export type Category = (typeof CATEGORY_ORDER)[number];

// The API's setup groups (services/api/anum_api/integration_setup.py) folded into fewer, broader
// categories; a few items read better next to their neighbours than in their API group.
const GROUP_CATEGORY: Record<string, Category> = {
  email: 'messaging', messaging: 'messaging', notifications: 'messaging',
  meetings: 'calls',
  ai: 'ai',
  marketing: 'marketing',
  commerce: 'commerce', operations: 'commerce',
  developer: 'developer', automation: 'developer',
  sign_in: 'platform', storage: 'platform', monitoring: 'platform',
};
const ITEM_CATEGORY: Record<string, Category> = { speech: 'calls', transcription: 'calls' };

export const setupCategory = (item: Pick<SetupItem, 'id' | 'group'>): Category => ITEM_CATEGORY[item.id] ?? GROUP_CATEGORY[item.group] ?? 'other';

export function setupBucket(status: SetupStatus): Bucket {
  if (status === 'connected') return 'connected';
  if (status === 'error') return 'error';
  return 'setup';
}

export function serviceBucket(status: IntegrationState['status']): Bucket {
  if (status === 'connected') return 'connected';
  if (status === 'degraded') return 'error';
  return 'off';
}

export type PrimaryAction = 'fix' | 'connect' | 'setUp' | 'manage' | 'details';

/** One action per row: what the person most likely wants next, given what their role may do. */
export function setupAction(item: SetupItem, canViewSetup: boolean): PrimaryAction {
  if (item.status === 'ready') return 'connect';
  if (item.status === 'connected') return canViewSetup || item.connect_view ? 'manage' : 'details';
  if (!canViewSetup) return 'details';
  return item.status === 'error' ? 'fix' : 'setUp';
}

export function serviceAction(item: Pick<IntegrationState, 'status'>, owner: boolean): PrimaryAction {
  if (!owner) return 'details';
  return item.status === 'degraded' ? 'fix' : 'manage';
}

export const actionLabel = (action: PrimaryAction): string => t(`setup.action.${action}`);
export const actionName = (action: PrimaryAction, name: string): string => t(`setup.action.${action}For`, { name });

const known = (key: string): key is MessageKey => Object.hasOwn(CATALOGS.en, key);

export const categoryName = (category: Category): string => t(`setup.cat.${category}`);

/** One line on what a deployment service is for, by kind; its health detail when the kind is new. */
export function servicePurpose(item: Pick<IntegrationState, 'kind' | 'detail'>): string {
  const key = `setup.kind.${item.kind}`;
  return known(key) ? t(key) : item.detail;
}

export interface Entry<T> {
  key: string;
  name: string;
  purpose: string;
  bucket: Bucket;
  category: Category;
  /** Folded text the search matches against. */
  haystack: string;
  value: T;
}

const fold = (text: string) => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();

export function setupEntry(item: SetupItem): Entry<SetupItem> {
  const category = setupCategory(item);
  return {
    key: `setup:${item.id}`, name: itemName(item.id), purpose: itemText(item.id), bucket: setupBucket(item.status), category,
    haystack: fold([item.id, itemName(item.id), itemText(item.id), groupName(item.group), categoryName(category), ...item.missing].join(' ')),
    value: item,
  };
}

export function serviceEntry(item: IntegrationState): Entry<IntegrationState> {
  return {
    key: `service:${item.id}`, name: item.name, purpose: servicePurpose(item), bucket: serviceBucket(item.status), category: 'services',
    haystack: fold([item.id, item.name, item.kind, servicePurpose(item), categoryName('services'), item.environment_variable ?? ''].join(' ')),
    value: item,
  };
}

export type Counts = Record<Bucket, number> & { total: number };

export function pageCounts(entries: readonly Pick<Entry<unknown>, 'bucket'>[]): Counts {
  const counts: Counts = { connected: 0, setup: 0, error: 0, off: 0, total: entries.length };
  for (const entry of entries) counts[entry.bucket] += 1;
  return counts;
}

export const matchesFilter = (bucket: Bucket, filter: PageFilter): boolean => filter === 'all' || bucket === filter;

/** Every query word must appear in the entry's name, description, category or setting names. */
export function matchesQuery(entry: Pick<Entry<unknown>, 'haystack'>, query: string): boolean {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return words.every((word) => entry.haystack.includes(word));
}

const BUCKET_ORDER: Record<Bucket, number> = { error: 0, setup: 1, connected: 2, off: 3 };

export interface Section<T> { category: Category; entries: T[]; counts: Counts; needsAction: boolean }

/** Filter, search, then group by category in page order; problems first inside each category. */
export function buildSections<T extends Entry<unknown>>(entries: readonly T[], filter: PageFilter, query: string): Section<T>[] {
  const shown = entries.filter((e) => matchesFilter(e.bucket, filter) && matchesQuery(e, query));
  const byCategory = new Map<Category, T[]>();
  for (const entry of shown) byCategory.set(entry.category, [...(byCategory.get(entry.category) ?? []), entry]);
  return CATEGORY_ORDER.filter((c) => byCategory.has(c)).map((category) => {
    const list = [...(byCategory.get(category) ?? [])].sort((a, b) => BUCKET_ORDER[a.bucket] - BUCKET_ORDER[b.bucket] || a.name.localeCompare(b.name));
    const counts = pageCounts(list);
    return { category, entries: list, counts, needsAction: counts.error + counts.setup > 0 };
  });
}

/**
 * Whether a section starts open: always while a filter or search narrows the page (so matches are
 * visible), otherwise only when something in it needs action. Sections where everything works
 * start collapsed.
 */
export const sectionStartsOpen = (section: Pick<Section<unknown>, 'needsAction'>, narrowed: boolean): boolean => narrowed || section.needsAction;

/** The short count line in a section header, for example "4 · 1 error · 2 need setup". */
export function sectionSummary(counts: Counts): string {
  const parts = [String(counts.total)];
  if (counts.error) parts.push(t('setup.sectionErrors', { count: counts.error }));
  if (counts.setup) parts.push(t('setup.sectionSetup', { count: counts.setup }));
  if (!counts.error && !counts.setup && counts.connected === counts.total) parts.push(t('setup.sectionAllConnected'));
  return parts.join(' · ');
}

export const filterLabel = (filter: PageFilter): string => t(`setup.filter.${filter}`);
