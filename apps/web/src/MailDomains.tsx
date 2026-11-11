// Mail domains, aliases and mailboxes on the Mailcow server (docs/email.md#mail-domains), shown to the
// platform operator in the platform console: each domain's Mailcow state, its aliases and mailboxes, a live DNS check
// of MX, SPF, DKIM and DMARC with the exact records to set. Every change is only asked here and
// runs after an approval bound to its payload hash; changes stay inside ANUM_MAILSERVER_ALLOWED_DOMAINS.
// A new mailbox's first password is sent once with the request and cleared from the form at once.
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { AtSign, Copy, Globe, Inbox, Plus, RefreshCw, ShieldCheck, Trash2, Undo2 } from 'lucide-react';
import * as api from './lib/api';
import { mapApproval } from './lib/api';
import { expiryLabel, shortHash } from './lib/approvals';
import { describeError, isPermissionDenied } from './lib/errors';
import { CATALOGS, formatDateTime, t, type MessageKey } from './lib/i18n';
import {
  DNS_KINDS, QUOTA_DEFAULT_MB, cleanAlias, dnsCodeKey, dnsTone, domainMatches, mailboxPasswordProblem, parseGoto, parseQuota, pendingFor, recordLine,
  type DnsReport, type DomainChangeBody, type MailDomain, type MailDomainsView, type PendingDomainChange,
} from './lib/mailDomains';
import { askDomainChange, getDomainDns, getMailDomains, withdrawDomainChange } from './lib/mailDomainsApi';
import { PaneProblem } from './PageParts';

/** A value isolated from the surrounding text direction (domains and addresses inside Arabic). */
const iso = (value: string | number): string => `⁨${value}⁩`;
const PAGE = 20;
const ALIAS_PAGE = 10;
const AUTO_DNS = 5;

type Load = { state: 'loading' } | { state: 'ready'; value: MailDomainsView } | { state: 'error' | 'denied'; message: string };
type Dns = { state: 'loading' } | { state: 'ready'; value: DnsReport } | { state: 'error'; message: string };
type Note = { text: string; error?: boolean } | null;

/** A DNS judgement or record note in the reader's language; host and record names are kept as they are. */
function codeText(code: string | null | undefined, params: Record<string, string>, fallback: string): string {
  const key = dnsCodeKey(code, (k) => k in CATALOGS.en);
  if (!key) return fallback;
  return t(key as MessageKey, Object.fromEntries(Object.entries(params).map(([name, value]) => [name, iso(value)])));
}

function changeSummary(change: Pick<PendingDomainChange, 'operation' | 'domain' | 'address' | 'goto' | 'name'>): string {
  return t(`maildom.op.${change.operation}` as MessageKey, { domain: iso(change.domain), address: iso(change.address ?? ''), goto: iso(change.goto.join(', ')), name: iso(change.name ?? '') });
}

export function MailDomains() {
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [dns, setDns] = useState<Record<string, Dns>>({});
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(PAGE);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Note>(null);
  const autoChecked = useRef(false);

  const check = useCallback(async (domain: string) => {
    setDns((current) => ({ ...current, [domain]: { state: 'loading' } }));
    try {
      const value = await getDomainDns(domain);
      setDns((current) => ({ ...current, [domain]: { state: 'ready', value } }));
    } catch (error) {
      setDns((current) => ({ ...current, [domain]: { state: 'error', message: describeError(error) } }));
    }
  }, []);

  const reload = useCallback(async () => {
    try {
      const value = await getMailDomains();
      setLoad({ state: 'ready', value });
      if (!autoChecked.current) {
        autoChecked.current = true;
        value.domains.filter((d) => d.allowed && d.in_mailcow).slice(0, AUTO_DNS).forEach((d) => void check(d.domain));
      }
    } catch (error) {
      setLoad({ state: isPermissionDenied(error) ? 'denied' : 'error', message: describeError(error) });
    }
  }, [check]);
  useEffect(() => { void reload(); }, [reload]);

  const run = async (key: string, action: () => Promise<Note>) => {
    setBusy(key);
    setNote(null);
    try { setNote(await action()); } catch (error) { setNote({ text: t('maildom.actionFailed', { message: describeError(error) }), error: true }); }
    setBusy(null);
    await reload();
  };
  const ask = (key: string, body: DomainChangeBody) => run(key, async () => {
    await askDomainChange(body);
    const domain = body.domain ?? (body.address ?? '').split('@')[1] ?? '';
    return { text: t('maildom.asked', { summary: changeSummary({ operation: body.operation, domain, address: body.address ?? null, goto: body.goto ?? [], name: body.name ?? null }) }) };
  });
  const approve = (change: PendingDomainChange) => run(`approve:${change.approval.id}`, async () => {
    const decided = await api.approveTask(mapApproval(change.approval));
    const failed = decided.task.status === 'failed' || decided.run?.status === 'failed';
    return failed ? { text: t('maildom.notDone', { message: decided.run?.result ?? '' }), error: true } : { text: t('maildom.done', { summary: changeSummary(change) }) };
  });
  const withdraw = (change: PendingDomainChange) => run(`withdraw:${change.approval.id}`, async () => {
    await withdrawDomainChange(change.approval.id);
    return { text: t('maildom.withdrawn') };
  });

  const view = load.state === 'ready' ? load.value : null;
  const rows = view ? view.domains.filter((d) => domainMatches(d, query)) : [];
  return <section className="pane mailDomainsPane" aria-labelledby="mail-domains-title">
    <div className="paneHead">
      <h2 id="mail-domains-title"><Globe aria-hidden="true" />{t('maildom.title')}</h2>
      {view && view.domains.length > 0 && <div className="toolbar mailDomainsToolbar">
        <label className="visuallyHidden" htmlFor="mail-domains-search">{t('maildom.search')}</label>
        <input id="mail-domains-search" type="search" value={query} placeholder={t('maildom.search')} onChange={(e) => { setQuery(e.target.value); setShown(PAGE); }} />
      </div>}
    </div>
    <div className="paneBody">
      <p className="muted mailDomainsIntro">{t('maildom.intro')}</p>
      {load.state === 'loading' && <p className="muted" role="status">{t('maildom.loading')}</p>}
      {load.state === 'error' && <PaneProblem text={t('maildom.failed', { message: load.message })} onRetry={() => { setLoad({ state: 'loading' }); void reload(); }} />}
      {load.state === 'denied' && <p className="notice" role="alert">{t('maildom.denied', { message: load.message })}</p>}
      {note && <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}>{note.text}</p>}
      {view && view.mailcow_state === 'off' && <p className="notice">{t('maildom.mailcow.off')}</p>}
      {view && view.mailcow_state !== 'off' && view.mailcow_state !== 'ok' && <p className="notice errorNotice" role="alert">{t('maildom.mailcow.problem', { message: view.message ?? '' })}</p>}
      {view && !view.can_change && view.change_note && view.mailcow_state === 'ok' && <p className="notice">{t('maildom.changesOff', { message: view.change_note })}</p>}
      {view && view.mailcow_state === 'ok' && !view.mailboxes_read && <p className="notice">{t('maildom.mailboxesUnread')}</p>}
      {view && view.pending.length > 0 && <div className="mailDomainsPending" role="group" aria-label={t('maildom.pendingTitle')}>
        <h3>{t('maildom.pendingTitle')}</h3>
        <ul>{view.pending.map((change) => {
          const mapped = mapApproval(change.approval);
          const expiry = expiryLabel(mapped);
          return <li key={change.approval.id} className="mailDomainApproval">
            <strong>{changeSummary(change)}</strong>
            <p className="mailDomainHash"><span className="muted">{t('maildom.hash')}</span> <code dir="ltr" title={mapped.payloadHash ?? ''}>{shortHash(mapped.payloadHash)}</code>{expiry && <span className="muted"> · {expiry}</span>}</p>
            <div className="mailDomainsActions">
              <button type="button" disabled={busy !== null} onClick={() => void approve(change)}><ShieldCheck aria-hidden="true" />{busy === `approve:${change.approval.id}` ? t('maildom.approving') : t('maildom.approve')}</button>
              <button type="button" className="secondary" disabled={busy !== null} onClick={() => void withdraw(change)}><Undo2 aria-hidden="true" />{t('maildom.withdraw')}</button>
            </div>
          </li>;
        })}</ul>
      </div>}
      {view && !view.domains.length && <div className="paneEmpty"><Globe aria-hidden="true" /><h3>{t('maildom.empty')}</h3><p>{t('maildom.emptyText')}</p></div>}
      {view && view.domains.length > 0 && !rows.length && <div className="paneEmpty"><h3>{t('maildom.noMatch')}</h3></div>}
      {view && rows.length > 0 && <ul className="mailDomainList">
        {rows.slice(0, shown).map((domain) => <DomainCard key={domain.domain} domain={domain} view={view} dns={dns[domain.domain]} busy={busy}
          onCheck={() => void check(domain.domain)} onAsk={(key, body) => void ask(key, body)} />)}
      </ul>}
      {rows.length > shown && <button type="button" className="secondary" onClick={() => setShown(shown + PAGE)}>{t('maildom.showMore', { count: Math.min(PAGE, rows.length - shown) })}</button>}
      {view && view.can_change && <p className="muted">{t('maildom.addMoreHelp')}</p>}
    </div>
  </section>;
}

function DomainCard({ domain, view, dns, busy, onCheck, onAsk }: {
  domain: MailDomain; view: MailDomainsView; dns: Dns | undefined; busy: string | null; onCheck(): void; onAsk(key: string, body: DomainChangeBody): void;
}) {
  const [allAliases, setAllAliases] = useState(false);
  const waiting = pendingFor(view.pending, domain.domain);
  const canChange = view.can_change && domain.allowed;
  const removable = domain.in_mailcow && domain.mailboxes === 0 && domain.aliases.length === 0;
  const titleId = `domain-${domain.domain.replace(/[^a-z0-9]/g, '-')}`;
  const aliases = allAliases ? domain.aliases : domain.aliases.slice(0, ALIAS_PAGE);
  return <li className="mailDomainCard" aria-labelledby={titleId}>
    <div className="mailDomainHead">
      <h3 id={titleId} dir="ltr">{domain.domain}</h3>
      <div className="mailDomainBadges">
        <span className={`statusBadge ${domain.in_mailcow ? 'success' : 'neutral'}`}>{t(domain.in_mailcow ? 'maildom.badge.inMailcow' : 'maildom.badge.notInMailcow')}</span>
        {domain.in_mailcow && !domain.active && <span className="statusBadge warning">{t('maildom.badge.off')}</span>}
        {!domain.allowed && <span className="statusBadge neutral" title={t('maildom.notAllowedHelp')}>{t('maildom.badge.notAllowed')}</span>}
        {waiting && <span className="statusBadge warning">{t('maildom.waiting')}</span>}
      </div>
    </div>
    {domain.in_mailcow && <p className="muted">{t('maildom.mailboxes', { count: domain.mailboxes })}{domain.quota_used_percent !== null && <> · {t('maildom.quota', { percent: iso(`${domain.quota_used_percent}%`) })}</>}</p>}
    {!domain.allowed && <p className="muted">{t('maildom.notAllowedHelp')}</p>}
    <div className="mailDomainsActions">
      <button type="button" className="secondary" disabled={dns?.state === 'loading'} onClick={onCheck}><RefreshCw aria-hidden="true" />{dns ? t('maildom.checkAgain') : t('maildom.checkDns')}</button>
      {canChange && !domain.in_mailcow && !waiting && <button type="button" disabled={busy !== null} onClick={() => onAsk(`add:${domain.domain}`, { operation: 'add_domain', domain: domain.domain })}><Plus aria-hidden="true" />{t('maildom.add')}</button>}
      {canChange && domain.in_mailcow && !waiting && <button type="button" className="secondary danger" disabled={busy !== null || !removable} title={removable ? undefined : t('maildom.removeBlocked')}
        onClick={() => onAsk(`remove:${domain.domain}`, { operation: 'remove_domain', domain: domain.domain })}><Trash2 aria-hidden="true" />{t('maildom.remove')}</button>}
    </div>
    {dns && <DnsPanel dns={dns} />}
    {domain.in_mailcow && <div className="mailDomainAliases">
      <h4><AtSign aria-hidden="true" />{t('maildom.aliases')}</h4>
      {!domain.aliases.length && <p className="muted">{t('maildom.noAliases')}</p>}
      {domain.aliases.length > 0 && <ul>{aliases.map((alias) => {
        const aliasWaiting = pendingFor(view.pending, domain.domain, alias.address);
        return <li key={alias.id}>
          <span dir="auto">{t('maildom.aliasTo', { address: iso(alias.address), goto: iso(alias.goto.join(', ')) })}{!alias.active && <> ({t('maildom.aliasOff')})</>}</span>
          {aliasWaiting && <span className="statusBadge warning">{t('maildom.waiting')}</span>}
          {canChange && !aliasWaiting && <button type="button" className="secondary iconButton" disabled={busy !== null} aria-label={t('maildom.removeAlias', { address: alias.address })} title={t('maildom.removeAlias', { address: alias.address })}
            onClick={() => onAsk(`alias:${alias.id}`, { operation: 'remove_alias', address: alias.address, alias_id: alias.id })}><Trash2 aria-hidden="true" /></button>}
        </li>;
      })}</ul>}
      {domain.aliases.length > ALIAS_PAGE && !allAliases && <button type="button" className="secondary" onClick={() => setAllAliases(true)}>{t('maildom.showAllAliases', { count: domain.aliases.length })}</button>}
      {canChange && <AliasForm domain={domain.domain} busy={busy !== null} onAsk={onAsk} />}
    </div>}
    {domain.in_mailcow && view.mailboxes_read && <MailboxSection domain={domain} view={view} canChange={canChange} busy={busy} onAsk={onAsk} />}
  </li>;
}

function MailboxSection({ domain, view, canChange, busy, onAsk }: {
  domain: MailDomain; view: MailDomainsView; canChange: boolean; busy: string | null; onAsk(key: string, body: DomainChangeBody): void;
}) {
  const [all, setAll] = useState(false);
  const boxes = domain.mailbox_list ?? [];
  const shown = all ? boxes : boxes.slice(0, ALIAS_PAGE);
  const full = domain.max_mailboxes !== null && domain.mailboxes >= domain.max_mailboxes;
  return <div className="mailDomainAliases mailDomainMailboxes">
    <h4><Inbox aria-hidden="true" />{t('maildom.mailboxTitle')}</h4>
    {!boxes.length && <p className="muted">{t('maildom.noMailboxes')}</p>}
    {boxes.length > 0 && <ul>{shown.map((box) => {
      const waiting = pendingFor(view.pending, domain.domain, box.address);
      return <li key={box.address}>
        <span dir="auto"><strong>{iso(box.address)}</strong>{box.name && <> · {box.name}</>}{!box.active && <> ({t('maildom.aliasOff')})</>}</span>
        <span className="muted">{box.quota_mb === null ? t('maildom.quotaNone')
          : box.used_percent === null ? t('maildom.quotaOnly', { quota: iso(box.quota_mb) })
            : t('maildom.quotaUsed', { quota: iso(box.quota_mb), percent: iso(`${box.used_percent}%`) })}</span>
        {waiting && <span className="statusBadge warning">{t('maildom.waiting')}</span>}
      </li>;
    })}</ul>}
    {view.pending.filter((p) => p.domain === domain.domain && p.operation === 'add_mailbox').map((p) =>
      <p key={p.approval.id} className="muted">{t('maildom.mailboxWaiting', { address: iso(p.address ?? '') })}</p>)}
    {boxes.length > ALIAS_PAGE && !all && <button type="button" className="secondary" onClick={() => setAll(true)}>{t('maildom.showAllMailboxes', { count: boxes.length })}</button>}
    {canChange && full && <p className="muted">{t('maildom.mailboxFull', { count: domain.max_mailboxes ?? 0 })}</p>}
    {canChange && !full && <MailboxForm domain={domain.domain} busy={busy !== null} onAsk={onAsk} />}
  </div>;
}

type MailboxError = 'address' | 'name' | 'password' | 'quota';

function MailboxForm({ domain, busy, onAsk }: { domain: string; busy: boolean; onAsk(key: string, body: DomainChangeBody): void }) {
  const [local, setLocal] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [quota, setQuota] = useState(String(QUOTA_DEFAULT_MB));
  const [error, setError] = useState<{ field: MailboxError; text: string } | null>(null);
  const id = domain.replace(/[^a-z0-9]/g, '-');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const address = cleanAlias(local, domain);
    const owner = name.trim().replace(/\s+/g, ' ');
    const passwordProblem = mailboxPasswordProblem(password, local.trim().toLowerCase());
    const quotaMb = parseQuota(quota);
    if (!address) { setError({ field: 'address', text: t('maildom.badMailbox') }); return; }
    if (!owner || owner.length > 120) { setError({ field: 'name', text: t('maildom.badOwner') }); return; }
    if (passwordProblem) { setError({ field: 'password', text: t(`maildom.badPassword.${passwordProblem}` as MessageKey) }); return; }
    if (quotaMb === null) { setError({ field: 'quota', text: t('maildom.badQuota') }); return; }
    setError(null);
    onAsk(`mailbox:new:${domain}`, { operation: 'add_mailbox', address, name: owner, password, quota_mb: quotaMb });
    // The password leaves the page with the request and is not kept.
    setPassword('');
    setLocal('');
    setName('');
  };
  const invalid = (field: MailboxError) => error?.field === field || undefined;
  return <form className="mailAliasForm" onSubmit={submit} aria-label={t('maildom.addMailbox')} noValidate autoComplete="off">
    <h4>{t('maildom.addMailbox')}</h4>
    <label htmlFor={`mailbox-name-${id}`}>{t('maildom.mailboxAddress')}</label>
    <div className="mailAliasName" dir="ltr"><input id={`mailbox-name-${id}`} value={local} onChange={(e) => setLocal(e.target.value)} autoComplete="off" spellCheck={false} aria-invalid={invalid('address')} /><span>@{domain}</span></div>
    <label htmlFor={`mailbox-owner-${id}`}>{t('maildom.mailboxOwner')}</label>
    <input id={`mailbox-owner-${id}`} dir="auto" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} autoComplete="off" aria-invalid={invalid('name')} />
    <label htmlFor={`mailbox-password-${id}`}>{t('maildom.mailboxPassword')}</label>
    <input id={`mailbox-password-${id}`} type="password" dir="ltr" value={password} maxLength={128} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
      aria-describedby={`mailbox-password-help-${id}`} aria-invalid={invalid('password')} />
    <small id={`mailbox-password-help-${id}`} className="muted">{t('maildom.mailboxPasswordHelp')}</small>
    <label htmlFor={`mailbox-quota-${id}`}>{t('maildom.mailboxQuota')}</label>
    <input id={`mailbox-quota-${id}`} className="mailQuotaInput" dir="ltr" inputMode="numeric" value={quota} onChange={(e) => setQuota(e.target.value)} aria-describedby={`mailbox-quota-help-${id}`} aria-invalid={invalid('quota')} />
    <small id={`mailbox-quota-help-${id}`} className="muted">{t('maildom.mailboxQuotaHelp')}</small>
    {error && <p className="notice errorNotice" role="alert">{error.text}</p>}
    <div><button type="submit" disabled={busy}><Plus aria-hidden="true" />{t('maildom.askMailbox')}</button></div>
  </form>;
}

function AliasForm({ domain, busy, onAsk }: { domain: string; busy: boolean; onAsk(key: string, body: DomainChangeBody): void }) {
  const [local, setLocal] = useState('');
  const [goto, setGoto] = useState('');
  const [error, setError] = useState<string | null>(null);
  const id = domain.replace(/[^a-z0-9]/g, '-');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const address = cleanAlias(local, domain);
    const targets = parseGoto(goto);
    if (!address) { setError(t('maildom.badAlias')); return; }
    if (!targets || !targets.length || targets.length > 10) { setError(t('maildom.badGoto')); return; }
    setError(null);
    onAsk(`alias:new:${domain}`, { operation: 'add_alias', address, goto: targets });
    setLocal('');
    setGoto('');
  };
  return <form className="mailAliasForm" onSubmit={submit} aria-label={t('maildom.addAlias')} noValidate>
    <h4>{t('maildom.addAlias')}</h4>
    <label htmlFor={`alias-name-${id}`}>{t('maildom.aliasName')}</label>
    <div className="mailAliasName" dir="ltr"><input id={`alias-name-${id}`} value={local} onChange={(e) => setLocal(e.target.value)} autoComplete="off" spellCheck={false} aria-invalid={error === t('maildom.badAlias')} /><span>@{domain}</span></div>
    <label htmlFor={`alias-goto-${id}`}>{t('maildom.aliasGoto')}</label>
    <textarea id={`alias-goto-${id}`} dir="ltr" rows={2} value={goto} onChange={(e) => setGoto(e.target.value)} aria-describedby={`alias-goto-help-${id}`} spellCheck={false} />
    <small id={`alias-goto-help-${id}`} className="muted">{t('maildom.aliasGotoHelp')}</small>
    {error && <p className="notice errorNotice" role="alert">{error}</p>}
    <div><button type="submit" disabled={busy}><Plus aria-hidden="true" />{t('maildom.askAlias')}</button></div>
  </form>;
}

function DnsPanel({ dns }: { dns: Dns }) {
  const [copied, setCopied] = useState<string | null>(null);
  if (dns.state === 'loading') return <p className="muted" role="status">{t('maildom.checking')}</p>;
  if (dns.state === 'error') return <p className="notice errorNotice" role="alert">{t('maildom.dnsFailed', { message: dns.message })}</p>;
  const report = dns.value;
  const copy = async (kind: string, text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(kind); } catch { setCopied(`fail:${kind}`); }
  };
  return <div className="mailDnsPanel">
    <p className="muted">{t('maildom.checkedAt', { when: formatDateTime(report.checked_at) })}</p>
    <ul className="mailDnsChecks">{DNS_KINDS.map((kind) => {
      const item = report[kind];
      return <li key={kind}>
        <strong>{t(`maildom.dns.${kind}` as MessageKey)}</strong>
        <span className={`statusBadge ${dnsTone(item.status)}`}>{t(`maildom.status.${item.status}` as MessageKey)}</span>
        <span className="mailDnsMessage">{codeText(item.code, item.params, item.message)}</span>
        {item.found.length > 0 && <code dir="ltr" className="mailDnsFound">{item.found.join('\n')}</code>}
      </li>;
    })}</ul>
    <h4>{t('maildom.records')}</h4>
    <p className="muted">{t('maildom.recordsHelp')}</p>
    <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('maildom.records')}><table className="dataTable mailDnsRecords">
      <thead><tr><th scope="col">{t('maildom.col.type')}</th><th scope="col">{t('maildom.col.name')}</th><th scope="col">{t('maildom.col.value')}</th><th scope="col"><span className="visuallyHidden">{t('maildom.copy')}</span></th></tr></thead>
      <tbody>{report.records.map((record) => <tr key={record.kind}>
        <td>{record.type}</td>
        <td><code dir="ltr">{record.name}</code></td>
        <td>{record.value && <code dir="ltr">{record.value}</code>}{record.note && <small className="muted">{codeText(record.note_code, {}, record.note)}</small>}</td>
        <td><button type="button" className="secondary iconButton" aria-label={t('maildom.copyRecord', { kind: t(`maildom.dns.${record.kind}` as MessageKey) })} onClick={() => void copy(record.kind, recordLine(record))}><Copy aria-hidden="true" /></button>
          {copied === record.kind && <small role="status">{t('maildom.copied')}</small>}
          {copied === `fail:${record.kind}` && <small role="alert">{t('maildom.copyFailed')}</small>}</td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}
