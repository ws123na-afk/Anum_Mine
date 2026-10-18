// Unit tests for skill signing helpers (src/lib/skillSigning.ts, docs/skills.md#signing).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { rollbackTargets, historyLabel } from '../src/lib/catalog.ts';
import {
  base64Bytes, filterKeys, flagged, installable, keyBody, keyCounts, revokeBody, rotateBody, signatureBody, verificationLabel, verificationTone,
  type PublisherKey, type SkillVerification,
} from '../src/lib/skillSigning.ts';

const at = '2026-10-18T12:00:00Z';
// Plainly fake, low-entropy values: 32 and 64 zero bytes in base64.
const KEY = 'A'.repeat(43) + '=';
const SIG = 'A'.repeat(86) + '==';
const check = (over: Partial<SkillVerification> = {}): SkillVerification => ({
  status: 'verified', installable: true, digest: 'ab'.repeat(32), key_id: 'acme-2026', signer: 'Acme', key_source: 'tenant',
  fingerprint: 'abcd:ef01', signed_at: at, detail: 'Signed by Acme with key acme-2026.', ...over,
});
const key = (id: string, over: Partial<PublisherKey> = {}): PublisherKey => ({
  id, tenant_id: 't', publisher: 'Acme', public_key: KEY, fingerprint: `fp-${id}`, source: 'tenant', status: 'active',
  created_by: 'admin', created_at: at, rotated_at: null, replaced_by: null, revoked_at: null, revoked_by: null, revoke_reason: null, ...over,
});

describe('verification', () => {
  test('only a verified version is installable; a missing check defers to the API', () => {
    assert.equal(installable({ verification: check() }), true);
    assert.equal(installable({ verification: check({ status: 'unsigned', installable: false }) }), false);
    assert.equal(installable({}), true);
    assert.equal(flagged({ verification: check({ status: 'revoked', installable: false }) }), true);
    assert.equal(flagged({ verification: check() }), false);
    assert.equal(flagged(null), false);
  });
  test('badge text and tone say who signed and what is wrong', () => {
    assert.equal(verificationLabel(check()), 'Signed by Acme');
    assert.equal(verificationLabel(check({ key_source: 'first_party', signer: 'ANUM' })), 'First-party');
    assert.equal(verificationLabel(check({ status: 'unsigned', installable: false })), 'Unsigned');
    assert.equal(verificationLabel(check({ status: 'revoked', installable: false })), 'Key revoked');
    assert.equal(verificationLabel(check({ status: 'invalid', installable: false })), 'Signature invalid');
    assert.equal(verificationLabel(check({ status: 'unknown_key', installable: false })), 'Unknown key');
    assert.equal(verificationTone('verified'), 'success');
    assert.equal(verificationTone('unsigned'), 'warning');
    assert.equal(verificationTone('revoked'), 'dangerTone');
  });
  test('rollback targets are older signed versions only', () => {
    const versions = [
      { version: '3.0.0', verification: check() },
      { version: '2.0.0', verification: check({ status: 'unsigned', installable: false }) },
      { version: '1.0.0', verification: check() },
    ];
    assert.deepEqual(rollbackTargets(versions, '3.0.0'), ['1.0.0']);
  });
  test('signing history reads in words', () => {
    const entry = (action: string, metadata: Record<string, unknown>) => ({ id: 'a', action, actor: 'x', outcome: 'denied', created_at: at, metadata });
    assert.equal(historyLabel(entry('skill.install_refused', { version: '1.0.0' })), 'Install of 1.0.0 refused: not signed by a trusted key');
    assert.equal(historyLabel(entry('skill.rollback_refused', { to_version: '0.9.0' })), 'Rollback to 0.9.0 refused: not signed by a trusted key');
    assert.equal(historyLabel(entry('skill.signed', { version: '1.0.0' })), 'Signed 1.0.0');
  });
});

describe('key input mirrors the API', () => {
  test('base64 byte length', () => {
    assert.equal(base64Bytes(KEY), 32);
    assert.equal(base64Bytes(SIG), 64);
    assert.equal(base64Bytes('not base64!'), null);
    assert.equal(base64Bytes(''), null);
  });
  test('add, rotate, revoke and sign bodies', () => {
    assert.deepEqual(keyBody({ id: ' acme-2026 ', publisher: ' Acme ', publicKey: KEY }), { ok: true, value: { id: 'acme-2026', publisher: 'Acme', public_key: KEY } });
    assert.equal(keyBody({ id: 'Bad Id', publisher: 'Acme', publicKey: KEY }).ok, false);
    assert.equal(keyBody({ id: 'acme-2026', publisher: '', publicKey: KEY }).ok, false);
    assert.equal(keyBody({ id: 'acme-2026', publisher: 'Acme', publicKey: 'AAAA' }).ok, false);
    const taken = keyBody({ id: 'acme-2026', publisher: 'Acme', publicKey: KEY }, ['acme-2026']);
    assert.equal(taken.ok, false);
    assert.equal(!taken.ok && taken.error, 'Key id acme-2026 is already used. Choose another id.');
    assert.deepEqual(rotateBody('acme-2027', KEY), { ok: true, value: { new_id: 'acme-2027', public_key: KEY } });
    assert.equal(rotateBody('acme-2026', KEY, ['acme-2026']).ok, false);
    assert.equal(revokeBody('  ').ok, false);
    assert.equal(revokeBody('x'.repeat(501)).ok, false);
    assert.deepEqual(revokeBody(' Leaked '), { ok: true, value: { reason: 'Leaked' } });
    assert.deepEqual(signatureBody('acme-2026', SIG), { ok: true, value: { key_id: 'acme-2026', signature: SIG } });
    assert.equal(signatureBody('acme-2026', KEY).ok, false);
  });
});

describe('key list', () => {
  const keys = [
    key('old', { status: 'revoked', created_at: '2026-01-01T00:00:00Z' }),
    key('mid', { status: 'rotated', created_at: '2026-05-01T00:00:00Z' }),
    key('new', { publisher: 'Beta Labs', created_at: '2026-09-01T00:00:00Z' }),
    key('newer', { created_at: '2026-10-01T00:00:00Z' }),
  ];
  test('active first, newest first, then rotated, then revoked', () => {
    assert.deepEqual(filterKeys(keys, '', 'all').map((k) => k.id), ['newer', 'new', 'mid', 'old']);
  });
  test('search id, publisher and fingerprint; filter by status', () => {
    assert.deepEqual(filterKeys(keys, 'beta', 'all').map((k) => k.id), ['new']);
    assert.deepEqual(filterKeys(keys, 'fp-mid', 'all').map((k) => k.id), ['mid']);
    assert.deepEqual(filterKeys(keys, '', 'revoked').map((k) => k.id), ['old']);
    assert.deepEqual(keyCounts(keys), { active: 2, rotated: 1, revoked: 1 });
  });
});
