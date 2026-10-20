// The public pricing page (#pricing, no sign-in) and self-serve sign-up (#signup?plan=<id>), drawn
// outside the signed-in app (docs/billing.md#self-serve-sign-up). Sign-up starts Keycloak's own
// registration page (prompt=create, the privacy policy accepted first, ANUM never sees a password),
// then a verified account without an organisation names it here and enters its new workspace.
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, Building2, CheckCircle2, LogIn, LogOut, MailCheck, RefreshCw, Sparkles, UserPlus } from 'lucide-react';
import type { SignInOptions } from '@anum/contracts';
import * as api from '../lib/api';
import * as auth from '../lib/auth';
import * as b from '../lib/billingApi';
import { COUNTRIES, formatMoney, formatRate, planFromHash, publicRoute, signupProblems, type Plan, type PublicCatalog, type SignupResult, type SignupStatus } from '../lib/billing';
import { countryName } from '../lib/billing';
import { canCreateAccount, NO_SIGN_IN_OPTIONS } from '../lib/signIn';
import { ApiError, describeError } from '../lib/errors';
import { t } from '../lib/i18n';
import { Note, PlanCard, date, priceLine } from './parts';

/** Which public screen the address bar asks for; re-renders when the hash changes. */
export function usePublicRoute(): 'pricing' | 'signup' | null {
  const [, setHash] = useState(() => location.hash);
  useEffect(() => {
    const sync = () => setHash(location.hash);
    window.addEventListener('hashchange', sync); window.addEventListener('popstate', sync);
    return () => { window.removeEventListener('hashchange', sync); window.removeEventListener('popstate', sync); };
  }, []);
  return publicRoute(location.hash);
}

function Shell({ children, wide, labelledBy }: { children: ReactNode; wide?: boolean; labelledBy: string }) {
  return <main className="signInShell blPublic"><section className={`surface signInCard ${wide ? 'blPublicWide' : ''}`} aria-labelledby={labelledBy}>
    <div className="brand"><span className="brandMark" aria-hidden="true">A</span><div>ANUM<small>{t('brand.tagline')}</small></div></div>
    {children}
  </section></main>;
}

function usePlans() {
  const [catalog, setCatalog] = useState<PublicCatalog | null>(null), [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(async () => { setProblem(null); try { setCatalog(await b.getPublicPlans()); } catch (e) { setProblem(describeError(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  return { catalog, problem, reload: load };
}

// ---- pricing -------------------------------------------------------------------------------------------------

export function PricingPage({ signedIn }: { signedIn: boolean }) {
  const { catalog, problem, reload } = usePlans();
  const leave = <a className="blLink" href={signedIn ? '#home' : '#'} onClick={signedIn ? undefined : (event) => { event.preventDefault(); history.replaceState(null, '', location.pathname); window.dispatchEvent(new HashChangeEvent('hashchange')); }}>
    {signedIn ? <><ArrowLeft aria-hidden="true" className="blFlip" />{t('pricing.backToApp')}</> : <><LogIn aria-hidden="true" />{t('pricing.signIn')}</>}</a>;
  return <Shell wide labelledBy="pricing-title">
    <div className="blPublicHead"><div><h1 id="pricing-title">{t('pricing.title')}</h1><p className="muted">{t('pricing.lead')}</p></div>{leave}</div>
    {problem && <div className="blRetry"><Note error>{t('pricing.failed', { message: problem })}</Note><button type="button" className="secondary" onClick={() => void reload()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    {!catalog && !problem && <Note>{t('pricing.loading')}</Note>}
    {catalog && catalog.plans.length === 0 && <Note>{t('pricing.none')}</Note>}
    {catalog && catalog.plans.length > 0 && <>
      {!catalog.signup_enabled && <Note>{t('pricing.closed')}</Note>}
      <div className="blPlans blPublicPlans">{catalog.plans.map((plan) => <PlanCard key={plan.id} plan={plan}
        action={catalog.signup_enabled ? <a className="blLink primary" href={`#signup?plan=${encodeURIComponent(plan.id)}`} aria-label={t('pricing.startNamed', { plan: plan.name })}><Sparkles aria-hidden="true" />{t('pricing.start')}</a> : undefined} />)}</div>
      <p className="muted blSmall">{catalog.tax.rate_bp > 0 ? t('pricing.vat', { rate: formatRate(catalog.tax.rate_bp), country: countryName(catalog.tax.country) }) : t('pricing.noVat')}</p>
    </>}
    <nav className="signInLegal" aria-label={t('auth.legalLinks')}><a href="/privacy">{t('auth.privacyLink')}</a><a href="/terms">{t('auth.termsLink')}</a></nav>
  </Shell>;
}

// ---- sign-up -------------------------------------------------------------------------------------------------

/**
 * `status` is the app's sign-in state; `onStatus` hands a new one back (a refreshed token that now
 * names the organisation enters the app).
 */
export function SignupPage({ status, onStatus }: { status: auth.AuthStatus; onStatus: (next: auth.AuthStatus) => void }) {
  if (status.kind === 'signedOut') return <SignedOutSignup message={status.message} />;
  return <AccountSignup status={status} onStatus={onStatus} />;
}

function Steps() {
  return <ol className="blSteps">{(['signup.step1', 'signup.step2', 'signup.step3'] as const).map((key) => <li key={key}>{t(key)}</li>)}</ol>;
}

function ChosenPlan({ catalog, planId }: { catalog: PublicCatalog | null; planId: string | null }) {
  const plan = catalog?.plans.find((p) => p.id === planId);
  if (!plan) return null;
  return <p className="blChosen"><CheckCircle2 aria-hidden="true" /><span>{t('signup.chosen', { plan: plan.name, price: priceLine(plan), days: plan.trial_days })}</span></p>;
}

function SignedOutSignup({ message }: { message?: string }) {
  const { catalog } = usePlans();
  const [options, setOptions] = useState<SignInOptions | null>(null), [accepted, setAccepted] = useState(false), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(message ?? null);
  useEffect(() => { void api.getSignInOptions().then(setOptions).catch((e) => { setOptions(NO_SIGN_IN_OPTIONS); setProblem(describeError(e)); }); }, []);
  const planId = planFromHash(location.hash);
  const loaded = options ?? NO_SIGN_IN_OPTIONS, creatable = canCreateAccount(loaded) && Boolean(loaded.privacy_policy_url);
  const start = (create: boolean) => { setBusy(true); setProblem(null); void auth.signIn(create ? { create: true } : {}).then(() => setBusy(false), (e) => { setBusy(false); setProblem(describeError(e)); }); };
  const [before, after = ''] = t('auth.acceptPrivacy', { link: '\u0001' }).split('\u0001');
  return <Shell labelledBy="signup-title">
    <h1 id="signup-title">{t('signup.title')}</h1><p className="muted">{t('signup.lead')}</p>
    <ChosenPlan catalog={catalog} planId={planId} />
    <Steps />
    {problem && <Note error>{problem}</Note>}
    {options === null && <Note>{t('common.loading')}</Note>}
    {options && !creatable && <Note>{t('signup.noRegistration')}</Note>}
    {creatable && <>
      <label className="consent"><input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} aria-describedby="signup-consent-hint" /><span>{before}<a href={loaded.privacy_policy_url!} target="_blank" rel="noreferrer noopener">{t('auth.privacyPolicy')}</a>{after}</span></label>
      {!accepted && <p className="muted small" id="signup-consent-hint">{t('auth.acceptFirst')}</p>}
      <div className="actions"><button type="button" disabled={busy || !accepted} onClick={() => start(true)}><UserPlus aria-hidden="true" />{t('signup.createAccount')}</button></div>
    </>}
    <p className="signInSwitch">{t('auth.haveAccount')} <button type="button" className="linkButton" disabled={busy} onClick={() => start(false)}>{t('auth.signIn')}</button></p>
    <p className="signInSwitch"><a href="#pricing">{t('signup.seePlans')}</a></p>
  </Shell>;
}

function AccountSignup({ status, onStatus }: { status: auth.AuthStatus; onStatus: (next: auth.AuthStatus) => void }) {
  const [state, setState] = useState<SignupStatus | null>(null), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SignupResult | null>(null), [linking, setLinking] = useState(false), [note, setNote] = useState<string | null>(null);
  const load = useCallback(async () => { setProblem(null); try { setState(await b.getSignupStatus()); } catch (e) { setProblem(describeError(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  /** A fresh token: when it names the organisation the app opens in the new workspace. */
  const enter = async () => {
    setBusy(true); setNote(null);
    try {
      const next = auth.oidcEnabled ? await auth.checkAgain() : status;
      if (next.kind === 'signedIn' || next.kind === 'local') { history.replaceState(null, '', `${location.pathname}#home`); onStatus(next.kind === 'local' ? { kind: 'local' } : next); return; }
      if (next.kind === 'signedOut') { onStatus(next); return; }
      setLinking(true); setNote(t('signup.stillLinking'));
    } catch (e) { setNote(describeError(e)); } finally { setBusy(false); }
  };
  const checkEmail = async () => {
    setBusy(true); setNote(null);
    try { if (auth.oidcEnabled) { const next = await auth.checkAgain(); if (next.kind === 'signedOut') { onStatus(next); return; } } await load(); }
    catch (e) { setNote(describeError(e)); } finally { setBusy(false); }
  };
  const signOut = auth.oidcEnabled ? <button type="button" className="secondary" onClick={() => void auth.signOut()}><LogOut aria-hidden="true" />{t('auth.signOut')}</button> : null;

  if (problem && !state) return <Shell labelledBy="signup-title"><h1 id="signup-title">{t('signup.title')}</h1><Note error>{t('signup.statusFailed', { message: problem })}</Note>
    <div className="actions"><button type="button" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button>{signOut}</div></Shell>;
  if (!state) return <Shell labelledBy="signup-title"><h1 id="signup-title">{t('signup.title')}</h1><Note>{t('signup.checking')}</Note></Shell>;

  if (result || linking || state.state === 'provisioned') {
    const linked = result ? result.identity_linked : state.identity_linked !== false;
    return <Shell labelledBy="signup-title"><Building2 aria-hidden="true" className="blHeroIcon" /><h1 id="signup-title">{linked && !linking ? t('signup.readyTitle') : t('signup.linkingTitle')}</h1>
      <p>{linked && !linking ? t('signup.readyText') : t('signup.linkingText')}</p>
      {result?.trial_end && <p className="muted">{t('signup.trialUntil', { date: date(result.trial_end) })}</p>}
      {note && <Note>{note}</Note>}
      <div className="actions"><button type="button" disabled={busy} onClick={() => void enter()}>{linked && !linking ? <><Sparkles aria-hidden="true" />{t('signup.enter')}</> : <><RefreshCw aria-hidden="true" />{t('auth.checkAgain')}</>}</button>{signOut}</div>
    </Shell>;
  }
  if (state.state === 'verify_email') return <Shell labelledBy="signup-title"><MailCheck aria-hidden="true" className="blHeroIcon" /><h1 id="signup-title">{t('signup.verifyTitle')}</h1>
    <p>{state.email ? t('signup.verifyText', { email: state.email }) : t('signup.verifyTextNoEmail')}</p><p className="muted">{t('signup.verifyHint')}</p>
    {note && <Note error>{note}</Note>}
    <div className="actions"><button type="button" disabled={busy} onClick={() => void checkEmail()}><RefreshCw aria-hidden="true" />{t('auth.checkAgain')}</button>{signOut}</div>
  </Shell>;
  if (state.signup_enabled === false) return <Shell labelledBy="signup-title"><h1 id="signup-title">{t('signup.closedTitle')}</h1><p>{t('signup.closedText')}</p><div className="actions">{signOut}</div></Shell>;
  return <SignupForm email={state.email} onCreated={(created) => { setResult(created); if (created.identity_linked) void enter(); }} signOut={signOut} />;
}

function SignupForm({ email, onCreated, signOut }: { email: string | null; onCreated: (result: SignupResult) => void; signOut: ReactNode }) {
  const { catalog, problem: plansProblem, reload } = usePlans();
  const [name, setName] = useState(''), [country, setCountry] = useState('SA'), [planId, setPlanId] = useState(() => planFromHash(location.hash) ?? ''), [terms, setTerms] = useState(false);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const formId = useId();
  useEffect(() => { if (catalog && !catalog.plans.some((p) => p.id === planId)) setPlanId(catalog.plans[0]?.id ?? ''); }, [catalog, planId]);
  const plan: Plan | undefined = catalog?.plans.find((p) => p.id === planId);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body = { organization_name: name.trim().replace(/\s+/g, ' '), plan_id: planId, country, accept_terms: terms };
    const wrong = signupProblems(body);
    if (wrong.length) { setProblem(wrong.map((key) => t(key)).join(' ')); return; }
    setBusy(true); setProblem(null);
    try { onCreated(await b.createOrganisation(body)); }
    catch (e) { setProblem(signupRefusal(e)); }
    finally { setBusy(false); }
  };
  return <Shell labelledBy="signup-title">
    <h1 id="signup-title">{t('signup.formTitle')}</h1><p className="muted">{email ? t('signup.formLead', { email }) : t('signup.formLeadNoEmail')}</p>
    <form onSubmit={(e) => void submit(e)} noValidate className="blSignupForm">
      <label className="field" htmlFor={`${formId}-name`}><span>{t('signup.orgName')}</span><input id={`${formId}-name`} value={name} maxLength={160} autoComplete="organization" dir="auto" onChange={(e) => setName(e.target.value)} required /></label>
      <label className="field" htmlFor={`${formId}-country`}><span>{t('signup.country')}</span><select id={`${formId}-country`} value={country} onChange={(e) => setCountry(e.target.value)}>{COUNTRIES.map((code) => <option key={code} value={code}>{countryName(code)}</option>)}</select></label>
      <label className="field" htmlFor={`${formId}-plan`}><span>{t('signup.plan')}</span>
        <select id={`${formId}-plan`} value={planId} disabled={!catalog?.plans.length} onChange={(e) => setPlanId(e.target.value)}>{(catalog?.plans ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}: {formatMoney(p.price_minor, p.currency)}</option>)}</select></label>
      {plansProblem && <div className="blRetry"><Note error>{t('pricing.failed', { message: plansProblem })}</Note><button type="button" className="secondary" onClick={() => void reload()}>{t('common.tryAgain')}</button></div>}
      {plan && <p className="muted blSmall">{t('signup.planNote', { price: priceLine(plan), days: plan.trial_days })}</p>}
      <label className="consent"><input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} /><span>{t('signup.acceptBefore')}<a href="/terms" target="_blank" rel="noreferrer noopener">{t('auth.termsLink')}</a>{t('signup.acceptAfter')}</span></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy || !planId}>{busy ? t('signup.creating') : <><Building2 aria-hidden="true" />{t('signup.create')}</>}</button>{signOut}</div>
    </form>
  </Shell>;
}

/** A refused sign-up as a sentence that says what to do: verify first, already created, fix the form, or wait. */
export function signupRefusal(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'email_not_verified') return t('signup.refused.verify', { message: error.detail ?? '' });
    if (error.status === 409) return t('signup.refused.exists', { message: error.detail ?? '' });
    if (error.status === 422) return t('signup.refused.invalid', { message: error.details.map((d) => d.message).join(' ') || error.detail || '' });
    if (error.status === 429) return t('signup.refused.limited', { message: error.detail ?? '' });
    if (error.status === 404) return t('signup.refused.closed');
  }
  return t('billing.failed', { message: describeError(error) });
}
