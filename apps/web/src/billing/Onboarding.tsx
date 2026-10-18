// The first-run checklist on Home for billing admins (docs/billing.md#self-serve-sign-up): invite the
// team, connect email, choose a plan, add payment details, with a way into Setup plans. Hidden when
// complete or dismissed, and for anyone the API refuses (403) or who has no billing account (404).
// Also the shared notice for 402 plan_limit_reached and billing_read_only from any request.
import { useEffect, useState } from 'react';
import { AlertTriangle, ArrowRight, CheckCircle2, Circle, ListChecks, X } from 'lucide-react';
import * as b from '../lib/billingApi';
import { BILLING_REFUSAL_EVENT } from '../lib/api';
import { ONBOARDING_LINKS, type Onboarding } from '../lib/billing';
import { describeError } from '../lib/errors';
import { t } from '../lib/i18n';
import './billing.css';

export function OnboardingChecklist() {
  const [data, setData] = useState<Onboarding | null>(null), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  useEffect(() => { let live = true; void b.getOnboarding().then((value) => { if (live) setData(value); }).catch(() => undefined); return () => { live = false; }; }, []);
  if (!data || data.complete || data.dismissed || !data.items.length) return null;
  const done = data.items.filter((item) => item.done).length;
  const dismiss = async () => {
    setBusy(true); setProblem(null);
    try { await b.dismissOnboarding(); setData({ ...data, dismissed: true }); } catch (e) { setProblem(describeError(e)); setBusy(false); }
  };
  return <section className="surface blChecklist" aria-labelledby="onboarding-title">
    <div className="blHead"><div><p className="eyebrow"><ListChecks aria-hidden="true" className="blInlineIcon" />{t('billing.onboarding.eyebrow')}</p><h2 id="onboarding-title">{t('billing.onboarding.title')}</h2>
      <p className="muted">{t('billing.onboarding.progress', { done, total: data.items.length })}</p></div>
      <button type="button" className="secondary" disabled={busy} onClick={() => void dismiss()}><X aria-hidden="true" />{t('billing.onboarding.dismiss')}</button></div>
    <div className="blProgress" role="progressbar" aria-label={t('billing.onboarding.title')} aria-valuemin={0} aria-valuemax={data.items.length} aria-valuenow={done}><span style={{ inlineSize: `${Math.round((done / data.items.length) * 100)}%` }} /></div>
    <ul className="blSteps blChecks">{data.items.map((item) => {
      const link = ONBOARDING_LINKS[item.key];
      if (!link) return null;
      return <li key={item.key} className={item.done ? 'done' : undefined}>
        {item.done ? <CheckCircle2 aria-hidden="true" /> : <Circle aria-hidden="true" />}
        <span>{t(link.label)}<span className="visuallyHidden">: {item.done ? t('billing.onboarding.done') : t('billing.onboarding.todo')}</span></span>
        {!item.done && <a href={link.hash} className="blLink">{t(`${link.label}.go` as 'billing.onboarding.invite_team.go')}<ArrowRight aria-hidden="true" className="blFlip" /></a>}
      </li>;
    })}</ul>
    <p className="muted blSmall">{t('billing.onboarding.setupBefore')}<a href="#setup">{t('billing.onboarding.setupLink')}</a>{t('billing.onboarding.setupAfter')}</p>
    {problem && <p className="notice errorNotice" role="alert">{t('billing.failed', { message: problem })}</p>}
  </section>;
}

/** A plan limit or read-only refusal from any request, with the way to Settings > Billing. */
export function BillingNotice() {
  const [refusal, setRefusal] = useState<{ code: string; message: string } | null>(null);
  useEffect(() => {
    const on = (event: Event) => { const detail = (event as CustomEvent<{ code: string; message: string | null }>).detail; setRefusal({ code: detail.code, message: detail.message ?? t('billing.refusal.fallback') }); };
    window.addEventListener(BILLING_REFUSAL_EVENT, on);
    return () => window.removeEventListener(BILLING_REFUSAL_EVENT, on);
  }, []);
  if (!refusal) return null;
  return <div className="notice errorNotice blRefusal" role="alert">
    <AlertTriangle aria-hidden="true" />
    <div><strong>{refusal.code === 'billing_read_only' ? t('billing.refusal.readOnly') : t('billing.refusal.limit')}</strong><p>{refusal.message}</p></div>
    <div className="blRefusalActions"><a className="blLink primary" href="#billing" onClick={() => setRefusal(null)}>{t('billing.refusal.open')}</a>
      <button type="button" className="secondary iconOnly" aria-label={t('billing.refusal.dismiss')} onClick={() => setRefusal(null)}><X aria-hidden="true" /></button></div>
  </div>;
}
