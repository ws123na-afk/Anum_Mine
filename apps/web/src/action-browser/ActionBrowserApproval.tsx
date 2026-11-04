// The extra part of an approval card for a web.act step (docs/action-browser.md#which-steps-stop-for-approval):
// the picture of the page (owners), the site, the address, the action, the form's fields and the
// amount the page shows; for the first approval, the sites and saved items the task may use.
// Lazy chunk. The decision itself stays the Approvals page's own (payload hash and all).
// A step that types a card's security code adds a masked field for it: ANUM never saves the
// code, so the owner types it here; the card keeps it only in memory until the approve is sent
// (docs/action-browser.md#the-card-security-code).
import { useEffect, useId, useState } from 'react';
import type { Approval } from '@anum/contracts';
import { useLocale } from '../useLocale';
import { cardCodeProblem, kindText, say, stepFacts, type BrowserSession } from '../lib/actionBrowser';
import { sessionsForTask } from './api';
import { Picture } from './ActionBrowserLive';
import './actionBrowser.css';

export interface CardCodeField { value: string; onChange(value: string): void }

export default function ActionBrowserApproval({ approval, code }: { approval: Approval; code?: CardCodeField }) {
  const locale = useLocale();
  const facts = stepFacts(approval.arguments);
  const [session, setSession] = useState<BrowserSession | null>(null);
  const pending = approval.status === 'pending';
  useEffect(() => {
    if (!pending || !facts || facts.resume === 'scope') return undefined;
    let live = true;
    sessionsForTask(approval.taskId).then((all) => { if (live) setSession(all[all.length - 1] ?? null); }, () => undefined);
    return () => { live = false; };
  }, [approval.taskId, pending, facts?.resume]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!facts) return null;
  const args = approval.arguments ?? {};
  const list = (key: string): string[] => (Array.isArray(args[key]) ? (args[key] as unknown[]).map(String) : []);
  const logins = args.logins && typeof args.logins === 'object' ? Object.keys(args.logins as Record<string, unknown>).map((h) => `login:${h}`) : [];
  return <div className="abApproval" lang={locale} data-action-browser-approval={facts.resume}>
    <p className="abApprovalKind"><strong>{kindText(facts)}</strong></p>
    {facts.resume === 'scope'
      ? <dl className="abScope">
        <div><dt>{say('sites')}</dt><dd>{list('allowed_domains').map((s) => <span key={s} className="abChip" dir="ltr">{s}</span>)}</dd></div>
        <div><dt>{say('handles')}</dt><dd>{[...logins, ...list('cards').map((c) => `card:${c}`)].map((h) => <span key={h} className="abChip" dir="ltr">{h}</span>)}{logins.length + list('cards').length === 0 && '-'}</dd></div>
        <p className="abMuted">{say('approvalScope')}</p>
      </dl>
      : <>
        <dl className="abFacts">
          {facts.site && <div><dt>{say('approvalSite')}</dt><dd dir="ltr">{facts.site}</dd></div>}
          {facts.url && <div><dt>{say('approvalAddress')}</dt><dd dir="ltr" className="abBreak">{facts.url}</dd></div>}
          {facts.target && <div><dt>{say('approvalAction')}</dt><dd dir="auto">{facts.target}</dd></div>}
          {facts.value && <div><dt>{say('approvalValue')}</dt><dd dir="ltr">{facts.value}</dd></div>}
          {facts.amount && <div className="abAmount"><dt>{say('approvalAmount')}</dt><dd dir="ltr"><strong>{facts.amount}</strong></dd></div>}
        </dl>
        {facts.amount && <p className="abMuted">{say('amountNote')}</p>}
        {facts.fields.length > 0 && <details className="abFields"><summary>{say('approvalFields')} ({facts.fields.length})</summary>
          <ul>{facts.fields.map((f, i) => <li key={`${f.label}-${i}`} dir="auto">{f.label} <small className="abMuted" dir="ltr">{f.type}</small></li>)}</ul></details>}
        {facts.resume === 'handoff' && <p className="abNote" role="note">{say('approvalHandoff')}</p>}
        {code && <CardCode field={code} />}
        {pending && session && <Picture session={session} interval={2000} />}
      </>}
  </div>;
}

/** The masked field for the card security code: digits only, never autofilled or saved by the browser. */
function CardCode({ field }: { field: CardCodeField }) {
  const id = useId();
  const [touched, setTouched] = useState(false);
  const problem = touched ? cardCodeProblem(field.value) : null;
  return <div className="abCode">
    <label className="field" htmlFor={`${id}-code`}>
      <span>{say('codeLabel')}</span>
      <input id={`${id}-code`} type="password" inputMode="numeric" pattern="[0-9]*" maxLength={4} size={6} dir="ltr"
        autoComplete="off" spellCheck={false} data-1p-ignore="" data-lpignore="true" required
        aria-invalid={problem ? true : undefined} aria-describedby={`${id}-help${problem ? ` ${id}-error` : ''}`}
        value={field.value} onBlur={() => setTouched(true)}
        onChange={(event) => field.onChange(event.target.value.replace(/\D/g, '').slice(0, 4))} />
    </label>
    <small id={`${id}-help`} className="abMuted">{say('codeHelp')}</small>
    {problem && <small id={`${id}-error`} className="abError" role="alert">{problem}</small>}
  </div>;
}
