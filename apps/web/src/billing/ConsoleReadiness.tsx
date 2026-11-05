// The platform owner's "Ready to sell" checklist (docs/selling-readiness.md#checklist). Every item is read
// from real state by GET /api/v1/platform/readiness; nothing here is assumed. Each item says what is
// missing and links to where it is fixed. SPF cannot be known from the code, so a button resolves the
// mail domain's SPF and DMARC records on the server and shows exactly what they allow.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { CircleAlert, CircleCheck, CircleDashed, CircleX, Globe, RefreshCw } from 'lucide-react';
import { describeError, isPermissionDenied } from '../lib/errors';
import { formatDateTime, t, type MessageKey } from '../lib/i18n';
import { fixTarget, sortReadiness, validDomain, type DnsResult, type Readiness, type ReadinessItem } from '../lib/selling';
import { checkDns, getReadiness } from '../lib/sellingApi';
import { Badge, Note } from './parts';

type Load = { state: 'loading' } | { state: 'ready'; value: Readiness } | { state: 'denied' | 'error'; message: string };
export type ConsoleTab = 'settings' | 'company' | 'plans';
const TONE = { done: 'success', warning: 'warning', missing: 'dangerTone', unknown: 'neutral' } as const;
const ICON = { done: CircleCheck, warning: CircleAlert, missing: CircleX, unknown: CircleDashed } as const;

export function ConsoleReadiness({ onOpenTab }: { onOpenTab: (tab: ConsoleTab) => void }) {
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const refresh = useCallback(async () => {
    setLoad({ state: 'loading' });
    try { setLoad({ state: 'ready', value: await getReadiness() }); }
    catch (e) { setLoad({ state: isPermissionDenied(e) ? 'denied' : 'error', message: describeError(e) }); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  if (load.state === 'loading') return <section className="surface blPanel" aria-busy="true"><Note>{t('selling.ready.loading')}</Note></section>;
  if (load.state !== 'ready') return <section className="surface blPanel"><div className="blRetry">
    <Note error>{load.state === 'denied' ? t('selling.ready.denied') : t('selling.ready.failed', { message: load.message })}</Note>
    <button type="button" className="secondary" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></section>;
  const value = load.value;
  return <div className="blStack">
    <section className="surface blPanel" aria-labelledby="ready-title">
      <div className="blHead"><div><h2 id="ready-title">{t('selling.ready.title')}</h2><p className="muted">{t('selling.ready.lead')}</p></div>
        <div className="blBadges"><Badge tone={value.ready ? 'success' : 'warning'}>{t('selling.ready.count', { done: value.done, total: value.total })}</Badge>
          <button type="button" className="secondary" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('selling.ready.recheck')}</button></div></div>
      <p className="muted blSmall">{t('selling.ready.checked', { when: formatDateTime(value.checked_at, { dateStyle: 'medium', timeStyle: 'short' }) })}</p>
      <ul className="blReady">{sortReadiness(value.items).map((item) => <ReadyItem key={item.key} item={item} onOpenTab={onOpenTab} />)}</ul>
    </section>
    <DnsCheck defaultDomain={value.mail_domain ?? ''} />
  </div>;
}

function detailText(item: ReadinessItem): string[] {
  const d = item.detail as Record<string, unknown>;
  const lines: string[] = [];
  switch (item.key) {
    case 'payment_provider':
      if (d.active === 'stripe' || d.active === 'moyasar') lines.push(t(`selling.ready.provider.active.${d.active}` as MessageKey, { currencies: ((d.currencies as string[] | undefined) ?? []).join(', ') }));
      lines.push(t('selling.ready.provider.stripe', { state: t(`selling.ready.providerState.${String(d.stripe)}` as MessageKey) }));
      lines.push(t('selling.ready.provider.moyasar', { state: t(`selling.ready.providerState.${String(d.moyasar)}` as MessageKey) }));
      if (d.moyasar_optional !== false) lines.push(t('selling.ready.provider.moyasarOptional'));
      if (item.state === 'warning') lines.push(t('selling.ready.provider.testKeys'));
      lines.push(d.bank_transfer ? t('selling.ready.provider.bankOn') : t('selling.ready.provider.bankOff'));
      break;
    case 'company_details': {
      const missing = (d.missing as string[] | undefined) ?? [];
      if (missing.length) lines.push(t('selling.ready.company.missing', { fields: missing.map((k) => t(`selling.company.field.${k}` as MessageKey)).join(', ') }));
      break;
    }
    case 'mail_sending':
      lines.push(d.mailbox_connected ? t('selling.ready.mail.mailboxOn') : t('selling.ready.mail.mailboxOff'));
      lines.push(d.smtp_configured ? t('selling.ready.mail.smtpOn') : t('selling.ready.mail.smtpOff'));
      lines.push(t('selling.ready.mail.spfNote'));
      break;
    case 'backups_local': case 'backups_offsite':
      if (!d.recorded) lines.push(t('selling.ready.backup.notRecorded'));
      else if (d.ok === undefined) lines.push(t('selling.ready.backup.never'));
      else {
        if (d.finished_at) lines.push(t(d.ok ? 'selling.ready.backup.lastOk' : 'selling.ready.backup.lastFailed', { when: formatDateTime(String(d.finished_at), { dateStyle: 'medium', timeStyle: 'short' }) }));
        if (!d.ok) lines.push(d.last_success_at ? t('selling.ready.backup.lastSuccess', { when: formatDateTime(String(d.last_success_at), { dateStyle: 'medium', timeStyle: 'short' }) }) : t('selling.ready.backup.noSuccess'));
        if (item.key === 'backups_offsite' && !d.ok) lines.push(t('selling.ready.backup.offsiteHint'));
      }
      break;
    case 'legal_pages': {
      const missing = (d.missing as Record<string, number> | undefined) ?? {};
      const total = Object.values(missing).reduce((a, b) => a + b, 0);
      lines.push(total ? t('selling.ready.legal.missing', { count: total }) : t('selling.ready.legal.filled'));
      lines.push(t('selling.ready.legal.published', { count: ((d.published as string[] | undefined) ?? []).length, total: 5 }));
      break;
    }
    case 'plans':
      lines.push(t('selling.ready.plans.counts', { active: Number(d.active ?? 0), published: Number(d.published ?? 0) }));
      break;
    case 'ai_model':
      lines.push(d.placeholder ? t('selling.ready.model.placeholder')
        : d.key_missing ? t('selling.ready.model.keyMissing', { provider: String(d.provider) })
          : t('selling.ready.model.real', { provider: String(d.provider) }));
      break;
    case 'public_issuer':
      if (!d.configured) lines.push(t('selling.ready.issuer.notSet'));
      else if (!d.is_you) lines.push(t('selling.ready.issuer.other'));
      if (!d.web_app_url) lines.push(t('selling.ready.issuer.noWebUrl'));
      break;
  }
  return lines;
}

function ReadyItem({ item, onOpenTab }: { item: ReadinessItem; onOpenTab: (tab: ConsoleTab) => void }) {
  const Icon = ICON[item.state];
  const target = fixTarget(item.fix);
  return <li className={`blReadyItem ${item.state}`}>
    <Icon aria-hidden="true" className="blReadyIcon" />
    <div>
      <h3>{t(`selling.ready.item.${item.key}` as MessageKey)} <Badge tone={TONE[item.state]}>{t(`selling.ready.state.${item.state}` as MessageKey)}</Badge></h3>
      <p className="muted">{t(`selling.ready.why.${item.key}` as MessageKey)}</p>
      {detailText(item).map((line, i) => <p key={i} className="blSmall" dir="auto">{line}</p>)}
      {item.state !== 'done' && <p className="blSmall"><strong>{t('selling.ready.fixLabel')}</strong> {t(`selling.ready.fix.${item.key}` as MessageKey)}</p>}
      {item.state !== 'done' && (target.tab ? <button type="button" className="secondary" onClick={() => onOpenTab(target.tab!)}>{t(`selling.ready.open.${target.tab}` as MessageKey)}</button>
        : target.href ? <a className="blLinkButton" href={target.href}>{t('selling.ready.open.mail')}</a> : null)}
    </div>
  </li>;
}

function DnsCheck({ defaultDomain }: { defaultDomain: string }) {
  const [domain, setDomain] = useState(defaultDomain), [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DnsResult | null>(null), [problem, setProblem] = useState<string | null>(null);
  const id = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!validDomain(domain)) { setProblem(t('selling.dns.badDomain')); return; }
    setBusy(true); setProblem(null);
    try { setResult(await checkDns(domain)); } catch (e) { setProblem(t('selling.dns.failed', { message: describeError(e) })); } finally { setBusy(false); }
  };
  return <section className="surface blPanel" aria-labelledby={`${id}-title`}>
    <h2 id={`${id}-title`}><Globe aria-hidden="true" className="blInlineIcon" />{t('selling.dns.title')}</h2>
    <p className="muted">{t('selling.dns.lead')}</p>
    <form className="blInlineForm" onSubmit={(e) => void submit(e)} noValidate>
      <label className="field" htmlFor={`${id}-domain`}><span>{t('selling.dns.domain')}</span><input id={`${id}-domain`} dir="ltr" value={domain} placeholder="example.com" onChange={(e) => setDomain(e.target.value)} /></label>
      <button type="submit" disabled={busy}>{busy ? t('selling.dns.checking') : t('selling.dns.check')}</button>
    </form>
    {problem && <Note error>{problem}</Note>}
    {result && <div className="blDns" role="status">
      <h3>SPF <Badge tone={result.spf.status === 'ok' ? 'success' : 'dangerTone'}>{t(`selling.dns.spf.${result.spf.status}` as MessageKey)}</Badge></h3>
      {result.spf.record ? <code dir="ltr" className="blRecord">{result.spf.record}</code> : <p className="muted">{result.spf.error ? t('selling.dns.lookupFailed', { error: result.spf.error }) : t('selling.dns.noRecord')}</p>}
      <p className="blSmall">{t(`selling.dns.spfHelp.${result.spf.status}` as MessageKey)}</p>
      <h3>DMARC <Badge tone={result.dmarc.status === 'ok' ? 'success' : result.dmarc.status === 'monitor' ? 'warning' : 'dangerTone'}>{t(`selling.dns.dmarc.${result.dmarc.status}` as MessageKey)}</Badge></h3>
      {result.dmarc.record ? <code dir="ltr" className="blRecord">{result.dmarc.record}</code> : <p className="muted">{t('selling.dns.noRecord')}</p>}
      <p className="muted blSmall">{t('selling.dns.checkedAt', { domain: result.domain, when: formatDateTime(result.checked_at, { dateStyle: 'medium', timeStyle: 'short' }) })}</p>
    </div>}
  </section>;
}
