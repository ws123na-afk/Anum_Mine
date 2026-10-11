// Workspace files (docs/files.md): list paths, upload checks, labels, index state and a safe
// Markdown reader. Pure, so `pnpm --filter @anum/web test:unit` exercises it without a browser.
// Text comes from the message catalogs (docs/web-localization.md).
import { CATALOGS, t, type MessageKey } from './i18n.ts';

export type FileKind = 'text' | 'markdown' | 'data' | 'image' | 'pdf' | 'document' | 'archive';
export type IndexStatus = 'indexed' | 'stale' | 'failed' | 'skipped' | 'pending';

export interface ApiFileIndex { status: IndexStatus; error: string | null; chunk_count: number; embedding_model: string | null; indexed_at: string | null }

/** `GET /api/v1/files` row and `GET /api/v1/files/{id}`. */
export interface ApiFile {
  id: string; name: string; content_type: string; size_bytes: number; sha256: string;
  created_by: string; created_at: string; updated_by?: string | null; updated_at?: string | null;
  kind?: FileKind | null; previewable?: boolean; can_manage?: boolean; index?: ApiFileIndex | null;
}

/** `GET /api/v1/files/usage`. */
export interface ApiFileUsage {
  used_bytes: number; file_count: number; quota_bytes: number; max_upload_bytes: number; link_ttl_seconds: number;
  accepted: string; accepted_extensions: string[]; can_upload: boolean; can_manage_all: boolean;
}

export interface ApiFilePreview { kind: FileKind; text: string; truncated: boolean; size_bytes: number }
export interface ApiFileLink { url: string; expires_at: string }

export const PAGE_SIZE = 50;
export type SortValue = 'created_at:desc' | 'created_at:asc' | 'name:asc' | 'name:desc' | 'size:desc' | 'size:asc';
export const SORT_VALUES: readonly SortValue[] = ['created_at:desc', 'created_at:asc', 'name:asc', 'name:desc', 'size:desc', 'size:asc'];
/** The sort choices with their labels in the current language. */
export function sortOptions(): { value: SortValue; label: string }[] {
  return SORT_VALUES.map((value) => ({ value, label: t(`files.sort.${value}` as MessageKey) }));
}

/** `GET /api/v1/files` for one page: search, sort and the keyset cursor (the last id shown). */
export function filesListPath(options: { query?: string; sort?: SortValue; cursor?: string | null; limit?: number } = {}): string {
  const [sort, order] = (options.sort ?? 'created_at:desc').split(':');
  const params = new URLSearchParams({ limit: String(options.limit ?? PAGE_SIZE), sort, order });
  const query = options.query?.trim();
  if (query) params.set('q', query);
  if (options.cursor) params.set('cursor', options.cursor);
  return `/api/v1/files?${params.toString()}`;
}

/** Append a page, keeping the first copy of a file a racing reload already listed. */
export function mergePage(current: ApiFile[], page: ApiFile[]): ApiFile[] {
  const seen = new Set(current.map((file) => file.id));
  return [...current, ...page.filter((file) => !seen.has(file.id))];
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(1)} ${units[unit]}`;
}

/** "Markdown", "PNG image", "Word document"; an unknown type reads as its subtype. */
export function fileTypeLabel(contentType: string): string {
  const media = contentType.split(';', 1)[0].trim().toLowerCase();
  const key = `files.type.${media}`;
  if (key in CATALOGS.en) return t(key as MessageKey);
  const [major = '', minor = ''] = media.split('/');
  return (minor.replace(/^(x-|vnd\.)/, '').replace(/\+.*$/, '') || major || 'file').toUpperCase();
}

export type PreviewMode = 'text' | 'markdown' | 'image' | 'pdf';
/** How the detail panel previews a file, or null when only a download makes sense. */
export function previewMode(file: Pick<ApiFile, 'kind' | 'previewable'>): PreviewMode | null {
  if (!file.previewable) return null;
  switch (file.kind) {
    case 'markdown': return 'markdown';
    case 'text': case 'data': return 'text';
    case 'image': return 'image';
    case 'pdf': return 'pdf';
    default: return null;
  }
}

/** A `.statusBadge` tone class. */
export type Tone = 'success' | 'neutral' | 'warning' | 'dangerTone';
function skipReason(error: string | null): string {
  const key = `files.skip.${error ?? ''}`;
  if (key in CATALOGS.en) return t(key as MessageKey);
  return error ? error.replaceAll('_', ' ') : t('files.skip.other');
}

/** The retrieval index state of a file as a badge and a sentence (docs/memory.md#retrieval). */
export function indexBadge(index: ApiFileIndex | null | undefined): { label: string; tone: Tone; detail: string } {
  if (!index) return { label: t('files.index.unknown'), tone: 'neutral', detail: t('files.index.unknownDetail') };
  switch (index.status) {
    case 'indexed':
      return { label: t('files.index.indexed'), tone: 'success', detail: t('files.index.indexedDetail', { count: index.chunk_count }) };
    case 'stale':
      return { label: t('files.index.stale'), tone: 'warning', detail: t('files.index.staleDetail') };
    case 'failed':
      return {
        label: t('files.index.failed'), tone: 'dangerTone',
        detail: index.error ? t('files.index.failedDetailReason', { reason: index.error.replaceAll('_', ' ') }) : t('files.index.failedDetail'),
      };
    case 'skipped':
      return { label: t('files.index.skipped'), tone: 'neutral', detail: t('files.index.skippedDetail', { reason: skipReason(index.error) }) };
    default:
      return { label: t('files.index.pending'), tone: 'warning', detail: t('files.index.pendingDetail') };
  }
}

/** Re-indexing helps only when it could change the outcome. */
export function canReindex(index: ApiFileIndex | null | undefined): boolean {
  return !index || index.status === 'failed' || index.status === 'stale' || index.status === 'pending';
}

export function uploaderLabel(createdBy: string, currentUserId: string | null): string {
  return currentUserId && createdBy === currentUserId ? t('files.you') : createdBy;
}

function extension(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot).toLowerCase() : '';
}

/** A reason to refuse an upload before sending it, from the limits the API reported; null to send. */
export function uploadProblem(file: { name: string; size: number }, usage: ApiFileUsage | null): string | null {
  if (file.size === 0) return t('files.check.empty', { name: file.name });
  if (!usage) return null;
  if (file.size > usage.max_upload_bytes) return t('files.check.tooLarge', { name: file.name, size: formatBytes(file.size), max: formatBytes(usage.max_upload_bytes) });
  const ext = extension(file.name);
  if (!ext || !usage.accepted_extensions.includes(ext)) return t('files.check.type', { name: file.name, accepted: usage.accepted });
  if (usage.used_bytes + file.size > usage.quota_bytes) {
    return t('files.check.quota', { name: file.name, size: formatBytes(file.size), used: formatBytes(usage.used_bytes), quota: formatBytes(usage.quota_bytes) });
  }
  return null;
}

/** Why a new name would be refused, or null. A rename never changes the extension (the type). */
export function renameProblem(current: string, next: string): string | null {
  const name = next.trim();
  if (!name) return t('files.rename.empty');
  if (name.length > 255) return t('files.rename.tooLong');
  // eslint-disable-next-line no-control-regex
  if (/[\\/\u0000-\u001f\u007f]/.test(name) || name === '.' || name === '..') return t('files.rename.chars');
  if (extension(name) !== extension(current)) return extension(current) ? t('files.rename.keepExtension', { extension: extension(current) }) : t('files.rename.noExtension');
  if (name === current) return t('files.rename.same');
  return null;
}

/** Storage used against the workspace quota, for the meter. */
export function usageMeter(usage: ApiFileUsage): { percent: number; tone: 'ok' | 'warn' | 'stop'; text: string } {
  const raw = usage.quota_bytes > 0 ? (usage.used_bytes / usage.quota_bytes) * 100 : 0;
  const percent = Math.min(100, Math.max(0, raw));
  return {
    percent,
    tone: raw >= 95 ? 'stop' : raw >= 80 ? 'warn' : 'ok',
    text: t('files.storageText', { used: formatBytes(usage.used_bytes), quota: formatBytes(usage.quota_bytes), count: usage.file_count }),
  };
}

/** Upload progress as a sentence: "42% of 3.1 MB". */
export function progressText(loaded: number, total: number): string {
  if (total <= 0) return t('files.progressStarting');
  return t('files.progress', { percent: Math.min(100, Math.round((loaded / total) * 100)), size: formatBytes(total) });
}

// --------------------------------------------------------------------------- Markdown

export type Inline =
  | { type: 'text'; text: string }
  | { type: 'strong' | 'em'; children: Inline[] }
  | { type: 'code'; text: string }
  | { type: 'link'; href: string; children: Inline[] };

export type Block =
  | { type: 'heading'; level: 1 | 2 | 3 | 4 | 5 | 6; children: Inline[] }
  | { type: 'paragraph'; children: Inline[] }
  | { type: 'list'; ordered: boolean; items: Inline[][] }
  | { type: 'quote'; children: Inline[] }
  | { type: 'code'; text: string }
  | { type: 'rule' };

/** Only web and mail links become links; anything else (javascript:, data:) stays text. */
export function safeHref(href: string): string | null {
  const value = href.trim();
  return /^(https?:\/\/|mailto:)/i.test(value) ? value : null;
}

/** Inline Markdown: `code`, **strong**, *em* / _em_ and [text](url). Never HTML. */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*]+)\*|_([^_]+)_|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) out.push({ type: 'text', text: text.slice(last, index) });
    if (match[1] !== undefined) out.push({ type: 'code', text: match[1] });
    else if (match[2] !== undefined || match[3] !== undefined) out.push({ type: 'strong', children: parseInline(match[2] ?? match[3]) });
    else if (match[4] !== undefined || match[5] !== undefined) out.push({ type: 'em', children: parseInline(match[4] ?? match[5]) });
    else {
      const href = safeHref(match[7]);
      out.push(href ? { type: 'link', href, children: parseInline(match[6]) } : { type: 'text', text: match[0] });
    }
    last = index + match[0].length;
  }
  if (last < text.length) out.push({ type: 'text', text: text.slice(last) });
  return out;
}

/** Block Markdown into a tree the view renders as React elements (no innerHTML anywhere). */
export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => { if (paragraph.length) { blocks.push({ type: 'paragraph', children: parseInline(paragraph.join(' ')) }); paragraph = []; } };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fence = /^\s*(```|~~~)/.exec(line);
    if (fence) {
      flush();
      const body: string[] = [];
      for (i += 1; i < lines.length && !lines[i].trimStart().startsWith(fence[1]); i += 1) body.push(lines[i]);
      blocks.push({ type: 'code', text: body.join('\n') });
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (heading) { flush(); blocks.push({ type: 'heading', level: heading[1].length as 1 | 2 | 3 | 4 | 5 | 6, children: parseInline(heading[2]) }); continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); blocks.push({ type: 'rule' }); continue; }
    const item = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (item) {
      flush();
      const ordered = /\d/.test(item[1]);
      const items: Inline[][] = [parseInline(item[2])];
      while (i + 1 < lines.length) {
        const next = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i + 1]);
        if (!next || /\d/.test(next[1]) !== ordered) break;
        items.push(parseInline(next[2]));
        i += 1;
      }
      blocks.push({ type: 'list', ordered, items });
      continue;
    }
    const quote = /^\s*>\s?(.*)$/.exec(line);
    if (quote) { flush(); blocks.push({ type: 'quote', children: parseInline(quote[1]) }); continue; }
    if (!line.trim()) { flush(); continue; }
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
}
