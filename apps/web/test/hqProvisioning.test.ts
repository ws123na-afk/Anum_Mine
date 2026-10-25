// Provisioning client rules (src/hq-provisioning/client.ts): reads create nothing, only a click confirm sends the hash shown.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { ProvisioningGap, ProvisioningGaps, ProvisioningProposal } from '@anum/contracts';
import { ProvisioningApiError, canConfirm, createProvisioningClient, orderedGaps } from '../src/hq-provisioning/client.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const scope = { tenantId: 't', workspaceId: 'w' };
const HASH = 'a'.repeat(64);
function proposal(patch: Partial<ProvisioningProposal> = {}): ProvisioningProposal {
  return { version: 1, payload_hash: HASH, can_confirm: true, content: { template_id: 'orders_desk', lang: 'ar' } as never, ...patch } as ProvisioningProposal;
}
const gaps = (patch: Partial<ProvisioningGaps> = {}): ProvisioningGaps => ({
  version: 1, workspace_id: 'w', as_of: 'x', sources: {}, gaps: [], coverage: [], limits: { agents: 1, max_agents: 200 }, can_confirm: true, templates: [], ...patch });

describe('provisioning client', () => {
  test('reads use GET and never send a body', async () => {
    const calls: { path: string; init: RequestInit }[] = [];
    const client = createProvisioningClient(async <T>(path: string, init: RequestInit) => { calls.push({ path, init }); return (path.includes('/gaps') ? gaps() : proposal()) as T; }, () => scope);
    await client.gaps();
    await client.proposal('orders_desk', 'en', 'room-1');
    assert.deepEqual(calls.map((c) => c.init.method), ['GET', 'GET']);
    assert.equal(calls[1].path, '/api/v1/office/provisioning/proposals/orders_desk?lang=en&room_id=room-1');
    assert.ok(calls.every((c) => c.init.body === undefined));
  });

  test('confirm sends the shown hash, the proposal language and input mode click only', async () => {
    let sent: { path: string; body: Record<string, unknown> } | null = null;
    const client = createProvisioningClient(async <T>(path: string, init: RequestInit) => { sent = { path, body: JSON.parse(String(init.body)) }; return {} as T; }, () => scope);
    await client.confirm(proposal(), 'r2');
    assert.equal(sent!.path, '/api/v1/office/provisioning/proposals/orders_desk/confirm');
    assert.deepEqual(sent!.body, { payload_hash: HASH, input_mode: 'click', lang: 'ar', room_id: 'r2' });
  });

  test('refuses another workspace, a wrong version and unsafe ids', async () => {
    const other = createProvisioningClient(async <T>() => gaps({ workspace_id: 'other' }) as T, () => scope);
    await assert.rejects(other.gaps(), ProvisioningApiError);
    const wrong = createProvisioningClient(async <T>() => ({ ...gaps(), version: 2 }) as T, () => scope);
    await assert.rejects(wrong.gaps(), ProvisioningApiError);
    const bad = createProvisioningClient(async <T>() => proposal({ payload_hash: 'short' }) as T, () => scope);
    await assert.rejects(bad.proposal('orders_desk', 'en'), ProvisioningApiError);
    const ok = createProvisioningClient(async <T>() => proposal() as T, () => scope);
    await assert.rejects(ok.proposal('Orders/../x' as never, 'en'), ProvisioningApiError);
    await assert.rejects(ok.proposal('orders_desk', 'en', '../x'), ProvisioningApiError);
  });

  test('a workspace switch during the read drops the answer', async () => {
    let current = scope;
    const client = createProvisioningClient(async <T>() => { current = { tenantId: 't', workspaceId: 'w2' }; return gaps() as T; }, () => current);
    await assert.rejects(client.gaps(), ProvisioningApiError);
  });
});

describe('ordering and confirm gate', () => {
  test('critical gaps come first, then alphabetical', () => {
    const g = (id: string, severity: 'critical' | 'warning') => ({ key: id, template_id: id, severity, reasons: [], flow_based: true }) as unknown as ProvisioningGap;
    assert.deepEqual(orderedGaps([g('b', 'warning'), g('c', 'critical'), g('a', 'warning')]).map((x) => x.template_id), ['c', 'a', 'b']);
  });
  test('only a person who may confirm, and not while busy, can confirm', () => {
    assert.equal(canConfirm(proposal(), false), true);
    assert.equal(canConfirm(proposal({ can_confirm: false }), false), false);
    assert.equal(canConfirm(proposal(), true), false);
    assert.equal(canConfirm(null, false), false);
  });
});

describe('messages', () => {
  test('every provisioning key exists in both languages without long dashes', () => {
    const keys = Object.keys(en).filter((k) => k.startsWith('hqProv.'));
    assert.ok(keys.length > 90);
    for (const key of keys) {
      assert.ok(key in ar, `${key} missing in Arabic`);
      for (const text of [en[key as keyof typeof en], ar[key as keyof typeof ar]]) assert.ok(!/[–—]/.test(String(text)), key);
    }
  });
});
