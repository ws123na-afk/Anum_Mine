// "Sources used" on a run: read from the run's `retrieval` step, which the API records
// with chunk ids, source ids and scores only (never the retrieved text). Nothing here
// invents data: a run without a retrieval step shows nothing.
import type { AgentRun, RetrievedSourceUsed } from '@anum/contracts';

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
  return sourceType === 'memory' ? 'Memory' : 'File';
}

/** One line explaining an empty or degraded retrieval. */
export function sourcesNote(record: RunSources): string {
  switch (record.state) {
    case 'ok':
      return record.truncated ? 'Some retrieved text was shortened to fit the prompt limit.' : '';
    case 'no_results':
      return 'No indexed memory or file matched this task.';
    case 'skipped':
      return `Retrieval skipped${record.reason ? `: ${record.reason}` : ''}.`;
    default:
      return 'Retrieval was unavailable; the task ran without workspace context.';
  }
}

export function scoreLabel(score: number): string {
  return `${Math.round(Math.max(0, Math.min(1, score)) * 100)}% match`;
}
