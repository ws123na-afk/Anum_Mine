// Unit tests for the Memory, Skills and Agents helpers in src/lib/capabilities.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { agentRows, indexSummary, installReview, memoryIndexState, memorySearchPath, skillAnchor, skillInstallState, type RetrievalIndexStatus } from '../src/lib/capabilities.ts';

const at = '2026-10-06T12:00:00Z';
const skill = (over: Partial<{ skill_id: string; version: string; name: string; required_tools: string[]; risk_level: string }> = {}) => ({ skill_id: 'research', version: '1.0.0', name: 'Research', description: 'Finds things.', required_tools: ['web.search', 'files.read'], risk_level: 'low', ...over });
const install = (over: Partial<{ id: string; skill_id: string; version: string; enabled: boolean; approved_tools: string[] }> = {}) => ({ id: 'inst_1', skill_id: 'research', version: '1.0.0', approved_tools: ['web.search'], enabled: true, installed_at: at, ...over });

describe('memorySearchPath', () => {
  test('omits the query when blank', () => {
    assert.equal(memorySearchPath('   '), '/api/v1/memories');
  });
  test('encodes the trimmed query and task', () => {
    assert.equal(memorySearchPath(' launch plan&x ', 'task_1'), '/api/v1/memories?query=launch+plan%26x&task_id=task_1');
  });
});

describe('memoryIndexState and indexSummary', () => {
  const status: RetrievalIndexStatus = { embedding_model: 'anum-local-hash-v1', indexed: 1, failed: 1, skipped: 0, stale: 0, chunks: 3, sources: [
    { source_type: 'memory', source_id: 'm1', name: null, status: 'indexed', error: null, chunk_count: 3, embedding_model: 'anum-local-hash-v1', indexed_at: at },
    { source_type: 'memory', source_id: 'm2', name: null, status: 'failed', error: 'embedder down', chunk_count: 0, embedding_model: null, indexed_at: at },
    { source_type: 'file', source_id: 'm3', name: 'a.txt', status: 'indexed', error: null, chunk_count: 1, embedding_model: null, indexed_at: at },
  ] };
  test('reads the memory source status', () => {
    assert.deepEqual(memoryIndexState(status, 'm1'), { state: 'indexed', error: null });
    assert.deepEqual(memoryIndexState(status, 'm2'), { state: 'failed', error: 'embedder down' });
  });
  test('ignores file sources and missing status', () => {
    assert.equal(memoryIndexState(status, 'm3').state, 'not_indexed');
    assert.equal(memoryIndexState(null, 'm1').state, 'not_indexed');
  });
  test('summarises counts', () => {
    assert.equal(indexSummary(status), '1 indexed · 3 chunks · 1 failed (anum-local-hash-v1)');
    assert.equal(indexSummary(null), 'Retrieval index status unavailable.');
  });
});

describe('installReview', () => {
  test('lists every required tool once, sorted', () => {
    const r = installReview(skill({ required_tools: ['web.search', 'files.read', 'web.search'] }));
    assert.deepEqual(r.tools, ['files.read', 'web.search']);
    assert.equal(r.summary, 'Installing grants 2 tools to agents in this workspace.');
    assert.equal(r.highRisk, false);
  });
  test('flags high risk and no tools', () => {
    const r = installReview(skill({ required_tools: [], risk_level: 'High' }));
    assert.equal(r.highRisk, true);
    assert.equal(r.summary, 'This skill requests no tools.');
  });
});

describe('skillInstallState', () => {
  test('distinguishes available, installed, disabled and other version', () => {
    assert.equal(skillInstallState(skill(), []), 'available');
    assert.equal(skillInstallState(skill(), [install()]), 'installed');
    assert.equal(skillInstallState(skill(), [install({ enabled: false })]), 'disabled');
    assert.equal(skillInstallState(skill({ version: '2.0.0' }), [install()]), 'other_version');
  });
});

describe('agentRows', () => {
  test('joins installs to versions and sorts enabled first', () => {
    const rows = agentRows(
      [install({ id: 'a', skill_id: 'zeta', enabled: true }), install({ id: 'b', skill_id: 'alpha', enabled: false }), install({ id: 'c', skill_id: 'research' })],
      [skill(), skill({ skill_id: 'alpha', name: 'Alpha' })],
    );
    assert.deepEqual(rows.map((r) => [r.installId, r.name, r.enabled]), [['c', 'Research', true], ['a', 'zeta', true], ['b', 'Alpha', false]]);
    assert.equal(rows[0].detailsAnchor, 'skill-research');
  });
  test('anchors are safe ids', () => {
    assert.equal(skillAnchor('acme/skill v1'), 'skill-acme-skill-v1');
  });
});
