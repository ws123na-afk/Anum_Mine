// Pure helpers for Favorites, the Knowledge base and Reports (docs/knowledge-base.md,
// docs/reports.md): labels, deep links, the chat report block, metric formatting.
// No browser APIs, so `pnpm test:unit` exercises them directly.
import type { FavoriteType, KnowledgeCitation, ReportCard, ReportCreatedByKind, ReportMetric } from '@anum/contracts';
import { formatDateTime, t, type MessageKey } from './i18n.ts';

export const FAVORITE_TYPES: FavoriteType[] = ['conversation', 'task', 'report', 'collection', 'file', 'memory', 'automation', 'agent'];
export const FAVORITES_PAGE = 30;
export const REPORTS_PAGE = 20;
export const ITEMS_PAGE = 50;
export const KNOWN_REPORT_TYPES = ['tyre', 'commerce', 'commerce_summary', 'tyre_summary', 'task_summary', 'automation_summary', 'marketing', 'custom'] as const;

/** Where in a document a cited passage is: "page 3", "slide 2", "sheet 1 (Budget)"; empty for notes and text. */
export function citationPlace(citation: Pick<KnowledgeCitation, 'page' | 'page_unit' | 'page_label'>): string {
  const { page, page_unit: unit, page_label: label } = citation;
  if (page == null || !unit) return '';
  const place = t(`kb.location.${unit}` as MessageKey, { n: page });
  return label ? `${place} (${label})` : place;
}

export function favoriteTypeLabel(type: FavoriteType): string {
  return t(`library.type.${type}` as MessageKey);
}

/** A report type as words: the known ones translated, any other slug humanised. */
export function reportTypeLabel(type: string): string {
  return (KNOWN_REPORT_TYPES as readonly string[]).includes(type)
    ? t(`reports.type.${type}` as MessageKey)
    : type.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase());
}

export function createdByLabel(kind: ReportCreatedByKind): string {
  return t(`reports.by.${kind}` as MessageKey);
}

/** Where opening a favorite goes: the app view and the hash that deep-links the item. */
export function favoriteTarget(type: FavoriteType, id: string): { view: string; hash: string } {
  switch (type) {
    case 'report': return { view: 'reports', hash: `#reports/${encodeURIComponent(id)}` };
    case 'collection': return { view: 'knowledge', hash: `#knowledge/${encodeURIComponent(id)}` };
    case 'conversation': return { view: 'assistant', hash: '#assistant' };
    case 'task': return { view: 'tasks', hash: '#tasks' };
    case 'file': return { view: 'files', hash: '#files' };
    case 'memory': return { view: 'memory', hash: '#memory' };
    case 'automation': return { view: 'automation', hash: '#automation' };
    case 'agent': return { view: 'agents', hash: '#agents' };
  }
}

/** `#reports/<id>` or `#knowledge/<id>`: the id, else null. */
export function hashTarget(hash: string, view: 'reports' | 'knowledge'): string | null {
  const prefix = `#${view}/`;
  if (!hash.startsWith(prefix)) return null;
  try {
    const id = decodeURIComponent(hash.slice(prefix.length));
    return id && /^[\w-]{1,80}$/.test(id) ? id : null;
  } catch {
    return null;
  }
}

const BLOCK_START = /\n?```anum-report[^\n]*(\n|$)/;

/**
 * A reply as the reader should see it: without the `anum-report` block the server turns into
 * a stored report and a card. While streaming the block may be incomplete; it is hidden from
 * its first line on.
 */
export function visibleReply(text: string): string {
  const match = BLOCK_START.exec(text);
  if (!match) {
    // The fence may still be arriving ("```anum-rep..."): hold back a trailing partial fence.
    const partial = text.lastIndexOf('```');
    if (partial >= 0 && '```anum-report'.startsWith(text.slice(partial).trimEnd()) && text.slice(partial).length > 3) return text.slice(0, partial).trimEnd();
    return text;
  }
  const after = text.slice(match.index + match[0].length);
  const close = after.indexOf('\n```');
  const rest = close >= 0 ? after.slice(close + 4) : '';
  return (text.slice(0, match.index) + rest).trimEnd();
}

export function metricValue(metric: ReportMetric): string {
  return metric.unit ? `${metric.value} ${metric.unit}` : metric.value;
}

/** "+8.5%" / "-3%"; null when there is no earlier period to compare with. */
export function changeLabel(change: number | null | undefined): string | null {
  if (change === null || change === undefined || !Number.isFinite(change)) return null;
  const rounded = Math.round(change * 10) / 10;
  return `${rounded > 0 ? '+' : ''}${rounded}%`;
}

export function changeTone(change: number | null | undefined): 'up' | 'down' | 'flat' {
  if (!change) return 'flat';
  return change > 0 ? 'up' : 'down';
}

const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { dateStyle: 'medium', timeZone: 'UTC' });

export function periodLabel(start: string | null, end: string | null): string | null {
  if (start && end) return start === end ? day(start) : t('reports.period', { start: day(start), end: day(end) });
  if (start) return t('reports.periodFrom', { start: day(start) });
  if (end) return t('reports.periodTo', { end: day(end) });
  return null;
}

/** Tags typed as "a, b , c": trimmed, lowercased, unique, at most 12. */
export function parseTags(text: string): string[] {
  const seen: string[] = [];
  for (const raw of text.split(',')) {
    const tag = raw.trim().toLowerCase().slice(0, 40);
    if (tag && !seen.includes(tag)) seen.push(tag);
  }
  return seen.slice(0, 12);
}

export function fileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '';
  if (bytes < 1024) return t('library.bytes', { n: bytes });
  if (bytes < 1024 * 1024) return t('library.kb', { n: Math.round(bytes / 102.4) / 10 });
  return t('library.mb', { n: Math.round(bytes / 104857.6) / 10 });
}

/** The report cards of a stored conversation, by assistant turn id. */
export function cardsByTurn(cards: ReportCard[] | undefined): Map<string, ReportCard> {
  const map = new Map<string, ReportCard>();
  for (const card of cards ?? []) if (card.turn_id) map.set(card.turn_id, card);
  return map;
}

/** The file name from a Content-Disposition header, with a fallback. */
export function downloadName(header: string | null, fallback: string): string {
  const match = /filename="?([^";]+)"?/i.exec(header ?? '');
  return match ? match[1] : fallback;
}
