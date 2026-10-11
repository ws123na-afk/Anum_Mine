// Workspace files (docs/files.md): list paths, upload checks, labels, index state and a safe
// Markdown reader. Pure, so `pnpm --filter @anum/web test:unit` exercises it without a browser.

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
export const SORT_OPTIONS: readonly { value: SortValue; label: string }[] = [
  { value: 'created_at:desc', label: 'Newest first' },
  { value: 'created_at:asc', label: 'Oldest first' },
  { value: 'name:asc', label: 'Name, A to Z' },
  { value: 'name:desc', label: 'Name, Z to A' },
  { value: 'size:desc', label: 'Largest first' },
  { value: 'size:asc', label: 'Smallest first' },
];

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

const TYPE_LABELS: Record<string, string> = {
  'text/plain': 'Text', 'text/markdown': 'Markdown', 'text/x-rst': 'reStructuredText', 'text/csv': 'CSV',
  'text/tab-separated-values': 'TSV', 'application/json': 'JSON', 'application/yaml': 'YAML', 'application/xml': 'XML',
  'image/png': 'PNG image', 'image/jpeg': 'JPEG image', 'image/gif': 'GIF image', 'image/webp': 'WebP image',
  'application/pdf': 'PDF', 'application/zip': 'ZIP archive',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'Excel workbook',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PowerPoint deck',
  'application/vnd.oasis.opendocument.text': 'OpenDocument text',
  'application/vnd.oasis.opendocument.spreadsheet': 'OpenDocument sheet',
  'application/vnd.oasis.opendocument.presentation': 'OpenDocument slides',
};

/** "Markdown", "PNG image", "Word document"; an unknown type reads as its subtype. */
export function fileTypeLabel(contentType: string): string {
  const media = contentType.split(';', 1)[0].trim().toLowerCase();
  const known = TYPE_LABELS[media];
  if (known) return known;
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
const SKIP_REASONS: Record<string, string> = {
  not_text: 'only text files are indexed', too_large: 'it has more than 2 MiB of text', not_utf8: 'it is not UTF-8 text', empty: 'it has no text',
};

/** The retrieval index state of a file as a badge and a sentence (docs/memory.md#retrieval). */
export function indexBadge(index: ApiFileIndex | null | undefined): { label: string; tone: Tone; detail: string } {
  if (!index) return { label: 'Index unknown', tone: 'neutral', detail: 'The index state could not be read. Reload to try again.' };
  switch (index.status) {
    case 'indexed':
      return { label: 'Indexed', tone: 'success', detail: `Tasks can use this file (${index.chunk_count} passage${index.chunk_count === 1 ? '' : 's'}).` };
    case 'stale':
      return { label: 'Re-index needed', tone: 'warning', detail: 'Indexed with an earlier embedding model; tasks will not find it until it is indexed again.' };
    case 'failed':
      return { label: 'Indexing failed', tone: 'dangerTone', detail: `Indexing failed${index.error ? ` (${index.error.replaceAll('_', ' ')})` : ''}. Re-index to try again.` };
    case 'skipped':
      return { label: 'Not indexed', tone: 'neutral', detail: `Not indexed: ${SKIP_REASONS[index.error ?? ''] ?? (index.error ?? 'not indexable').replaceAll('_', ' ')}.` };
    default:
      return { label: 'Not indexed yet', tone: 'warning', detail: 'This file has not been indexed yet. Re-index so tasks can use it.' };
  }
}

/** Re-indexing helps only when it could change the outcome. */
export function canReindex(index: ApiFileIndex | null | undefined): boolean {
  return !index || index.status === 'failed' || index.status === 'stale' || index.status === 'pending';
}

export function uploaderLabel(createdBy: string, currentUserId: string | null): string {
  return currentUserId && createdBy === currentUserId ? 'You' : createdBy;
}

function extension(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot).toLowerCase() : '';
}

/** A reason to refuse an upload before sending it, from the limits the API reported; null to send. */
export function uploadProblem(file: { name: string; size: number }, usage: ApiFileUsage | null): string | null {
  if (file.size === 0) return `${file.name} is empty. Choose a file with content.`;
  if (!usage) return null;
  if (file.size > usage.max_upload_bytes) return `${file.name} is ${formatBytes(file.size)}; files can be at most ${formatBytes(usage.max_upload_bytes)}. Split or compress it.`;
  const ext = extension(file.name);
  if (!ext || !usage.accepted_extensions.includes(ext)) return `${file.name} cannot be uploaded: this workspace accepts ${usage.accepted}.`;
  if (usage.used_bytes + file.size > usage.quota_bytes) {
    return `${file.name} (${formatBytes(file.size)}) does not fit: ${formatBytes(usage.used_bytes)} of ${formatBytes(usage.quota_bytes)} is used. Delete files you no longer need.`;
  }
  return null;
}

/** Why a new name would be refused, or null. A rename never changes the extension (the type). */
export function renameProblem(current: string, next: string): string | null {
  const name = next.trim();
  if (!name) return 'Enter a name.';
  if (name.length > 255) return 'Names are at most 255 characters.';
  // eslint-disable-next-line no-control-regex
  if (/[\\/\u0000-\u001f\u007f]/.test(name) || name === '.' || name === '..') return 'Names cannot contain / or \\ or control characters.';
  if (extension(name) !== extension(current)) return extension(current) ? `Keep the ${extension(current)} extension: renaming does not change the file type.` : 'Do not add an extension: renaming does not change the file type.';
  if (name === current) return 'Enter a different name.';
  return null;
}

/** Storage used against the workspace quota, for the meter. */
export function usageMeter(usage: ApiFileUsage): { percent: number; tone: 'ok' | 'warn' | 'stop'; text: string } {
  const raw = usage.quota_bytes > 0 ? (usage.used_bytes / usage.quota_bytes) * 100 : 0;
  const percent = Math.min(100, Math.max(0, raw));
  return {
    percent,
    tone: raw >= 95 ? 'stop' : raw >= 80 ? 'warn' : 'ok',
    text: `${formatBytes(usage.used_bytes)} of ${formatBytes(usage.quota_bytes)} used · ${usage.file_count} file${usage.file_count === 1 ? '' : 's'}`,
  };
}

/** Upload progress as a sentence: "42% of 3.1 MB". */
export function progressText(loaded: number, total: number): string {
  if (total <= 0) return 'Starting...';
  return `${Math.min(100, Math.round((loaded / total) * 100))}% of ${formatBytes(total)}`;
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
