// Unit tests for the approval policy helpers (src/lib/policy.ts) and the workspace selection
// (src/lib/workspaces.ts). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { WorkspaceMember } from '@anum/contracts';
import { approvalPolicyBody, mapApprovalPolicy, policyChanged, policyDraft, policyUpdatedLabel, POLICY_SWITCHES, twoPersonWarning } from '../src/lib/policy.ts';
import { EMPTY_MEMORY, forget, isWorkspaceId, memoryKey, readMemory, remember, resolveWorkspace, select, workspaceOptions, writeMemory, type KeyValueStorage } from '../src/lib/workspaces.ts';

const member = (userId: string, role: WorkspaceMember['role'], active = true): WorkspaceMember => ({
  tenantId: 't', workspaceId: 'w', userId, role, active, createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
});

function memoryStorage(initial: Record<string, string> = {}): KeyValueStorage & { data: Record<string, string> } {
  const data = { ...initial };
  return { data, getItem: (key) => data[key] ?? null, setItem: (key, value) => { data[key] = value; } };
}

describe('approval policy', () => {
  test('maps the wire shape and sends both switches', () => {
    const policy = mapApprovalPolicy({ two_person_rule: true, medium_risk_requires_approval: false, updated_by: 'user_owner', updated_at: '2026-10-06T10:00:00Z' });
    assert.deepEqual(policy, { twoPersonRule: true, mediumRiskRequiresApproval: false, updatedBy: 'user_owner', updatedAt: '2026-10-06T10:00:00Z' });
    assert.deepEqual(mapApprovalPolicy({ two_person_rule: false, medium_risk_requires_approval: false }), { twoPersonRule: false, mediumRiskRequiresApproval: false, updatedBy: null, updatedAt: null });
    assert.deepEqual(approvalPolicyBody({ twoPersonRule: false, mediumRiskRequiresApproval: true }), { two_person_rule: false, medium_risk_requires_approval: true });
    assert.equal(policyChanged(policy, policyDraft(policy)), false);
    assert.equal(policyChanged(policy, { ...policyDraft(policy), mediumRiskRequiresApproval: true }), true);
  });

  test('explains every switch and the last change', () => {
    assert.deepEqual(POLICY_SWITCHES.map((x) => x.key), ['twoPersonRule', 'mediumRiskRequiresApproval']);
    for (const item of POLICY_SWITCHES) assert.ok(item.on && item.off && item.title);
    assert.equal(policyUpdatedLabel({ twoPersonRule: false, mediumRiskRequiresApproval: false }, (x) => x), 'Never changed: both switches are off by default.');
    assert.equal(policyUpdatedLabel({ twoPersonRule: true, mediumRiskRequiresApproval: false, updatedAt: 'T', updatedBy: 'ana' }, (x) => `at ${x}`), 'Last changed at T by ana.');
  });

  test('warns before the two-person rule leaves one owner unable to approve', () => {
    const on = { twoPersonRule: true, mediumRiskRequiresApproval: false };
    assert.match(twoPersonWarning(on, [member('a', 'owner'), member('b', 'member')]) ?? '', /one active owner/);
    assert.match(twoPersonWarning(on, [member('a', 'owner'), member('b', 'owner', false)]) ?? '', /one active owner/);
    assert.equal(twoPersonWarning(on, [member('a', 'owner'), member('b', 'owner')]), null);
    assert.equal(twoPersonWarning(on, null), null);
    assert.equal(twoPersonWarning({ ...on, twoPersonRule: false }, [member('a', 'owner')]), null);
  });
});

describe('workspace selection', () => {
  test('ids follow the API pattern', () => {
    assert.equal(isWorkspaceId('workspace_sales'), true);
    assert.equal(isWorkspaceId('ab'), false);
    assert.equal(isWorkspaceId('has space'), false);
    assert.equal(isWorkspaceId('x'.repeat(81)), false);
  });

  test('the selection wins, then the token claim, then configuration, then the fallback', () => {
    assert.equal(resolveWorkspace(select(EMPTY_MEMORY, 'workspace_sales'), 'workspace_claim', 'workspace_conf', 'workspace_foundation'), 'workspace_sales');
    assert.equal(resolveWorkspace(EMPTY_MEMORY, 'workspace_claim', 'workspace_conf', 'workspace_foundation'), 'workspace_claim');
    assert.equal(resolveWorkspace(EMPTY_MEMORY, null, 'workspace_conf', 'workspace_foundation'), 'workspace_conf');
    assert.equal(resolveWorkspace(EMPTY_MEMORY, 'bad id!', undefined, 'workspace_foundation'), 'workspace_foundation');
  });

  test('remembers per tenant and user, ignores malformed or blocked storage', () => {
    const storage = memoryStorage();
    const key = memoryKey('tenant_local', 'user-dev');
    assert.equal(key, 'anum.workspaces.tenant_local.user-dev');
    writeMemory(storage, key, select(remember(EMPTY_MEMORY, 'workspace_sales'), 'workspace_ops'));
    assert.deepEqual(readMemory(storage, key), { selected: 'workspace_ops', known: ['workspace_ops', 'workspace_sales'] });
    assert.deepEqual(readMemory(storage, memoryKey('tenant_local', 'someone-else')), EMPTY_MEMORY);
    assert.deepEqual(readMemory(memoryStorage({ [key]: '{not json' }), key), EMPTY_MEMORY);
    assert.deepEqual(readMemory(memoryStorage({ [key]: JSON.stringify({ selected: 'no way!', known: ['ok_id', 7, 'bad id'] }) }), key), { selected: null, known: ['ok_id'] });
    const blocked: KeyValueStorage = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
    assert.deepEqual(readMemory(blocked, key), EMPTY_MEMORY);
    assert.doesNotThrow(() => writeMemory(blocked, key, EMPTY_MEMORY));
    assert.deepEqual(readMemory(null, key), EMPTY_MEMORY);
  });

  test('remember deduplicates and caps; forget keeps the selected workspace', () => {
    let memory = EMPTY_MEMORY;
    for (let i = 0; i < 25; i += 1) memory = remember(memory, `workspace_${i}`);
    memory = remember(memory, 'workspace_3');
    assert.equal(memory.known.length, 20);
    assert.equal(memory.known[0], 'workspace_3');
    assert.equal(remember(memory, 'bad id'), memory);
    const selected = select(memory, 'workspace_3');
    assert.equal(forget(selected, 'workspace_3'), selected);
    assert.equal(forget(selected, 'workspace_4').known.includes('workspace_4'), false);
  });

  test('switcher options: current first, defaults, then remembered, no duplicates', () => {
    const memory = select(remember(EMPTY_MEMORY, 'workspace_foundation'), 'workspace_sales');
    assert.deepEqual(workspaceOptions('workspace_sales', ['workspace_foundation', undefined], memory), [
      { id: 'workspace_sales', current: true, source: 'remembered' },
      { id: 'workspace_foundation', current: false, source: 'default' },
    ]);
    assert.deepEqual(workspaceOptions('workspace_foundation', ['workspace_foundation'], EMPTY_MEMORY), [{ id: 'workspace_foundation', current: true, source: 'default' }]);
  });
});
