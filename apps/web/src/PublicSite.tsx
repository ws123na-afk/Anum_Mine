// The public pages that need no sign-in (docs/legal-pages.md): /legal/* render the documents in
// docs/legal, /trust lists the security facts, /status checks the API and sign-in from the browser.
// main.tsx renders this instead of the signed-in app when the path is one of them.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Activity, Bot, Building2, CircleAlert, CircleCheck, CircleDashed, CircleX, DatabaseBackup, Download, FileText, KeyRound, Languages, Layers, LockKeyhole, RefreshCw, ScanSearch, ScrollText, ShieldCheck, WifiOff } from 'lucide-react';
import { AnumMark } from './Shell';
import { apiBaseUrl } from './lib/api';
import { chooseLocale, formatDateTime, getLocale, t, type MessageKey } from './lib/i18n';
import { parseMarkdown, type MdBlock, type MdInline } from './lib/markdown';
import { LEGAL_SOURCES } from './lib/legalDocs';
import {
  LEGAL_DOCS, STATUS_PATH, STATUS_REFRESH_MS, TRUST_PATH, apiHealthy, checkEndpoint, discoveryHealthy, fillPlaceholders, headingIds, internalPath,
  legalLanguage, legalState, localizeLegalValues, overallState, parsePublished, publishedIds, readLegalDocument, remainingPlaceholders, rewriteDocLinks, statusTargets,
  type ComponentCheck, type ComponentState, type LegalDocId, type PublicRoute,
} from './lib/publicSite';
import { getPublicLegal } from './lib/publicLegalApi';
import type { PublicLegal } from './lib/selling';
import './publicSite.css';

/** The contact for vulnerability reports, as in SECURITY.md. */
export const SECURITY_CONTACT = 'info@shahzebrahman.com';
const SECURITY_POLICY_URL = 'https://github.com/ws123na-afk/Anum_Mine/blob/main/SECURITY.md';
const PUBLISHED = parsePublished(import.meta.env.VITE_ANUM_LEGAL_PUBLISHED);
const DOC_TITLE: Record<LegalDocId, MessageKey> = {
  terms: 'public.doc.terms', privacy: 'public.doc.privacy', dpa: 'public.doc.dpa', aup: 'public.doc.aup', subprocessors: 'public.doc.subprocessors',
};

export function PublicSite({ route }: { route: PublicRoute }) {
  const title = route.kind === 'legal' ? t(DOC_TITLE[route.doc]) : route.kind === 'trust' ? t('public.trust.title') : t('public.status.title');
  useEffect(() => { document.title = `${title} | ANUM`; }, [title]);
  const current = route.kind === 'legal' ? LEGAL_DOCS.find((d) => d.id === route.doc)!.path : route.kind === 'trust' ? TRUST_PATH : STATUS_PATH;
  return <div className="publicSite">
    <a className="publicSkip" href="#public-main">{t('public.skip')}</a>
    <header className="publicHeader">
      <a className="publicBrand" href="/"><AnumMark size={30} /><span>ANUM</span></a>
      <nav className="publicNav" aria-label={t('public.nav.label')}>
        <a href={TRUST_PATH} aria-current={current === TRUST_PATH ? 'page' : undefined}>{t('public.nav.trust')}</a>
        <a href={STATUS_PATH} aria-current={current === STATUS_PATH ? 'page' : undefined}>{t('public.nav.status')}</a>
        <a href="/legal/privacy" aria-current={route.kind === 'legal' ? 'page' : undefined}>{t('public.nav.legal')}</a>
        {/* Named by its text (WCAG 2.5.3): what it does in this page's language, then the other language's name in that language. */}
        <button type="button" className="publicLang" onClick={() => chooseLocale(getLocale() === 'en' ? 'ar' : 'en')}><Languages aria-hidden="true" size={16} /><span className="visuallyHidden">{t('public.language.switchLabel')}: </span><span lang={getLocale() === 'en' ? 'ar' : 'en'}>{t('public.language.switch')}</span></button>
        <a className="publicOpen" href="/">{t('public.nav.open')}</a>
      </nav>
    </header>
    <main id="public-main" className="publicMain" tabIndex={-1}>
      {route.kind === 'legal' ? <LegalPage id={route.doc} /> : route.kind === 'trust' ? <TrustPage /> : <StatusPage />}
    </main>
    <footer className="publicFooter">
      <nav aria-label={t('public.footer.legal')}>
        <h2>{t('public.footer.legal')}</h2>
        <ul>{LEGAL_DOCS.map((d) => <li key={d.id}><a href={d.path} aria-current={current === d.path ? 'page' : undefined}>{t(DOC_TITLE[d.id])}</a></li>)}</ul>
      </nav>
      <nav aria-label={t('public.footer.more')}>
        <h2>{t('public.footer.more')}</h2>
        <ul>
          <li><a href={TRUST_PATH}>{t('public.nav.trust')}</a></li>
          <li><a href={STATUS_PATH}>{t('public.nav.status')}</a></li>
          <li><a href={SECURITY_POLICY_URL} target="_blank" rel="noopener noreferrer">{t('public.trust.disclosure.policy')}</a></li>
        </ul>
      </nav>
    </footer>
  </div>;
}

// ---- Legal documents ---------------------------------------------------------------------------

// The company details and legal facts the platform owner entered (GET /api/v1/public/legal) fill the
// placeholders as the page renders; a fact not set yet shows as "Not set yet" (docs/selling-readiness.md).
const LegalValues = createContext<Record<string, string | null> | null>(null);
type LegalLoad = { state: 'loading' } | { state: 'ready'; value: PublicLegal } | { state: 'error' };
const RIGHTS_DOCS: ReadonlySet<LegalDocId> = new Set(['privacy', 'dpa', 'terms']);

function LegalPage({ id }: { id: LegalDocId }) {
  // The text follows the site language switch: docs/legal/ar for Arabic, docs/legal for English.
  const language = legalLanguage(getLocale());
  const dir = language === 'ar' ? 'rtl' : 'ltr';
  const doc = useMemo(() => readLegalDocument(LEGAL_SOURCES[language][id]), [id, language]);
  const [facts, setFacts] = useState<LegalLoad>({ state: 'loading' });
  useEffect(() => {
    let live = true;
    getPublicLegal().then((value) => { if (live) setFacts({ state: 'ready', value }); }, () => { if (live) setFacts({ state: 'error' }); });
    return () => { live = false; };
  }, []);
  const values = facts.state === 'ready' ? localizeLegalValues(facts.value.values, language) : null;
  const published = publishedIds(PUBLISHED, facts.state === 'ready' ? facts.value.published : null);
  const state = legalState(id, doc, published, values);
  const remaining = remainingPlaceholders(doc, values);
  const blocks = useMemo(() => parseMarkdown(rewriteDocLinks(doc.body)), [doc]);
  const headings = blocks.filter((b): b is Extract<MdBlock, { kind: 'heading' }> => b.kind === 'heading' && b.level === 2);
  const ids = headingIds(headings.map((h) => inlineText(h.inline)));
  const idOf = new Map(headings.map((h, i) => [h, ids[i]]));
  return <LegalValues.Provider value={values}><article className="legalDoc" lang={language} dir={dir} aria-labelledby="legal-title" aria-busy={facts.state === 'loading'}>
    <div className="publicIntro">
      <p className="publicEyebrow" lang={getLocale()}><FileText aria-hidden="true" size={16} />{t('public.legal.eyebrow')}</p>
      <h1 id="legal-title" dir={dir}>{doc.title}</h1>
      <p className="publicMeta">
        {doc.updated && <span lang={getLocale()}><PlaceholderText text={t('public.legal.updated', { date: doc.updated })} /></span>}
        {state === 'published' && <span className="publicBadge ok" lang={getLocale()}>{t('public.legal.publishedBadge')}</span>}
      </p>
      {language === 'ar' && <p className="publicNote" lang="ar">{t('public.legal.translationNote')}</p>}
    </div>
    {state !== 'published' && <section className="draftBanner" role="note" aria-labelledby="draft-title" lang={getLocale()}>
      <CircleAlert aria-hidden="true" size={20} />
      <div>
        <h2 id="draft-title">{t('public.legal.draftTitle')}</h2>
        <p>{state === 'blocked' ? t('public.legal.blockedText', { count: remaining.length }) : t('public.legal.draftText')}</p>
        <p>{t('public.legal.templateNote')}</p>
        {facts.state === 'ready' && remaining.length > 0 && <p>{t('public.legal.notSetNote', { count: remaining.length })}</p>}
        {facts.state === 'loading' && <p role="status">{t('public.legal.factsLoading')}</p>}
        {facts.state === 'error' && <p role="status">{t('public.legal.factsFailed')}</p>}
      </div>
    </section>}
    {facts.state === 'ready' && facts.value.company.legal_name && <CompanyCard company={facts.value.company} />}
    <div className={`legalLayout${headings.length > 2 ? ' withToc' : ''}`}>
      {headings.length > 2 && <nav className="legalToc" aria-labelledby="legal-toc">
        <h2 id="legal-toc" lang={getLocale()}>{t('public.legal.contents')}</h2>
        <ol>{headings.map((h, i) => <li key={ids[i]}><a href={`#${ids[i]}`}><Inlines parts={h.inline} plain /></a></li>)}</ol>
      </nav>}
      <div className="legalBody" dir={dir}>
        {blocks.map((b, i) => <Block key={i} block={b} id={b.kind === 'heading' ? idOf.get(b) : undefined} />)}
        {RIGHTS_DOCS.has(id) && <RightsPanel />}
      </div>
    </div>
  </article></LegalValues.Provider>;
}

/** Who provides the Service, from the company details (only the parts that are set). */
function CompanyCard({ company }: { company: PublicLegal['company'] }) {
  const rows: [MessageKey, string][] = ([
    ['public.company.legalName', company.legal_name], ['public.company.registration', [company.registration_number, company.registration_authority, company.registration_country].filter(Boolean).join(', ')],
    ['public.company.address', company.address], ['public.company.vat', company.vat_number], ['public.company.contact', company.contact_email],
    ['public.company.dpo', [company.dpo_name, company.dpo_email].filter(Boolean).join(', ')], ['public.company.server', company.server_location],
  ] as [MessageKey, string][]).filter(([, value]) => Boolean(value));
  return <section className="companyCard" aria-labelledby="company-card" lang={getLocale()}>
    <h2 id="company-card"><Building2 aria-hidden="true" size={18} />{t('public.company.title')}</h2>
    <dl>{rows.map(([label, value]) => <div key={label}><dt>{t(label)}</dt><dd dir="auto">{value}</dd></div>)}</dl>
  </section>;
}

/** Data subject rights wired to ANUM's own flows: a person's export and deletion, the organisation's export and deletion. */
function RightsPanel() {
  return <section className="rightsPanel" aria-labelledby="rights-title" lang={getLocale()} dir={getLocale() === 'ar' ? 'rtl' : 'ltr'}>
    <h2 id="rights-title">{t('public.rights.title')}</h2>
    <p>{t('public.rights.lead')}</p>
    <ul>
      <li><a className="publicAction" href="/#privacy"><Download aria-hidden="true" size={16} />{t('public.rights.exportMine')}</a><span>{t('public.rights.exportMineText')}</span></li>
      <li><a className="publicAction" href="/#privacy"><ShieldCheck aria-hidden="true" size={16} />{t('public.rights.deleteMine')}</a><span>{t('public.rights.deleteMineText')}</span></li>
      <li><a className="publicAction" href="/#settings"><DatabaseBackup aria-hidden="true" size={16} />{t('public.rights.organisation')}</a><span>{t('public.rights.organisationText')}</span></li>
      <li><a className="publicAction" href="/#settings"><ScrollText aria-hidden="true" size={16} />{t('public.rights.retention')}</a><span>{t('public.rights.retentionText')}</span></li>
    </ul>
    <p className="publicNote">{t('public.rights.signInNote')}</p>
  </section>;
}

function Block({ block, id }: { block: MdBlock; id?: string }) {
  switch (block.kind) {
    case 'heading': return block.level <= 2 ? <h2 id={id}><Inlines parts={block.inline} /></h2> : <h3><Inlines parts={block.inline} /></h3>;
    case 'list': {
      const items = block.items.map((item, j) => <li key={j}><Inlines parts={item} /></li>);
      return block.ordered ? <ol start={block.start === 1 ? undefined : block.start}>{items}</ol> : <ul>{items}</ul>;
    }
    case 'table': return <div className="legalTable" role="region" tabIndex={0} aria-label={t('public.legal.table')}>
      <table>
        <thead><tr>{block.header.map((cell, c) => <th key={c} scope="col"><Inlines parts={cell} /></th>)}</tr></thead>
        <tbody>{block.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c}><Inlines parts={cell} /></td>)}</tr>)}</tbody>
      </table>
    </div>;
    case 'quote': return <blockquote><Inlines parts={block.inline} /></blockquote>;
    case 'code': return <pre><code>{block.text}</code></pre>;
    case 'rule': return <hr />;
    default: return <p><Inlines parts={block.inline} /></p>;
  }
}

function inlineText(parts: MdInline[]): string { return parts.map((p) => p.text).join(''); }

function PlaceholderText({ text }: { text: string }) {
  const values = useContext(LegalValues);
  return <>{fillPlaceholders(text, values).map((part, i) => part.kind === 'missing'
    ? <mark key={i} className="placeholder" title={part.text}><span lang={getLocale()}>{t('public.legal.notSet')}</span> <small dir="ltr">{part.key}</small></mark>
    : part.kind === 'value' ? <span key={i} className="filled" dir="auto">{part.text}</span> : <span key={i}>{part.text}</span>)}</>;
}

function Inlines({ parts, plain = false }: { parts: MdInline[]; plain?: boolean }) {
  return <>{parts.map((x, i) => {
    switch (x.kind) {
      case 'strong': return <strong key={i}><PlaceholderText text={x.text} /></strong>;
      case 'em': return <em key={i}><PlaceholderText text={x.text} /></em>;
      case 'del': return <del key={i}>{x.text}</del>;
      case 'code': return <code key={i}><PlaceholderText text={x.text} /></code>;
      case 'link': {
        if (plain) return <span key={i}>{x.text}</span>;
        const path = internalPath(x.href);
        return path ? <a key={i} href={path}>{x.text}</a> : <a key={i} href={x.href} target="_blank" rel="noopener noreferrer">{x.text}</a>;
      }
      default: return <PlaceholderText key={i} text={x.text} />;
    }
  })}</>;
}

// ---- Trust ---------------------------------------------------------------------------------------

const TRUST_SECTIONS: { key: string; icon: ReactNode }[] = [
  { key: 'isolation', icon: <Layers aria-hidden="true" size={20} /> },
  { key: 'encryption', icon: <LockKeyhole aria-hidden="true" size={20} /> },
  { key: 'signIn', icon: <KeyRound aria-hidden="true" size={20} /> },
  { key: 'audit', icon: <ScrollText aria-hidden="true" size={20} /> },
  { key: 'ai', icon: <Bot aria-hidden="true" size={20} /> },
  { key: 'backups', icon: <DatabaseBackup aria-hidden="true" size={20} /> },
  { key: 'privacy', icon: <Download aria-hidden="true" size={20} /> },
];

function TrustPage() {
  return <article aria-labelledby="trust-title">
    <div className="publicIntro">
      <p className="publicEyebrow"><ShieldCheck aria-hidden="true" size={16} />{t('public.trust.eyebrow')}</p>
      <h1 id="trust-title">{t('public.trust.title')}</h1>
      <p className="publicLead">{t('public.trust.lead')}</p>
      <p className="publicNote">{t('public.trust.noCert')}</p>
    </div>
    <div className="trustGrid">
      {TRUST_SECTIONS.map((s) => <section key={s.key} className="trustCard" aria-labelledby={`trust-${s.key}`}>
        <span className="trustIcon">{s.icon}</span>
        <h2 id={`trust-${s.key}`}>{t(`public.trust.${s.key}.title` as MessageKey)}</h2>
        <p>{t(`public.trust.${s.key}.body` as MessageKey)}</p>
      </section>)}
      <section className="trustCard" aria-labelledby="trust-scanners">
        <span className="trustIcon"><ScanSearch aria-hidden="true" size={20} /></span>
        <h2 id="trust-scanners">{t('public.trust.scanners.title')}</h2>
        <p>{t('public.trust.scanners.body')}</p>
        <ul>{(['deps', 'sast', 'secrets', 'images'] as const).map((k) => <li key={k} dir="auto">{t(`public.trust.scanners.${k}`)}</li>)}</ul>
      </section>
    </div>
    <section className="trustDisclosure" aria-labelledby="trust-disclosure">
      <h2 id="trust-disclosure">{t('public.trust.disclosure.title')}</h2>
      <p>{t('public.trust.disclosure.body', { email: SECURITY_CONTACT })}</p>
      <p className="trustActions"><a className="publicButton" href={`mailto:${SECURITY_CONTACT}?subject=Security`}>{SECURITY_CONTACT}</a><a href={SECURITY_POLICY_URL} target="_blank" rel="noopener noreferrer">{t('public.trust.disclosure.policy')}</a></p>
    </section>
    <section className="trustRelated" aria-labelledby="trust-related">
      <h2 id="trust-related">{t('public.trust.related')}</h2>
      <ul>
        <li><a href="/legal/dpa">{t('public.trust.related.dpa')}</a></li>
        <li><a href="/legal/subprocessors">{t('public.trust.related.subprocessors')}</a></li>
        <li><a href={STATUS_PATH}>{t('public.trust.related.status')}</a></li>
      </ul>
    </section>
  </article>;
}

// ---- Status --------------------------------------------------------------------------------------

const STATE_ICON: Record<ComponentState, ReactNode> = {
  checking: <CircleDashed aria-hidden="true" size={18} />,
  operational: <CircleCheck aria-hidden="true" size={18} />,
  degraded: <CircleAlert aria-hidden="true" size={18} />,
  unreachable: <CircleX aria-hidden="true" size={18} />,
  not_configured: <CircleDashed aria-hidden="true" size={18} />,
};

function initialChecks(authConfigured: boolean): ComponentCheck[] {
  return [{ id: 'web', state: 'operational' }, { id: 'api', state: 'checking' }, { id: 'auth', state: authConfigured ? 'checking' : 'not_configured' }];
}

function StatusPage() {
  const targets = useMemo(() => statusTargets(apiBaseUrl, import.meta.env.VITE_ANUM_OIDC_ISSUER), []);
  const [checks, setChecks] = useState<ComponentCheck[]>(() => initialChecks(Boolean(targets.auth)));
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [offline, setOffline] = useState(() => typeof navigator !== 'undefined' && navigator.onLine === false);
  const live = useRef(true);
  const run = useCallback(async () => {
    setRunning(true);
    setOffline(navigator.onLine === false);
    setChecks(initialChecks(Boolean(targets.auth)));
    const deps = { fetch: (url: string, init: { signal: AbortSignal; cache: RequestCache; headers: Record<string, string> }) => fetch(url, { ...init, credentials: 'omit', redirect: 'error' }), now: () => performance.now() };
    const [api, auth] = await Promise.all([
      checkEndpoint('api', targets.api, apiHealthy, deps),
      targets.auth && targets.issuer ? checkEndpoint('auth', targets.auth, (body) => discoveryHealthy(body, targets.issuer!), deps) : Promise.resolve<ComponentCheck>({ id: 'auth', state: 'not_configured' }),
    ]);
    if (!live.current) return;
    setChecks([{ id: 'web', state: 'operational' }, api, auth]);
    setCheckedAt(Date.now());
    setRunning(false);
  }, [targets]);
  useEffect(() => {
    live.current = true;
    void run();
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void run(); }, STATUS_REFRESH_MS);
    const onOnline = () => void run();
    const onOffline = () => setOffline(true);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    return () => { live.current = false; window.clearInterval(timer); window.removeEventListener('online', onOnline); window.removeEventListener('offline', onOffline); };
  }, [run]);
  const overall = overallState(checks);
  const tone = overall === 'operational' ? 'ok' : overall === 'checking' ? 'neutral' : overall === 'outage' ? 'stop' : 'warn';
  return <article aria-labelledby="status-title">
    <div className="publicIntro">
      <p className="publicEyebrow"><Activity aria-hidden="true" size={16} />{t('public.status.eyebrow')}</p>
      <h1 id="status-title">{t('public.status.title')}</h1>
      <p className="publicLead">{t('public.status.lead')}</p>
    </div>
    {offline
      ? <section className="statusHeadline warn" role="alert"><WifiOff aria-hidden="true" size={22} /><div><h2>{t('public.status.offlineTitle')}</h2><p>{t('public.status.offline')}</p></div></section>
      : <section className={`statusHeadline ${tone}`} role={overall === 'outage' || overall === 'degraded' ? 'alert' : 'status'} aria-live="polite" aria-busy={overall === 'checking'}>
        {overall === 'operational' ? <CircleCheck aria-hidden="true" size={22} /> : overall === 'checking' ? <CircleDashed aria-hidden="true" size={22} /> : overall === 'outage' ? <CircleX aria-hidden="true" size={22} /> : <CircleAlert aria-hidden="true" size={22} />}
        <div>
          <h2>{t(`public.status.overall.${overall}`)}</h2>
          {overall === 'outage' && <p>{t('public.status.outageHelp')}</p>}
          {overall === 'degraded' && <p>{t('public.status.degradedHelp')}</p>}
        </div>
      </section>}
    <section className="statusPanel" aria-labelledby="status-components">
      <div className="statusPanelHead">
        <h2 id="status-components">{t('public.status.components')}</h2>
        <div className="statusActions">
          {checkedAt !== null && <span className="publicMeta">{t('public.status.lastChecked', { time: formatDateTime(checkedAt, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) })}</span>}
          <button type="button" className="publicButton" onClick={() => void run()} disabled={running}><RefreshCw aria-hidden="true" size={16} className={running ? 'spin' : undefined} />{running ? t('public.status.checkingNow') : t('public.status.checkAgain')}</button>
        </div>
      </div>
      <ul className="statusList">{checks.map((c) => <li key={c.id} className="statusRow" data-component={c.id} data-state={c.state}>
        <div className="statusName">
          <strong>{t(`public.status.component.${c.id}`)}</strong>
          <small>{t(`public.status.component.${c.id}Hint`)}</small>
        </div>
        <div className="statusValue">
          <span className={`publicBadge ${badgeTone(c.state)}`}>{STATE_ICON[c.state]}{t(`public.status.state.${c.state}`)}</span>
          <small>{detail(c)}</small>
        </div>
      </li>)}</ul>
    </section>
  </article>;
}

function badgeTone(state: ComponentState): string {
  return state === 'operational' ? 'ok' : state === 'degraded' ? 'warn' : state === 'unreachable' ? 'stop' : 'neutral';
}

function detail(c: ComponentCheck): string {
  if (c.id === 'web') return t('public.status.webDetail');
  switch (c.state) {
    case 'operational': return c.latencyMs !== undefined ? t('public.status.latency', { ms: c.latencyMs }) : '';
    case 'degraded': return c.httpStatus && c.httpStatus >= 300 ? t('public.status.httpStatus', { status: c.httpStatus }) : t('public.status.badAnswer');
    case 'unreachable': return t('public.status.unreachableDetail');
    case 'not_configured': return t('public.status.notConfiguredDetail');
    default: return '';
  }
}

