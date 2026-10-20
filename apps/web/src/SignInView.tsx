// Sign-in, create-account and pending-account screens (docs/identity.md#google-and-apple-sign-in).
// Every button starts the same authorization code + PKCE sign-in against Keycloak; Google and
// Apple add kc_idp_hint, "Sign up with email" adds prompt=create. Passwords are only ever typed
// on Keycloak's pages, which keep the two-factor rules.
import { useEffect, useState, type ReactNode } from 'react';
import { CircleDot, Copy, LogIn, LogOut, Mail, RefreshCw, UserPlus } from 'lucide-react';
import type { SignInOptions, SignInProvider } from '@anum/contracts';
import * as api from './lib/api';
import type { SignInRequestOptions } from './lib/oidc';
import { canCreateAccount, NO_SIGN_IN_OPTIONS, type PendingAccount } from './lib/signIn';
import { describeError } from './lib/errors';
import { desktopShell } from './lib/desktop';
import { t } from './lib/i18n';

function Brand() {
  return <div className="brand"><span className="brandMark" aria-hidden="true">A</span><div>ANUM<small>{t('brand.tagline')}</small></div></div>;
}

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'} aria-live={error ? undefined : 'polite'}><CircleDot aria-hidden="true"/>{children}</p>;
}

/** The providers' own marks, as their brand guidelines ask for on sign-in buttons. */
function ProviderMark({ id }: { id: SignInProvider['id'] }) {
  if (id === 'google') {
    return <svg className="providerMark" viewBox="0 0 18 18" aria-hidden="true" focusable="false"><path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"/><path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"/><path fill="#FBBC05" d="M3.97 10.72A5.4 5.4 0 0 1 3.68 9c0-.6.1-1.18.29-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.05l3.01-2.33z"/><path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"/></svg>;
  }
  return <svg className="providerMark" viewBox="0 0 17 20" aria-hidden="true" focusable="false"><path fill="currentColor" d="M14.04 10.63c-.02-2.2 1.8-3.26 1.88-3.31-1.03-1.5-2.62-1.7-3.18-1.73-1.35-.14-2.64.8-3.33.8-.69 0-1.74-.78-2.86-.76A4.24 4.24 0 0 0 2.97 7.8C1.43 10.46 2.57 14.4 4.07 16.56c.73 1.06 1.6 2.24 2.74 2.2 1.1-.05 1.51-.71 2.84-.71 1.32 0 1.7.71 2.86.69 1.18-.02 1.93-1.07 2.65-2.13a9.4 9.4 0 0 0 1.2-2.47 3.8 3.8 0 0 1-2.32-3.5zM11.86 4.17A3.86 3.86 0 0 0 12.75 1.4a3.94 3.94 0 0 0-2.55 1.32 3.7 3.7 0 0 0-.92 2.69 3.26 3.26 0 0 0 2.58-1.24z"/></svg>;
}

function ProviderButtons({ options, busy, disabled, verb, onSignIn }: { options: SignInOptions; busy: boolean; disabled?: boolean; verb: 'auth.continueWith' | 'auth.signUpWith'; onSignIn: (request: SignInRequestOptions) => void }) {
  if (!options.identity_providers.length) return null;
  return <div className="actions providerButtons">{options.identity_providers.map(provider => <button key={provider.id} type="button" className={`secondary providerButton provider-${provider.id}`} disabled={busy || disabled} onClick={() => onSignIn({ idpHint: provider.id })}><ProviderMark id={provider.id}/>{t(verb, { provider: provider.label })}</button>)}</div>;
}

/**
 * The public Privacy Policy and Terms of Service pages the web host serves at /privacy and
 * /terms (docs/legal-pages.md). The desktop app loads the build from disk, where those paths
 * are not served, so it shows no links.
 */
function LegalLinks() {
  if (desktopShell()) return null;
  return <nav className="signInLegal" aria-label={t('auth.legalLinks')}><a href="/privacy">{t('auth.privacyLink')}</a><a href="/terms">{t('auth.termsLink')}</a></nav>;
}

function PrivacyConsent({ url, checked, onChange }: { url: string; checked: boolean; onChange: (value: boolean) => void }) {
  const [before, after = ''] = t('auth.acceptPrivacy', { link: '\u0001' }).split('\u0001');
  return <label className="consent"><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} aria-describedby="consent-hint"/><span>{before}<a href={url} target="_blank" rel="noreferrer noopener">{t('auth.privacyPolicy')}</a>{after}</span></label>;
}

/**
 * The signed-out screen. `onSignIn` starts the Keycloak sign-in (and rejects with the reason
 * when it cannot start); the options decide which buttons exist.
 */
export function SignInScreen({ message, onSignIn }: { message?: string; onSignIn: (request: SignInRequestOptions) => Promise<void> }) {
  const [options, setOptions] = useState<SignInOptions | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [mode, setMode] = useState<'signIn' | 'create'>(() => location.hash === '#create-account' ? 'create' : 'signIn');
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.getSignInOptions().then(value => { if (live) setOptions(value); }).catch(e => { if (live) { setOptions(NO_SIGN_IN_OPTIONS); setOptionsError(describeError(e)); } });
    return () => { live = false; };
  }, []);
  const loaded = options ?? NO_SIGN_IN_OPTIONS;
  const creatable = canCreateAccount(loaded);
  const start = (request: SignInRequestOptions) => {
    setBusy(true); setError(null);
    // The return route is the current hash: never come back to the create-account screen.
    if (location.hash === '#create-account') history.replaceState(null, '', location.pathname);
    void onSignIn(request).then(() => setBusy(false), e => { setBusy(false); setError(describeError(e)); });
  };
  const switchMode = (next: 'signIn' | 'create') => { setMode(next); setError(null); history.replaceState(null, '', next === 'create' ? '#create-account' : location.pathname); };

  if (mode === 'create' && creatable && loaded.privacy_policy_url) {
    return <main className="signInShell"><section className="surface signInCard" aria-labelledby="sign-in-title"><Brand/><h1 id="sign-in-title">{t('auth.createTitle')}</h1><p className="muted">{t('auth.createText')}</p>
      {error && <Notice error>{error}</Notice>}
      <PrivacyConsent url={loaded.privacy_policy_url} checked={accepted} onChange={setAccepted}/>
      {!accepted && <p className="muted small" id="consent-hint">{t('auth.acceptFirst')}</p>}
      <ProviderButtons options={loaded} busy={busy} disabled={!accepted} verb="auth.signUpWith" onSignIn={start}/>
      {loaded.self_sign_up && <><div className="actions"><button type="button" disabled={busy || !accepted} onClick={() => start({ create: true })} aria-describedby="sign-up-email-hint"><Mail aria-hidden="true"/>{t('auth.signUpEmail')}</button></div><p className="muted small" id="sign-up-email-hint">{t('auth.signUpEmailHint')}</p></>}
      <p className="signInSwitch">{t('auth.haveAccount')} <button type="button" className="linkButton" onClick={() => switchMode('signIn')}>{t('auth.signIn')}</button></p>
      <LegalLinks/>
    </section></main>;
  }

  return <main className="signInShell"><section className="surface signInCard" aria-labelledby="sign-in-title" aria-busy={options === null}><Brand/><h1 id="sign-in-title">{t('auth.signInTitle')}</h1><p className="muted">{t('auth.signInText')}</p>
    {message && <Notice error>{message}</Notice>}
    {error && <Notice error>{error}</Notice>}
    {optionsError && <Notice>{t('auth.optionsUnavailable', { reason: optionsError })}</Notice>}
    <ProviderButtons options={loaded} busy={busy} verb="auth.continueWith" onSignIn={start}/>
    {loaded.identity_providers.length > 0 && <p className="signInDivider" role="separator"><span>{t('auth.or')}</span></p>}
    <div className="actions"><button type="button" disabled={busy} onClick={() => start({})}><LogIn aria-hidden="true"/>{loaded.identity_providers.length ? t('auth.signInEmail') : t('auth.signIn')}</button></div>
    <p className="signInSwitch"><a href="#pricing">{t('pricing.link')}</a></p>
    {creatable && <p className="signInSwitch">{t('auth.newHere')} <button type="button" className="linkButton" onClick={() => switchMode('create')}><UserPlus aria-hidden="true"/>{t('auth.createAccount')}</button></p>}
    <LegalLinks/>
  </section></main>;
}

/**
 * Signed in without an organization (docs/identity.md#pending-accounts). Shows what an
 * administrator needs to find the account, re-checks with a fresh token, and signs out.
 */
export function PendingScreen({ account, onCheckAgain, onSignOut }: { account: PendingAccount; onCheckAgain: () => Promise<boolean>; onSignOut: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const details = [account.email ? `${t('auth.pendingEmail')}: ${account.email}` : null, account.subject ? `${t('auth.pendingAccountId')}: ${account.subject}` : null].filter(Boolean).join('\n');
  const check = () => {
    setBusy(true); setNote(null);
    void onCheckAgain().then(stillPending => { setBusy(false); if (stillPending) setNote({ text: t('auth.stillPending') }); }, e => { setBusy(false); setNote({ text: describeError(e), error: true }); });
  };
  const copy = () => { void navigator.clipboard?.writeText(details).then(() => setNote({ text: t('auth.copied') }), () => undefined); };
  return <main className="signInShell"><section className="surface signInCard" aria-labelledby="pending-title"><Brand/><h1 id="pending-title">{t('auth.pendingTitle')}</h1><p>{t('auth.pendingText')}</p>
    <dl className="pendingDetails"><div><dt>{t('auth.pendingEmail')}</dt><dd>{account.email ?? t('auth.pendingNoEmail')}</dd></div>{account.subject && <div><dt>{t('auth.pendingAccountId')}</dt><dd><code>{account.subject}</code></dd></div>}</dl>
    <p className="muted">{t('auth.pendingNext')}</p>
    <p className="signInSwitch">{t('signup.pendingOwn')} <a href="#signup">{t('signup.pendingLink')}</a></p>
    {note && <Notice error={note.error}>{note.text}</Notice>}
    <div className="actions"><button type="button" disabled={busy} onClick={check}><RefreshCw aria-hidden="true"/>{t('auth.checkAgain')}</button><button type="button" className="secondary" onClick={copy}><Copy aria-hidden="true"/>{t('auth.copyDetails')}</button><button type="button" className="secondary" onClick={onSignOut}><LogOut aria-hidden="true"/>{t('auth.signOut')}</button></div>
  </section></main>;
}
