// Unit tests for the "Sources used" helpers in src/lib/retrieval.ts.
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AgentRun } from '@anum/contracts';
import {
  groupSources, MAX_SOURCE_LOOKUP, parseSourceNames, runSources, scoreLabel, sourceDisplay, sourceKindLabel, sourceLabel, sourceNameMap,
  sourceNamesPath, sourcesNote,
} from '../src/lib/retrieval.ts';

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

describe('source names', () => {
  test('one bounded lookup path names every distinct source once, memories and files apart', () => {
    assert.equal(sourceNamesPath([]), null);
    assert.equal(
      sourceNamesPath([{ sourceType: 'memory', sourceId: 'memory_1' }, { sourceType: 'file', sourceId: 'file 2&x' }]),
      '/api/v1/retrieval/sources?memory_id=memory_1&file_id=file+2%26x',
    );
    const many = Array.from({ length: MAX_SOURCE_LOOKUP + 5 }, (_, index) => ({ sourceType: 'memory' as const, sourceId: `memory_${index}` }));
    assert.equal(new URL(sourceNamesPath(many)!, 'http://x').searchParams.getAll('memory_id').length, MAX_SOURCE_LOOKUP);
  });

  test('parses the lookup and drops malformed entries', () => {
    const names = parseSourceNames({
      sources: [
        { source_type: 'memory', source_id: 'memory_1', found: true, title: 'Launch notes', kind: 'task_note', size_bytes: null },
        { source_type: 'file', source_id: 'file_1', found: false, title: null, kind: null },
        { source_type: 'agent', source_id: 'x', found: true },
        { source_type: 'file' },
        'junk',
      ],
    });
    assert.deepEqual(names.map((name) => [name.sourceId, name.found, name.title]), [['memory_1', true, 'Launch notes'], ['file_1', false, null]]);
    assert.deepEqual(parseSourceNames(null), []);
    assert.deepEqual(parseSourceNames({ sources: 'no' }), []);
  });

  test('kinds read as a person would say them', () => {
    assert.equal(sourceKindLabel({ sourceType: 'memory', kind: 'task_note' }), 'Task memory');
    assert.equal(sourceKindLabel({ sourceType: 'memory', kind: 'chat' }), 'Memory from your chats');
    assert.equal(sourceKindLabel({ sourceType: 'file', kind: 'text/markdown' }), 'Markdown file');
    assert.equal(sourceKindLabel({ sourceType: 'file', kind: 'application/pdf' }), 'PDF file');
    assert.equal(sourceKindLabel({ sourceType: 'file', kind: 'application/vnd.ms-excel' }), 'MS-EXCEL file');
    assert.equal(sourceKindLabel({ sourceType: 'file', kind: 'application/ld+json' }), 'LD file');
    assert.equal(sourceKindLabel({ sourceType: 'file', kind: null }), 'File');
  });

  test('a row shows the name when found, else the id and that it is gone', () => {
    const names = sourceNameMap(parseSourceNames({ sources: [
      { source_type: 'memory', source_id: 'memory_1', found: true, title: 'Launch notes', kind: 'task_note' },
      { source_type: 'file', source_id: 'file_gone', found: false },
      { source_type: 'file', source_id: 'file_untitled', found: true, title: null, kind: 'text/plain' },
    ] }));
    assert.deepEqual(sourceDisplay({ sourceType: 'memory', sourceId: 'memory_1' }, names), { title: 'Launch notes', kind: 'Task memory', found: true, resolved: true });
    assert.deepEqual(sourceDisplay({ sourceType: 'file', sourceId: 'file_gone' }, names), { title: 'file_gone', kind: 'File no longer available', found: false, resolved: true });
    assert.deepEqual(sourceDisplay({ sourceType: 'file', sourceId: 'file_untitled' }, names), { title: 'file_untitled', kind: 'Text file', found: true, resolved: true });
    // Before the lookup answers (or when it failed) the id shows, not as a link.
    assert.deepEqual(sourceDisplay({ sourceType: 'memory', sourceId: 'memory_1' }, null), { title: 'memory_1', kind: 'Memory', found: false, resolved: false });
    // A memory id and a file id that happen to match are told apart.
    assert.equal(sourceDisplay({ sourceType: 'file', sourceId: 'memory_1' }, names).found, false);
  });
});
