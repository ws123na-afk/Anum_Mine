// "Sources used" on a run: read from the run's `retrieval` step, which the API records
// with chunk ids, source ids and scores only (never the retrieved text). Nothing here
// invents data: a run without a retrieval step shows nothing.
import type { AgentRun, RetrievalSourceName, RetrievedSourceUsed } from '@anum/contracts';
import { t } from './i18n.ts';

export type RetrievalState = 'ok' | 'no_results' | 'skipped' | 'unavailable';

export interface RunSources {
  state: RetrievalState;
  sources: RetrievedSourceUsed[];
  reason: string | null;
  truncated: boolean;
  embeddingModel: string | null;
}

const STATES: readonly RetrievalState[] = ['ok', 'no_results', 'skipped', 'unavailable'];

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function parseSource(value: unknown): RetrievedSourceUsed | null {
  const item = asRecord(value);
  if (!item) return null;
  const sourceType = item.source_type;
  if (sourceType !== 'memory' && sourceType !== 'file') return null;
  if (typeof item.chunk_id !== 'string' || typeof item.source_id !== 'string') return null;
  const chunkIndex = typeof item.chunk_index === 'number' ? item.chunk_index : 0;
  const score = typeof item.score === 'number' && Number.isFinite(item.score) ? item.score : 0;
  return { chunkId: item.chunk_id, sourceType, sourceId: item.source_id, chunkIndex, score, truncated: item.truncated === true };
}

/** The run's retrieval record, or null when the run has no retrieval step. */
export function runSources(run: AgentRun | null): RunSources | null {
  const step = run?.steps.find((candidate) => candidate.type === 'retrieval');
  const metadata = asRecord(step?.metadata);
  if (!step || !metadata) return null;
  const state = STATES.includes(metadata.status as RetrievalState) ? (metadata.status as RetrievalState) : 'unavailable';
  const raw = Array.isArray(metadata.sources) ? metadata.sources : [];
  return {
    state,
    sources: raw.map(parseSource).filter((source): source is RetrievedSourceUsed => source !== null),
    reason: typeof metadata.reason === 'string' ? metadata.reason : null,
    truncated: metadata.truncated === true,
    embeddingModel: typeof metadata.embedding_model === 'string' ? metadata.embedding_model : null,
  };
}

/** Passages grouped by source, in first-used order. */
export function groupSources(sources: RetrievedSourceUsed[]): Array<{ sourceType: 'memory' | 'file'; sourceId: string; passages: RetrievedSourceUsed[] }> {
  const groups = new Map<string, { sourceType: 'memory' | 'file'; sourceId: string; passages: RetrievedSourceUsed[] }>();
  for (const source of sources) {
    const key = `${source.sourceType}:${source.sourceId}`;
    const group = groups.get(key) ?? { sourceType: source.sourceType, sourceId: source.sourceId, passages: [] };
    group.passages.push(source);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function sourceLabel(sourceType: 'memory' | 'file'): string {
  return sourceType === 'memory' ? t('sources.memory') : t('sources.file');
}

/** One line explaining an empty or degraded retrieval. */
export function sourcesNote(record: RunSources): string {
  switch (record.state) {
    case 'ok':
      return record.truncated ? t('sources.truncated') : '';
    case 'no_results':
      return t('sources.noResults');
    case 'skipped':
      return record.reason ? t('sources.skippedReason', { reason: record.reason }) : t('sources.skipped');
    default:
      return t('sources.unavailable');
  }
}

export function scoreLabel(score: number): string {
  return t('sources.match', { percent: Math.round(Math.max(0, Math.min(1, score)) * 100) });
}

// ---------------------------------------------------------------------------
// Names for the sources: GET /api/v1/retrieval/sources resolves every source of a run in one
// bounded request (never one per source). A source that is gone, expired or not visible to the
// caller comes back `found: false` and is shown by its id, without a link.

export type SourceGroup = ReturnType<typeof groupSources>[number];

/** One resolved source (packages/contracts/src/account.ts). */
export type SourceName = RetrievalSourceName;

/** The API refuses more than this many ids in one lookup; a run lists at most 20 passages. */
export const MAX_SOURCE_LOOKUP = 50;

/** Path naming every distinct source of the groups (bounded), or null when there are none. */
export function sourceNamesPath(groups: Array<Pick<SourceGroup, 'sourceType' | 'sourceId'>>): string | null {
  const query = new URLSearchParams();
  for (const group of groups.slice(0, MAX_SOURCE_LOOKUP)) query.append(group.sourceType === 'memory' ? 'memory_id' : 'file_id', group.sourceId);
  const text = query.toString();
  return text ? `/api/v1/retrieval/sources?${text}` : null;
}

/** Parse the lookup's body, dropping malformed entries rather than guessing. */
export function parseSourceNames(body: unknown): SourceName[] {
  const list = asRecord(body)?.sources;
  if (!Array.isArray(list)) return [];
  return list.flatMap((value): SourceName[] => {
    const item = asRecord(value);
    if (!item || (item.source_type !== 'memory' && item.source_type !== 'file') || typeof item.source_id !== 'string') return [];
    return [{
      sourceType: item.source_type,
      sourceId: item.source_id,
      found: item.found === true,
      title: typeof item.title === 'string' && item.title ? item.title : null,
      kind: typeof item.kind === 'string' && item.kind ? item.kind : null,
      sizeBytes: typeof item.size_bytes === 'number' ? item.size_bytes : null,
    }];
  });
}

export function sourceKey(sourceType: 'memory' | 'file', sourceId: string): string {
  return `${sourceType}:${sourceId}`;
}

export function sourceNameMap(names: SourceName[]): Map<string, SourceName> {
  return new Map(names.map((name) => [sourceKey(name.sourceType, name.sourceId), name]));
}

const FILE_KINDS: Record<string, string> = {
  'text/markdown': 'Markdown', 'text/plain': 'Text', 'text/csv': 'CSV', 'text/html': 'HTML', 'application/json': 'JSON',
  'application/pdf': 'PDF', 'application/xml': 'XML', 'text/xml': 'XML', 'application/yaml': 'YAML', 'text/yaml': 'YAML',
};

/** A readable kind: "Task memory", "Memory from your chats", or a file type such as "Markdown file". */
export function sourceKindLabel(name: Pick<SourceName, 'sourceType' | 'kind'>): string {
  if (name.sourceType === 'memory') return name.kind === 'chat' ? t('sources.kind.chatMemory') : t('sources.kind.taskMemory');
  if (!name.kind) return t('sources.file');
  const known = FILE_KINDS[name.kind.toLowerCase()];
  if (known) return t('sources.kind.fileOf', { kind: known === 'Text' ? t('sources.kind.text') : known });
  const [major = '', minor = ''] = name.kind.split('/');
  const label = minor.replace(/^(x-|vnd\.)/, '').replace(/\+.*$/, '') || major;
  return t('sources.kind.fileOf', { kind: label.toUpperCase() });
}

export interface SourceDisplay {
  /** The name to show: a memory's first line or a file's name, else the source id. */
  title: string;
  kind: string;
  /** The lookup found it, so it can be opened. */
  found: boolean;
  /** The lookup answered for this source (found or not). */
  resolved: boolean;
}

/** How one source row reads: its name and kind when the lookup found it, else its id. */
export function sourceDisplay(group: Pick<SourceGroup, 'sourceType' | 'sourceId'>, names: ReadonlyMap<string, SourceName> | null): SourceDisplay {
  const name = names?.get(sourceKey(group.sourceType, group.sourceId));
  if (!name) return { title: group.sourceId, kind: sourceLabel(group.sourceType), found: false, resolved: false };
  if (!name.found) return { title: group.sourceId, kind: t('sources.noLongerAvailable', { kind: sourceLabel(group.sourceType) }), found: false, resolved: true };
  return { title: name.title ?? group.sourceId, kind: sourceKindLabel(name), found: true, resolved: true };
}
