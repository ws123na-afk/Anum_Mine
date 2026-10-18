// Unit tests for the mail domains helpers (src/lib/mailDomains.ts, docs/email.md#mail-domains).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  cleanAlias, cleanDomain, dnsTone, domainMatches, parseGoto, pendingFor, recordLine, worstStatus,
  type DnsCheck, type DnsReport, type MailDomain, type PendingDomainChange,
} from '../src/lib/mailDomains.ts';

const check = (status: DnsCheck['status']): DnsCheck => ({ status, found: [], message: '' });
const report = (mx: DnsCheck['status'], spf: DnsCheck['status'], dkim: DnsCheck['status'], dmarc: DnsCheck['status']): DnsReport => ({
  domain: 'example.com', checked_at: '2026-10-19T10:00:00Z', mail_host: 'mail.example.com', mx: check(mx), spf: check(spf), dkim: check(dkim), dmarc: check(dmarc), records: [],
});
const domain: MailDomain = {
  domain: 'example.com', in_mailcow: true, active: true, allowed: true, mailboxes: 2, max_mailboxes: 10, quota_used_percent: 4,
  aliases: [{ id: '7', address: 'sales@example.com', goto: ['owner@client.example'], active: true }],
};

describe('mail domains helpers', () => {
  test('tones follow the shared status badges', () => {
    assert.equal(dnsTone('ok'), 'success');
    assert.equal(dnsTone('warning'), 'warning');
    assert.equal(dnsTone('danger'), 'dangerTone');
    assert.equal(dnsTone('missing'), 'dangerTone');
    assert.equal(dnsTone('error'), 'neutral');
  });
  test('the worst status wins: dangerous before missing before a failed lookup', () => {
    assert.equal(worstStatus(report('ok', 'danger', 'missing', 'warning')), 'danger');
    assert.equal(worstStatus(report('ok', 'ok', 'error', 'warning')), 'error');
    assert.equal(worstStatus(report('ok', 'ok', 'ok', 'ok')), 'ok');
  });
  test('domains and aliases are cleaned or refused', () => {
    assert.equal(cleanDomain(' Example.COM. '), 'example.com');
    assert.equal(cleanDomain('not a domain'), null);
    assert.equal(cleanDomain('-bad.com'), null);
    assert.equal(cleanAlias(' Sales ', 'example.com'), 'sales@example.com');
    assert.equal(cleanAlias('bad name', 'example.com'), null);
    assert.equal(cleanAlias('.dot', 'example.com'), null);
  });
  test('destinations split on commas, spaces and lines, without repeats', () => {
    assert.deepEqual(parseGoto('a@x.com, B@x.com\nb@x.com;c@y.org'), ['a@x.com', 'b@x.com', 'c@y.org']);
    assert.deepEqual(parseGoto('  '), []);
    assert.equal(parseGoto('a@x.com, nope'), null);
  });
  test('search matches the domain, an alias or a destination', () => {
    assert.equal(domainMatches(domain, 'EXAMPLE'), true);
    assert.equal(domainMatches(domain, 'sales@'), true);
    assert.equal(domainMatches(domain, 'client.example'), true);
    assert.equal(domainMatches(domain, 'other'), false);
    assert.equal(domainMatches(domain, ' '), true);
  });
  test('a waiting change is found for its domain or alias only', () => {
    const base = { approval: { id: 'a1' } } as unknown as PendingDomainChange;
    const pending: PendingDomainChange[] = [
      { ...base, operation: 'add_domain', domain: 'new.com', address: null, goto: [] },
      { ...base, operation: 'remove_alias', domain: 'example.com', address: 'sales@example.com', goto: [] },
    ];
    assert.equal(pendingFor(pending, 'new.com')?.operation, 'add_domain');
    assert.equal(pendingFor(pending, 'example.com'), undefined);
    assert.equal(pendingFor(pending, 'example.com', 'sales@example.com')?.operation, 'remove_alias');
  });
  test('a record becomes one zone line, TXT values quoted', () => {
    assert.equal(recordLine({ kind: 'mx', type: 'MX', name: 'example.com', value: '10 mail.example.com.', note: null }), 'example.com. MX 10 mail.example.com.');
    assert.equal(recordLine({ kind: 'spf', type: 'TXT', name: 'example.com', value: 'v=spf1 mx ~all', note: null }), 'example.com. TXT "v=spf1 mx ~all"');
  });
});
