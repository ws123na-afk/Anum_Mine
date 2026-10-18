// Skill signing (docs/skills.md#signing): verification badges, publisher key validation that
// mirrors the API, and key list search, filter and paging. The API verifies; these helpers only
// describe its answer and stop obviously bad input before a round trip.
import type { Result } from './catalog.ts';
import { t } from './i18n.ts';

export type VerificationStatus = 'verified' | 'unsigned' | 'invalid' | 'revoked' | 'unknown_key';
export type KeySource = 'tenant' | 'platform' | 'first_party';

export interface SkillVerification {
  status: VerificationStatus; installable: boolean; digest: string; key_id: string | null; signer: string | null;
  key_source: KeySource | null; fingerprint: string | null; signed_at: string | null; detail: string;
}

export type KeyStatus = 'active' | 'rotated' | 'revoked';
export interface PublisherKey {
  id: string; tenant_id: string | null; publisher: string; public_key: string; fingerprint: string; source: KeySource; status: KeyStatus;
  created_by: string; created_at: string; rotated_at: string | null; replaced_by: string | null;
  revoked_at: string | null; revoked_by: string | null; revoke_reason: string | null;
}
export interface PublisherKeyList { keys: PublisherKey[]; platform_keys: PublisherKey[]; can_manage: boolean }
export interface PublisherKeyRevoked { key: PublisherKey; affected_versions: number; flagged_installations: number }

const KEY_ID = /^[a-z0-9][a-z0-9_.-]{2,79}$/;

/** Whether the API would install this version; an older API without verification decides itself. */
export function installable(v: { verification?: SkillVerification | null } | null | undefined): boolean {
  return !v?.verification || v.verification.installable;
}

/** An installation whose installed version no longer verifies (key revoked, content changed). */
export function flagged(install: { verification?: SkillVerification | null } | null | undefined): boolean {
  return Boolean(install?.verification && !install.verification.installable);
}

export function verificationTone(status: VerificationStatus): 'success' | 'warning' | 'dangerTone' {
  if (status === 'verified') return 'success';
  if (status === 'unsigned') return 'warning';
  return 'dangerTone';
}

/** The short badge text: "Signed by Acme", "First-party", "Unsigned", "Key revoked", "Signature invalid". */
export function verificationLabel(v: SkillVerification): string {
  if (v.status === 'verified') {
    if (v.key_source === 'first_party') return t('signing.badge.firstParty');
    return t('signing.badge.signedBy', { signer: v.signer ?? v.key_id ?? '-' });
  }
  return t(`signing.badge.${v.status}`);
}

/** Raw bytes of standard base64, or null when it is not base64. */
export function base64Bytes(value: string): number | null {
  const s = value.trim();
  if (!s || s.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(s)) return null;
  return (s.length / 4) * 3 - (s.endsWith('==') ? 2 : s.endsWith('=') ? 1 : 0);
}

export interface KeyDraft { id: string; publisher: string; publicKey: string }
export const emptyKeyDraft = (): KeyDraft => ({ id: '', publisher: '', publicKey: '' });

function publicKeyError(value: string): string | null {
  return base64Bytes(value) === 32 ? null : t('signing.err.publicKey');
}

/** The `POST /skills/publisher-keys` body, validated as the API does. */
export function keyBody(d: KeyDraft, taken: string[] = []): Result<{ id: string; publisher: string; public_key: string }> {
  const id = d.id.trim(), publisher = d.publisher.trim(), publicKey = d.publicKey.trim();
  if (!KEY_ID.test(id)) return { ok: false, error: t('signing.err.keyId') };
  if (taken.includes(id)) return { ok: false, error: t('signing.err.keyIdTaken', { id }) };
  if (!publisher || publisher.length > 160) return { ok: false, error: t('signing.err.publisher') };
  const keyError = publicKeyError(publicKey);
  if (keyError) return { ok: false, error: keyError };
  return { ok: true, value: { id, publisher, public_key: publicKey } };
}

/** The `POST /skills/publisher-keys/{id}/rotate` body. */
export function rotateBody(newId: string, publicKey: string, taken: string[] = []): Result<{ new_id: string; public_key: string }> {
  const id = newId.trim();
  if (!KEY_ID.test(id)) return { ok: false, error: t('signing.err.keyId') };
  if (taken.includes(id)) return { ok: false, error: t('signing.err.keyIdTaken', { id }) };
  const keyError = publicKeyError(publicKey);
  if (keyError) return { ok: false, error: keyError };
  return { ok: true, value: { new_id: id, public_key: publicKey.trim() } };
}

/** The `POST /skills/publisher-keys/{id}/revoke` body. */
export function revokeBody(reason: string): Result<{ reason: string }> {
  const r = reason.trim();
  if (!r || r.length > 500) return { ok: false, error: t('signing.err.reason') };
  return { ok: true, value: { reason: r } };
}

/** The `PUT /skills/versions/{id}/{version}/signature` body: a key id and a 64-byte signature. */
export function signatureBody(keyId: string, signature: string): Result<{ key_id: string; signature: string }> {
  const id = keyId.trim(), sig = signature.trim();
  if (!KEY_ID.test(id)) return { ok: false, error: t('signing.err.keyId') };
  if (base64Bytes(sig) !== 64) return { ok: false, error: t('signing.err.signature') };
  return { ok: true, value: { key_id: id, signature: sig } };
}

export type KeyFilter = 'all' | KeyStatus;

/** Search id, publisher and fingerprint; active keys first, then rotated, then revoked, newest first. */
export function filterKeys(keys: PublisherKey[], query: string, filter: KeyFilter): PublisherKey[] {
  const q = query.trim().toLowerCase();
  const order: Record<KeyStatus, number> = { active: 0, rotated: 1, revoked: 2 };
  return keys
    .filter((k) => filter === 'all' || k.status === filter)
    .filter((k) => !q || [k.id, k.publisher, k.fingerprint].some((x) => x.toLowerCase().includes(q)))
    .sort((a, b) => order[a.status] - order[b.status] || b.created_at.localeCompare(a.created_at) || a.id.localeCompare(b.id));
}

export function keyCounts(keys: PublisherKey[]): Record<KeyStatus, number> {
  return {
    active: keys.filter((k) => k.status === 'active').length,
    rotated: keys.filter((k) => k.status === 'rotated').length,
    revoked: keys.filter((k) => k.status === 'revoked').length,
  };
}
