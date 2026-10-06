// Unit tests for the "Sources used" helpers in src/lib/retrieval.ts.
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AgentRun } from '@anum/contracts';
import { groupSources, runSources, scoreLabel, sourceLabel, sourcesNote } from '../src/lib/retrieval.ts';

const at = '2026-10-06T12:00:00Z';

function run(metadata?: Record<string, unknown>): AgentRun {
  const steps: AgentRun['steps'] = [{ id: 'step_model', type: 'model_call', summary: 'Planned.', createdAt: at, metadata: {} }];
  if (metadata) steps.unshift({ id: 'step_retrieval', type: 'retrieval', summary: 'Used passages.', createdAt: at, metadata });
  return { id: 'run_1', taskId: 'task_1', status: 'completed', steps };
}

describe('runSources', () => {
  test('returns null without a run or without a retrieval step (nothing is invented)', () => {
    assert.equal(runSources(null), null);
    assert.equal(runSources(run()), null);
  });

  test('reads chunk ids, sources and scores from the retrieval step', () => {
    const record = runSources(
      run({
        status: 'ok',
        embedding_model: 'anum-local-hash-v1',
        truncated: false,
        sources: [
          { chunk_id: 'chunk_a', source_type: 'memory', source_id: 'memory_1', chunk_index: 0, score: 0.81, truncated: false },
          { chunk_id: 'chunk_b', source_type: 'file', source_id: 'file_1', chunk_index: 2, score: 0.4, truncated: true },
        ],
      }),
    );
    assert.ok(record);
    assert.equal(record.state, 'ok');
    assert.equal(record.embeddingModel, 'anum-local-hash-v1');
    assert.deepEqual(record.sources.map((source) => [source.sourceType, source.sourceId, source.chunkIndex, source.truncated]), [
      ['memory', 'memory_1', 0, false],
      ['file', 'file_1', 2, true],
    ]);
  });

  test('drops malformed entries and unknown source types', () => {
    const record = runSources(
      run({
        status: 'ok',
        sources: [
          { chunk_id: 'chunk_a', source_type: 'web', source_id: 'x', chunk_index: 0, score: 1 },
          { source_type: 'memory', source_id: 'memory_1' },
          'not an object',
          { chunk_id: 'chunk_c', source_type: 'memory', source_id: 'memory_2', chunk_index: 1, score: 'high' },
        ],
      }),
    );
    assert.ok(record);
    assert.deepEqual(record.sources, [
      { chunkId: 'chunk_c', sourceType: 'memory', sourceId: 'memory_2', chunkIndex: 1, score: 0, truncated: false },
    ]);
  });

  test('an unknown status reads as unavailable', () => {
    assert.equal(runSources(run({ status: 'something-else', sources: [] }))?.state, 'unavailable');
  });
});

describe('presentation helpers', () => {
  test('groups passages by source in first-used order', () => {
    const groups = groupSources([
      { chunkId: 'a', sourceType: 'file', sourceId: 'file_1', chunkIndex: 0, score: 0.9, truncated: false },
      { chunkId: 'b', sourceType: 'memory', sourceId: 'memory_1', chunkIndex: 0, score: 0.8, truncated: false },
      { chunkId: 'c', sourceType: 'file', sourceId: 'file_1', chunkIndex: 3, score: 0.5, truncated: false },
    ]);
    assert.deepEqual(groups.map((group) => [group.sourceId, group.passages.length]), [['file_1', 2], ['memory_1', 1]]);
  });

  test('labels and notes', () => {
    assert.equal(sourceLabel('memory'), 'Memory');
    assert.equal(sourceLabel('file'), 'File');
    assert.equal(scoreLabel(0.456), '46% match');
    assert.equal(scoreLabel(1.7), '100% match');
    assert.equal(scoreLabel(-0.2), '0% match');
    const base = { sources: [], truncated: false, embeddingModel: null };
    assert.equal(sourcesNote({ ...base, state: 'no_results', reason: null }), 'No indexed memory or file matched this task.');
    assert.equal(
      sourcesNote({ ...base, state: 'skipped', reason: 'the caller may not read workspace memory' }),
      'Retrieval skipped: the caller may not read workspace memory.',
    );
    assert.equal(sourcesNote({ ...base, state: 'ok', reason: null }), '');
    assert.match(sourcesNote({ ...base, state: 'ok', reason: null, truncated: true }), /shortened/);
    assert.match(sourcesNote({ ...base, state: 'unavailable', reason: null }), /without workspace context/);
  });
});
