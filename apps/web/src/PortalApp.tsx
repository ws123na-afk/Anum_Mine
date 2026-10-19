// The public customer portal (docs/business-portal.md): a customer opens the link the business gave them
// and reads their offers, change orders, invoices and requests, approves or declines the exact version
// they were shown, and opens a request. No ANUM sign-in, no sidebar, no workspace headers: the link is
// read from the URL fragment, removed from the address bar at once and sent only as a bearer token to
// /api/v1/portal. This file imports nothing of the signed-in app.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, CircleDot, FileText, Inbox, Lock, Printer, ShieldAlert } from 'lucide-react';
import type {
  PortalChange, PortalChangeSummary, PortalInvoice, PortalInvoiceSummary, PortalMe, PortalOffer, PortalOfferSummary, PortalTicket, PortalTicketSummary,
} from '@anum/contracts';
import { ApiError } from './lib/errors';
import { LOCALES, LOCALE_NAMES, chooseLocale, formatDateTime, getLocale, t, type Locale } from './lib/i18n';
import { formatMoney, formatPct } from './lib/business';
import {
  REQUEST_SEVERITIES, changeStatusLabel, checkSigner, confirmText, expiryState, invoiceStatusText, invoiceStatusTone, methodLabel, offerStatusLabel, problemKind,
  resolveToken, severityLabel, storeToken, ticketStatusLabel, ticketTone, withoutFragment,
} from './lib/portal';
import * as p from './lib/portalApi';
import { printHtml } from './lib/printWindow';
import './portal.css';

type Section = 'overview' | 'offers' | 'changes' | 'invoices' | 'requests';
const SECTIONS: readonly Section[] = ['overview', 'offers', 'changes', 'invoices', 'requests'];

const sessionStore = (() => { try { return window.sessionStorage; } catch { return null; } })();
const date = (value: string | null | undefined) => (value ? formatDateTime(value.length === 10 ? `${value}T12:00:00` : value, { dateStyle: 'medium' }) : t('pt.dash'));
const dateTime = (value: string | null | undefined) => (value ? formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' }) : t('pt.dash'));
const money = (value: number | null, currency: Parameters<typeof formatMoney>[1]) => formatMoney(value, currency);

// ---- loading and failing -----------------------------------------------------------------------

type Loaded<T> = { kind: 'loading' } | { kind: 'ready'; value: T } | { kind: 'error'; error: unknown };

function useLoad<T>(fn: () => Promise<T>, deps: readonly unknown[]): { state: Loaded<T>; reload: () => void; set: (value: T) => void } {
  const [state, setState] = useState<Loaded<T>>({ kind: 'loading' }), [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ kind: 'loading' });
    fn().then((value) => { if (live) setState({ kind: 'ready', value }); }).catch((error) => { if (live) setState({ kind: 'error', error }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { state, reload: () => setTick((value) => value + 1), set: (value) => setState({ kind: 'ready', value }) };
}

const detailOf = (error: unknown) => (error instanceof ApiError ? error.detail : null);
const statusOf = (error: unknown) => (error instanceof ApiError ? error.status : null);

function Notice({ children, error, live }: { children: ReactNode; error?: boolean; live?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : live ? 'status' : undefined}><CircleDot aria-hidden="true" />{children}</p>;
}

/** The link itself is refused: say why with the API's words and what to do; there is nothing to retry. */
function LinkProblem({ message }: { message: string | null }) {
  return <section className="surface ptCard ptCenter" role="alert" aria-labelledby="pt-link-h">
    <Lock aria-hidden="true" />
    <h1 id="pt-link-h">{t('pt.link.title')}</h1>
    <p dir="auto">{message ?? t('pt.link.generic')}</p>
    <p className="muted">{t('pt.link.askNew')}</p>
  </section>;
}

function Problem({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const kind = problemKind(statusOf(error), detailOf(error));
  if (kind === 'link') return <LinkProblem message={detailOf(error)} />;
  const text = kind === 'network' ? t('pt.err.network') : kind === 'missing' ? t('pt.err.missing') : detailOf(error) ?? t('pt.err.generic');
  return <section className="surface ptCard" role="alert">
    <p className="ptCardTitle"><ShieldAlert aria-hidden="true" />{t('pt.err.title')}</p>
    <p dir="auto">{text}</p>
    {kind !== 'archived' && kind !== 'forbidden' && <div className="actions"><button type="button" onClick={onRetry}>{t('common.tryAgain')}</button></div>}
  </section>;
}

function Waiting({ what }: { what: string }) {
  return <p className="notice" role="status" aria-busy="true"><CircleDot aria-hidden="true" />{t('pt.loading', { what })}</p>;
}

function Friendly({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="emptyState ptEmpty"><Inbox aria-hidden="true" /><h3>{title}</h3><p>{text}</p>{action && <div className="ptCenterRow">{action}</div>}</div>;
}

function useFocus<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  return ref;
}

// ---- the page ----------------------------------------------------------------------------------

export function PortalApp() {
  const [link] = useState<{ token: string | null }>(() => {
    const found = resolveToken(window.location.hash, sessionStore);
    if (found.fromFragment) {
      // The credential leaves the address bar before anything else happens (and so history and screenshots).
      try { window.history.replaceState(null, '', withoutFragment(window.location)); } catch { /* the address keeps the fragment */ }
    }
    if (found.token) { storeToken(sessionStore, found.token); p.setPortalToken(found.token); }
    return { token: found.token };
  });
  const [section, setSection] = useState<Section>('overview');
  const [revoked, setRevoked] = useState<string | null>(null);
  const me = useLoad(() => p.getMe(), [link.token]);
  const mainRef = useRef<HTMLElement>(null);
  const go = useCallback((next: Section) => { setSection(next); window.setTimeout(() => mainRef.current?.focus(), 0); }, []);
  useEffect(() => { document.title = t('pt.docTitle'); }, [getLocale()]);
  const onLinkLost = useCallback((message: string | null) => { storeToken(sessionStore, null); setRevoked(message ?? ''); }, []);

  let body: ReactNode;
  if (!link.token) body = <LinkProblem message={t('pt.link.missing')} />;
  else if (revoked !== null) body = <LinkProblem message={revoked || null} />;
  else if (me.state.kind === 'loading') body = <Waiting what={t('pt.what.page')} />;
  else if (me.state.kind === 'error') {
    if (problemKind(statusOf(me.state.error), detailOf(me.state.error)) === 'link') storeToken(sessionStore, null);
    body = <Problem error={me.state.error} onRetry={me.reload} />;
  } else {
    const info = me.state.value;
    body = <>
      {!info.can_decide && <Notice live>{t('pt.readOnly')}</Notice>}
      <ExpiryNotice expiresAt={info.expires_at} />
      <nav className="ptTabs" aria-label={t('pt.sections')}>{SECTIONS.map((item) => <button key={item} type="button" aria-current={section === item ? 'page' : undefined} onClick={() => go(item)}>
        {t(`pt.tab.${item}`)}{badgeCount(item, info) > 0 && <span className="ptCount" aria-label={t('pt.waitingCount', { count: badgeCount(item, info) })}>{badgeCount(item, info)}</span>}</button>)}</nav>
      <main id="portal-main" ref={mainRef} tabIndex={-1} aria-label={t(`pt.tab.${section}`)}>
        {section === 'overview' && <Overview me={info} onGo={go} />}
        {section === 'offers' && <OffersSection me={info} onChanged={me.reload} onLinkLost={onLinkLost} />}
        {section === 'changes' && <ChangesSection me={info} onChanged={me.reload} onLinkLost={onLinkLost} />}
        {section === 'invoices' && <InvoicesSection onLinkLost={onLinkLost} />}
        {section === 'requests' && <RequestsSection me={info} onChanged={me.reload} onLinkLost={onLinkLost} />}
      </main>
    </>;
  }
  const info = me.state.kind === 'ready' ? me.state.value : null;
  return <div className="ptShell">
    <a className="skipLink" href="#portal-main" onClick={(event) => { event.preventDefault(); mainRef.current?.focus(); }}>{t('shell.skipToContent')}</a>
    <header className="ptHeader">
      <div className="ptBrand">
        <p className="ptBusiness" dir="auto">{info?.business_name || t('pt.brandFallback')}</p>
        <h1>{t('pt.title')}</h1>
        {info && <p className="muted" dir="auto">{t('pt.forWho', { contact: info.contact_name, account: info.account_name })}</p>}
      </div>
      <LanguageSwitch />
    </header>
    {body}
    <footer className="ptFooter muted">{t('pt.footer')}</footer>
  </div>;
}

function badgeCount(section: Section, me: PortalMe): number {
  if (section === 'offers') return me.counts.offers_waiting;
  if (section === 'changes') return me.counts.change_orders_waiting;
  if (section === 'invoices') return me.counts.invoices_open;
  if (section === 'requests') return me.counts.tickets_open;
  return 0;
}

function LanguageSwitch() {
  return <div className="ptLang" role="group" aria-label={t('pt.language')}>
    {LOCALES.map((code: Locale) => <button key={code} type="button" className="secondary" lang={code} aria-pressed={getLocale() === code} onClick={() => chooseLocale(code)}>{LOCALE_NAMES[code]}</button>)}
  </div>;
}

function ExpiryNotice({ expiresAt }: { expiresAt: string }) {
  const state = expiryState(expiresAt);
  if (state.kind === 'ok') return null;
  return <Notice live>{state.kind === 'expired' ? t('pt.expiry.expired') : t('pt.expiry.soon', { count: state.days, date: date(expiresAt) })}</Notice>;
}

function Overview({ me, onGo }: { me: PortalMe; onGo: (section: Section) => void }) {
  const rows: { section: Section; count: number; text: string; idle: string }[] = [
    { section: 'offers', count: me.counts.offers_waiting, text: t('pt.ov.offers', { count: me.counts.offers_waiting }), idle: t('pt.ov.offersIdle') },
    { section: 'changes', count: me.counts.change_orders_waiting, text: t('pt.ov.changes', { count: me.counts.change_orders_waiting }), idle: t('pt.ov.changesIdle') },
    { section: 'invoices', count: me.counts.invoices_open, text: t('pt.ov.invoices', { count: me.counts.invoices_open }), idle: t('pt.ov.invoicesIdle') },
    { section: 'requests', count: me.counts.tickets_open, text: t('pt.ov.requests', { count: me.counts.tickets_open }), idle: t('pt.ov.requestsIdle') },
  ];
  const nothing = rows.every((row) => row.count === 0);
  return <section aria-labelledby="pt-ov-h" className="ptStack">
    <h2 id="pt-ov-h">{t('pt.tab.overview')}</h2>
    <p className="muted">{t('pt.ov.hello', { name: me.contact_name })}</p>
    {nothing && <Notice live>{t('pt.ov.nothing')}</Notice>}
    {me.counts.invoices_overdue > 0 && <Notice live>{t('pt.ov.overdue', { count: me.counts.invoices_overdue })}</Notice>}
    <ul className="ptTiles">{rows.map((row) => <li key={row.section} className="surface ptTile">
      <strong>{row.count}</strong><span>{row.count > 0 ? row.text : row.idle}</span>
      <button type="button" className={row.count > 0 ? undefined : 'secondary'} onClick={() => onGo(row.section)}>{t(`pt.ov.open.${row.section}`)}</button></li>)}</ul>
  </section>;
}

// ---- list + detail scaffolding -------------------------------------------------------------------

function Back({ onClick, label }: { onClick: () => void; label: string }) {
  return <button type="button" className="secondary ptBack" onClick={onClick}><ArrowLeft aria-hidden="true" />{label}</button>;
}

function Lines({ rows, currency, qtyLabel }: { rows: { description: string; quantity: number; unit_price: number; amount: number; tag?: string }[]; currency: Parameters<typeof formatMoney>[1]; qtyLabel?: string }) {
  return <div className="pane dataTableWrap"><table className="dataTable ptTable">
    <caption className="visuallyHidden">{t('pt.lines')}</caption>
    <thead><tr><th scope="col">{t('pt.col.description')}</th><th scope="col" className="num">{qtyLabel ?? t('pt.col.qty')}</th><th scope="col" className="num ptHideSm">{t('pt.col.unit')}</th><th scope="col" className="num">{t('pt.col.amount')}</th></tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}><th scope="row"><span dir="auto">{row.description}</span>{row.tag && <small>{row.tag}</small>}</th><td className="num">{row.quantity}</td>
      <td className="num ptHideSm">{money(row.unit_price, currency)}</td><td className="num">{money(row.amount, currency)}</td></tr>)}</tbody></table></div>;
}

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return <section className="ptBlock"><h3>{title}</h3><ul>{items.map((item, index) => <li key={index} dir="auto">{item}</li>)}</ul></section>;
}

function Text({ title, text }: { title: string; text: string }) {
  if (!text) return null;
  return <section className="ptBlock"><h3>{title}</h3><p dir="auto" className="ptPre">{text}</p></section>;
}

function Badge({ tone, children }: { tone: 'neutral' | 'success' | 'warning' | 'dangerTone'; children: ReactNode }) {
  return <span className={`statusBadge ${tone}`}>{children}</span>;
}

// ---- the decision (offers and change orders) -------------------------------------------------

interface DecisionProps {
  kind: 'offer' | 'change'; title: string; total: number | null; currency: Parameters<typeof formatMoney>[1];
  onDecide: (decision: 'approved' | 'declined', name: string, note: string) => Promise<void>;
}

function DecisionPanel({ kind, title, total, currency, onDecide }: DecisionProps) {
  const [name, setName] = useState(''), [note, setNote] = useState(''), [step, setStep] = useState<'approved' | 'declined' | null>(null);
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const nameId = useId(), noteId = useId();
  const confirmRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (step) confirmRef.current?.focus(); }, [step]);
  function review(decision: 'approved' | 'declined') {
    const signer = checkSigner(name);
    if (!signer.ok) { setProblem(signer.error); return; }
    setProblem(null); setStep(decision);
  }
  async function confirm(event: FormEvent) {
    event.preventDefault();
    const signer = checkSigner(name);
    if (!signer.ok || !step) return;
    setBusy(true); setProblem(null);
    try { await onDecide(step, signer.name, note.trim()); }
    catch (error) {
      const status = statusOf(error);
      setProblem(`${detailOf(error) ?? t('pt.err.generic')}${status === 409 ? ` ${t('pt.decide.reloaded')}` : ''}`);
      setStep(null); setBusy(false);
    }
  }
  if (step) {
    const signer = checkSigner(name);
    return <form className="surface ptDecision" onSubmit={(event) => void confirm(event)} aria-labelledby="pt-confirm-h">
      <h3 id="pt-confirm-h" ref={confirmRef} tabIndex={-1}>{step === 'approved' ? t('pt.decide.confirmApprove') : t('pt.decide.confirmDecline')}</h3>
      <p dir="auto" className="ptConfirmText">{confirmText(kind, step, title, total, currency)}</p>
      <p className="muted">{t('pt.decide.signedAs', { name: signer.ok ? signer.name : name })}</p>
      {note.trim() && <p dir="auto" className="muted">{t('pt.decide.yourNote', { note: note.trim() })}</p>}
      <p className="muted">{t('pt.decide.final')}</p>
      <div className="actions"><button type="submit" className={step === 'declined' ? 'danger' : undefined} disabled={busy}>{busy ? t('dialog.working') : step === 'approved' ? t('pt.decide.approveNow') : t('pt.decide.declineNow')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setStep(null)}>{t('pt.decide.back')}</button></div>
    </form>;
  }
  return <section className="surface ptDecision" aria-labelledby={`${nameId}-h`}>
    <h3 id={`${nameId}-h`}>{t('pt.decide.title')}</h3>
    <p className="muted">{t('pt.decide.help')}</p>
    <label className="field" htmlFor={nameId}><span>{t('pt.decide.name')}</span><input id={nameId} autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} dir="auto" aria-describedby={problem ? `${nameId}-e` : undefined} /></label>
    <label className="field" htmlFor={noteId}><span>{t('pt.decide.note')}</span><textarea id={noteId} rows={2} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} dir="auto" /></label>
    {problem && <p id={`${nameId}-e`} className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
    <div className="actions"><button type="button" onClick={() => review('approved')}>{t('pt.decide.approve')}</button><button type="button" className="secondary" onClick={() => review('declined')}>{t('pt.decide.decline')}</button></div>
  </section>;
}

function DecidedNote({ decision, what }: { decision: { decision: 'approved' | 'declined'; signer_name: string; note: string; decided_at: string }; what: string }) {
  return <section className="surface ptDecision" role="status">
    <h3>{decision.decision === 'approved' ? t('pt.decided.approvedTitle') : t('pt.decided.declinedTitle')}</h3>
    <p dir="auto">{t(decision.decision === 'approved' ? 'pt.decided.approved' : 'pt.decided.declined', { what, name: decision.signer_name, when: dateTime(decision.decided_at) })}</p>
    {decision.note && <p dir="auto" className="muted">{t('pt.decide.yourNote', { note: decision.note })}</p>}
    <p className="muted">{t('pt.decided.change')}</p>
  </section>;
}

// ---- offers ------------------------------------------------------------------------------------

interface SectionProps { me?: PortalMe; onChanged?: () => void; onLinkLost: (message: string | null) => void }

function useLinkWatch(state: Loaded<unknown>, onLinkLost: (message: string | null) => void) {
  useEffect(() => {
    if (state.kind === 'error' && problemKind(statusOf(state.error), detailOf(state.error)) === 'link') onLinkLost(detailOf(state.error));
  }, [state, onLinkLost]);
}

function OffersSection({ me, onChanged, onLinkLost }: SectionProps & { me: PortalMe; onChanged: () => void }) {
  const list = useLoad(() => p.listOffers(), []);
  const [openId, setOpenId] = useState<string | null>(null);
  useLinkWatch(list.state, onLinkLost);
  if (openId) return <OfferDetail id={openId} me={me} onBack={() => { setOpenId(null); list.reload(); onChanged(); }} onLinkLost={onLinkLost} />;
  return <section aria-labelledby="pt-offers-h" className="ptStack">
    <h2 id="pt-offers-h">{t('pt.tab.offers')}</h2>
    {list.state.kind === 'loading' && <Waiting what={t('pt.what.offers')} />}
    {list.state.kind === 'error' && <Problem error={list.state.error} onRetry={list.reload} />}
    {list.state.kind === 'ready' && (list.state.value.length === 0
      ? <Friendly title={t('pt.offers.emptyTitle')} text={t('pt.offers.emptyText')} />
      : <ul className="ptList">{list.state.value.map((item: PortalOfferSummary) => <li key={item.id} className="surface ptRow">
        <div><button type="button" className="linkButton" onClick={() => setOpenId(item.id)}><span dir="auto">{item.title}</span></button>
          <small className="muted">{t('pt.offers.meta', { version: item.version, date: date(item.sent_at) })}{item.valid_until ? ` / ${t('pt.offers.validUntil', { date: date(item.valid_until) })}` : ''}</small></div>
        <div className="ptRowEnd"><strong>{money(item.total, item.currency)}</strong>
          <Badge tone={item.decision ? (item.decision.decision === 'approved' ? 'success' : 'dangerTone') : item.expired ? 'dangerTone' : item.status === 'accepted' ? 'success' : 'warning'}>
            {item.decision ? t(item.decision.decision === 'approved' ? 'pt.offers.youApproved' : 'pt.offers.youDeclined') : item.expired ? t('pt.offers.expired') : offerStatusLabel(item.status)}</Badge></div>
      </li>)}</ul>)}
  </section>;
}

function OfferDetail({ id, me, onBack, onLinkLost }: { id: string; me: PortalMe; onBack: () => void; onLinkLost: (message: string | null) => void }) {
  const detail = useLoad(() => p.getOffer(id), [id]);
  const heading = useFocus<HTMLHeadingElement>();
  useLinkWatch(detail.state, onLinkLost);
  if (detail.state.kind === 'loading') return <section className="ptStack"><Back onClick={onBack} label={t('pt.offers.back')} /><Waiting what={t('pt.what.offer')} /></section>;
  if (detail.state.kind === 'error') return <section className="ptStack"><Back onClick={onBack} label={t('pt.offers.back')} /><Problem error={detail.state.error} onRetry={detail.reload} /></section>;
  const offer: PortalOffer = detail.state.value;
  async function decide(decision: 'approved' | 'declined', name: string, note: string) {
    try { detail.set(await p.decideOffer(offer.id, { decision, content_hash: offer.content_hash, signer_name: name, note })); }
    catch (error) { if (statusOf(error) === 409) detail.reload(); throw error; }
  }
  return <article className="ptStack" aria-labelledby="pt-offer-h">
    <Back onClick={onBack} label={t('pt.offers.back')} />
    <div className="ptTitleRow"><h2 id="pt-offer-h" ref={heading} tabIndex={-1} dir="auto">{offer.title}</h2>
      <Badge tone={offer.status === 'accepted' ? 'success' : offer.expired ? 'dangerTone' : 'warning'}>{offer.expired ? t('pt.offers.expired') : offerStatusLabel(offer.status)}</Badge></div>
    <p className="muted">{t('pt.offers.meta', { version: offer.version, date: date(offer.sent_at) })}{offer.valid_until ? ` / ${t('pt.offers.validUntil', { date: date(offer.valid_until) })}` : ''}</p>
    <Text title={t('pt.offers.summary')} text={offer.summary} />
    <Text title={t('pt.offers.scope')} text={offer.scope} />
    <Bullets title={t('pt.offers.deliverables')} items={offer.deliverables} />
    <Bullets title={t('pt.offers.exclusions')} items={offer.exclusions} />
    <Bullets title={t('pt.offers.assumptions')} items={offer.assumptions} />
    <Bullets title={t('pt.offers.criteria')} items={offer.acceptance_criteria} />
    {offer.line_items.length > 0 && <section className="ptBlock"><h3>{t('pt.lines')}</h3><Lines rows={offer.line_items} currency={offer.currency} /></section>}
    {offer.milestones.length > 0 && <section className="ptBlock"><h3>{t('pt.offers.milestones')}</h3>
      <div className="pane dataTableWrap"><table className="dataTable ptTable"><caption className="visuallyHidden">{t('pt.offers.milestones')}</caption>
        <thead><tr><th scope="col">{t('pt.col.description')}</th><th scope="col" className="num">{t('pt.col.amount')}</th><th scope="col" className="ptHideSm">{t('pt.offers.due')}</th></tr></thead>
        <tbody>{offer.milestones.map((item, index) => <tr key={index}><th scope="row"><span dir="auto">{item.title}</span>{item.acceptance && <small dir="auto">{item.acceptance}</small>}</th>
          <td className="num">{money(item.amount, offer.currency)}</td><td className="ptHideSm">{item.due_in_days === null ? t('pt.dash') : t('pt.offers.dueDays', { count: item.due_in_days })}</td></tr>)}</tbody></table></div></section>}
    <dl className="ptTotals" aria-label={t('pt.totals')}>
      <div><dt>{t('pt.subtotal')}</dt><dd>{money(offer.subtotal, offer.currency)}</dd></div>
      <div><dt>{t('pt.tax', { rate: formatPct(offer.tax_rate_pct) })}</dt><dd>{money(offer.tax, offer.currency)}</dd></div>
      <div><dt>{t('pt.total')}</dt><dd><strong>{money(offer.total, offer.currency)}</strong></dd></div>
    </dl>
    <Text title={t('pt.offers.taxNote')} text={offer.tax_note} />
    <Text title={t('pt.offers.paymentTerms')} text={offer.payment_terms} />
    {offer.decision ? <DecidedNote decision={offer.decision} what={t('pt.what.thisOffer')} />
      : offer.status === 'accepted' ? <Notice live>{t('pt.offers.accepted')}</Notice>
      : offer.expired ? <Notice live>{t('pt.offers.expiredText', { date: date(offer.valid_until) })}</Notice>
      : !me.can_decide ? <Notice live>{t('pt.readOnlyDetail')}</Notice>
      : !offer.can_decide ? <Notice live>{t('pt.offers.cannotDecide')}</Notice>
      : <DecisionPanel kind="offer" title={offer.title} total={offer.total} currency={offer.currency} onDecide={decide} />}
  </article>;
}

// ---- change orders -----------------------------------------------------------------------------

function ChangesSection({ me, onChanged, onLinkLost }: SectionProps & { me: PortalMe; onChanged: () => void }) {
  const list = useLoad(() => p.listChanges(), []);
  const [openId, setOpenId] = useState<string | null>(null);
  useLinkWatch(list.state, onLinkLost);
  if (openId) return <ChangeDetail id={openId} me={me} onBack={() => { setOpenId(null); list.reload(); onChanged(); }} onLinkLost={onLinkLost} />;
  return <section aria-labelledby="pt-changes-h" className="ptStack">
    <h2 id="pt-changes-h">{t('pt.tab.changes')}</h2>
    <p className="muted">{t('pt.changes.help')}</p>
    {list.state.kind === 'loading' && <Waiting what={t('pt.what.changes')} />}
    {list.state.kind === 'error' && <Problem error={list.state.error} onRetry={list.reload} />}
    {list.state.kind === 'ready' && (list.state.value.length === 0
      ? <Friendly title={t('pt.changes.emptyTitle')} text={t('pt.changes.emptyText')} />
      : <ul className="ptList">{list.state.value.map((item: PortalChangeSummary) => <li key={item.id} className="surface ptRow">
        <div><button type="button" className="linkButton" onClick={() => setOpenId(item.id)}><span dir="auto">{t('pt.changes.heading', { number: item.number, title: item.title })}</span></button>
          <small className="muted" dir="auto">{item.project_name}</small></div>
        <div className="ptRowEnd"><strong>{signed(item.total_delta, item.currency)}</strong>
          <Badge tone={item.decision ? (item.decision.decision === 'approved' ? 'success' : 'dangerTone') : item.stale ? 'dangerTone' : item.status === 'approved' ? 'success' : 'warning'}>
            {item.decision ? t(item.decision.decision === 'approved' ? 'pt.offers.youApproved' : 'pt.offers.youDeclined') : item.stale ? t('pt.changes.stale') : changeStatusLabel(item.status)}</Badge></div>
      </li>)}</ul>)}
  </section>;
}

function signed(value: number | null, currency: Parameters<typeof formatMoney>[1]): string {
  if (value === null) return t('pt.dash');
  const text = money(Math.abs(value), currency);
  return value > 0 ? `+${text}` : value < 0 ? `-${text}` : text;
}

function ChangeDetail({ id, me, onBack, onLinkLost }: { id: string; me: PortalMe; onBack: () => void; onLinkLost: (message: string | null) => void }) {
  const detail = useLoad(() => p.getChange(id), [id]);
  const heading = useFocus<HTMLHeadingElement>();
  useLinkWatch(detail.state, onLinkLost);
  if (detail.state.kind === 'loading') return <section className="ptStack"><Back onClick={onBack} label={t('pt.changes.back')} /><Waiting what={t('pt.what.change')} /></section>;
  if (detail.state.kind === 'error') return <section className="ptStack"><Back onClick={onBack} label={t('pt.changes.back')} /><Problem error={detail.state.error} onRetry={detail.reload} /></section>;
  const change: PortalChange = detail.state.value;
  async function decide(decision: 'approved' | 'declined', name: string, note: string) {
    try { detail.set(await p.decideChange(change.id, { decision, content_hash: change.content_hash, signer_name: name, note })); }
    catch (error) { if (statusOf(error) === 409) detail.reload(); throw error; }
  }
  const title = t('pt.changes.heading', { number: change.number, title: change.title });
  return <article className="ptStack" aria-labelledby="pt-change-h">
    <Back onClick={onBack} label={t('pt.changes.back')} />
    <div className="ptTitleRow"><h2 id="pt-change-h" ref={heading} tabIndex={-1} dir="auto">{title}</h2>
      <Badge tone={change.stale ? 'dangerTone' : change.status === 'approved' ? 'success' : 'warning'}>{change.stale ? t('pt.changes.stale') : changeStatusLabel(change.status)}</Badge></div>
    <p className="muted" dir="auto">{change.project_name}</p>
    <Text title={t('pt.changes.request')} text={change.request} />
    {change.lines.length > 0 && <section className="ptBlock"><h3>{t('pt.changes.lines')}</h3>
      <Lines rows={change.lines.map((line) => ({ description: line.description, quantity: line.quantity, unit_price: line.unit_price, amount: line.amount, tag: t(line.action === 'add' ? 'pt.changes.added' : 'pt.changes.removed') }))} currency={change.currency} /></section>}
    <dl className="ptTotals" aria-label={t('pt.totals')}>
      <div><dt>{t('pt.changes.subtotalDelta')}</dt><dd>{signed(change.subtotal_delta, change.currency)}</dd></div>
      <div><dt>{t('pt.changes.taxDelta')}</dt><dd>{signed(change.tax_delta, change.currency)}</dd></div>
      <div><dt>{t('pt.changes.totalDelta')}</dt><dd><strong>{signed(change.total_delta, change.currency)}</strong></dd></div>
      <div><dt>{t('pt.changes.timeline')}</dt><dd>{change.timeline_days === 0 ? t('pt.changes.noTimeline') : t('pt.changes.timelineDays', { count: Math.abs(change.timeline_days), sign: change.timeline_days > 0 ? '+' : '-' })}</dd></div>
    </dl>
    {change.decision ? <DecidedNote decision={change.decision} what={t('pt.what.thisChange')} />
      : change.status === 'approved' ? <Notice live>{t('pt.changes.approved')}</Notice>
      : change.stale ? <Notice live>{t('pt.changes.staleText')}</Notice>
      : !me.can_decide ? <Notice live>{t('pt.readOnlyDetail')}</Notice>
      : !change.can_decide ? <Notice live>{t('pt.changes.cannotDecide')}</Notice>
      : <DecisionPanel kind="change" title={title} total={change.total_delta} currency={change.currency} onDecide={decide} />}
  </article>;
}

// ---- invoices ----------------------------------------------------------------------------------

function InvoicesSection({ onLinkLost }: { onLinkLost: (message: string | null) => void }) {
  const list = useLoad(() => p.listInvoices(), []);
  const [openId, setOpenId] = useState<string | null>(null);
  useLinkWatch(list.state, onLinkLost);
  if (openId) return <InvoiceDetail id={openId} onBack={() => setOpenId(null)} onLinkLost={onLinkLost} />;
  return <section aria-labelledby="pt-inv-h" className="ptStack">
    <h2 id="pt-inv-h">{t('pt.tab.invoices')}</h2>
    <p className="muted">{t('pt.invoices.help')}</p>
    {list.state.kind === 'loading' && <Waiting what={t('pt.what.invoices')} />}
    {list.state.kind === 'error' && <Problem error={list.state.error} onRetry={list.reload} />}
    {list.state.kind === 'ready' && (list.state.value.length === 0
      ? <Friendly title={t('pt.invoices.emptyTitle')} text={t('pt.invoices.emptyText')} />
      : <ul className="ptList">{list.state.value.map((item: PortalInvoiceSummary) => <li key={item.id} className="surface ptRow">
        <div><button type="button" className="linkButton" onClick={() => setOpenId(item.id)}><span dir="auto">{item.number}</span></button>
          <small className="muted">{item.status === 'void' ? t('pt.invoices.issued', { date: date(item.issue_date) }) : t('pt.invoices.dueOn', { date: date(item.due_date) })}{item.customer_reference ? ` / ${item.customer_reference}` : ''}</small></div>
        <div className="ptRowEnd"><span><strong>{money(item.status === 'void' ? item.total : item.balance, item.currency)}</strong>{item.status !== 'void' && item.payment_state !== 'paid' && <small className="muted"> {t('pt.invoices.toPay')}</small>}</span>
          <Badge tone={invoiceStatusTone(item)}>{invoiceStatusText(item)}{item.overdue && item.status !== 'void' ? `, ${t('pt.invoices.days', { count: item.days_overdue })}` : ''}</Badge></div>
      </li>)}</ul>)}
  </section>;
}

function InvoiceDetail({ id, onBack, onLinkLost }: { id: string; onBack: () => void; onLinkLost: (message: string | null) => void }) {
  const detail = useLoad(() => p.getInvoice(id), [id]);
  const heading = useFocus<HTMLHeadingElement>();
  const [language, setLanguage] = useState<'en' | 'ar'>(getLocale() === 'ar' ? 'ar' : 'en'), [note, setNote] = useState<string>(''), [failed, setFailed] = useState<string | null>(null);
  const langId = useId();
  useLinkWatch(detail.state, onLinkLost);
  if (detail.state.kind === 'loading') return <section className="ptStack"><Back onClick={onBack} label={t('pt.invoices.back')} /><Waiting what={t('pt.what.invoice')} /></section>;
  if (detail.state.kind === 'error') return <section className="ptStack"><Back onClick={onBack} label={t('pt.invoices.back')} /><Problem error={detail.state.error} onRetry={detail.reload} /></section>;
  const invoice: PortalInvoice = detail.state.value;
  async function print() {
    setNote(t('pt.print.opening')); setFailed(null);
    try { await printHtml(() => p.invoicePrintable(invoice.id, language)); setNote(t('pt.print.opened')); }
    catch (error) { setNote(''); setFailed(error instanceof ApiError ? detailOf(error) ?? t('pt.err.generic') : error instanceof Error ? error.message : t('pt.err.generic')); }
  }
  const c = invoice.currency;
  return <article className="ptStack" aria-labelledby="pt-invd-h">
    <Back onClick={onBack} label={t('pt.invoices.back')} />
    <div className="ptTitleRow"><h2 id="pt-invd-h" ref={heading} tabIndex={-1} dir="auto">{t('pt.invoices.heading', { number: invoice.number })}</h2>
      <Badge tone={invoiceStatusTone(invoice)}>{invoiceStatusText(invoice)}</Badge></div>
    {invoice.status === 'void' && <Notice live>{t('pt.invoices.voided', { reason: invoice.void_reason })}</Notice>}
    {invoice.overdue && invoice.status !== 'void' && <Notice live>{t('pt.invoices.overdueText', { count: invoice.days_overdue, date: date(invoice.due_date) })}</Notice>}
    <dl className="ptFacts">
      <div><dt>{t('pt.invoices.issue')}</dt><dd>{date(invoice.issue_date)}</dd></div>
      <div><dt>{t('pt.invoices.due')}</dt><dd>{date(invoice.due_date)}</dd></div>
      <div><dt>{t('pt.invoices.reference')}</dt><dd dir="auto">{invoice.customer_reference || t('pt.dash')}</dd></div>
    </dl>
    <section className="ptBlock"><h3>{t('pt.lines')}</h3>
      <div className="pane dataTableWrap"><table className="dataTable ptTable"><caption className="visuallyHidden">{t('pt.lines')}</caption>
        <thead><tr><th scope="col">{t('pt.col.description')}</th><th scope="col" className="num ptHideSm">{t('pt.col.qty')}</th><th scope="col" className="num ptHideSm">{t('pt.col.unit')}</th><th scope="col" className="num ptHideSm">{t('pt.col.taxPct')}</th><th scope="col" className="num">{t('pt.col.total')}</th></tr></thead>
        <tbody>{invoice.lines.map((line, index) => <tr key={index}><th scope="row"><span dir="auto">{line.description}</span></th><td className="num ptHideSm">{line.quantity}</td>
          <td className="num ptHideSm">{money(line.unit_price, c)}</td><td className="num ptHideSm">{formatPct(line.tax_rate_pct)}</td><td className="num">{money(line.total, c)}</td></tr>)}</tbody></table></div>
      <dl className="ptTotals" aria-label={t('pt.totals')}>
        <div><dt>{t('pt.subtotal')}</dt><dd>{money(invoice.subtotal, c)}</dd></div>
        <div><dt>{t('pt.taxPlain')}</dt><dd>{money(invoice.tax, c)}</dd></div>
        <div><dt>{t('pt.total')}</dt><dd><strong>{money(invoice.total, c)}</strong></dd></div>
        <div><dt>{t('pt.invoices.paid')}</dt><dd>{money(invoice.amount_paid, c)}</dd></div>
        <div><dt>{t('pt.invoices.balance')}</dt><dd><strong>{money(invoice.balance, c)}</strong></dd></div>
      </dl>
    </section>
    {invoice.payments.length > 0 && <section className="ptBlock"><h3>{t('pt.invoices.payments')}</h3>
      <ul className="ptPlain">{invoice.payments.map((payment, index) => <li key={index}><strong>{money(payment.amount, c)}</strong> {date(payment.paid_on)} / {methodLabel(payment.method)}{payment.reference ? ` / ${payment.reference}` : ''}</li>)}</ul></section>}
    {invoice.status !== 'void' && invoice.payment_state !== 'paid' && <section className="surface ptDecision" aria-labelledby="pt-how-h">
      <h3 id="pt-how-h">{t('pt.invoices.howToPay')}</h3>
      {invoice.seller.bank_details ? <p dir="auto" className="ptPre">{invoice.seller.bank_details}</p> : <p className="muted">{t('pt.invoices.noBank')}</p>}
      <p className="muted">{t('pt.invoices.noOnline')}</p>
      {invoice.payment_terms && <p dir="auto" className="ptPre">{invoice.payment_terms}</p>}
    </section>}
    <div className="ptBlock ptTwo">
      <section><h3>{t('pt.invoices.billTo')}</h3><p dir="auto" className="ptPre"><strong>{invoice.bill_to_name || t('pt.dash')}</strong>{invoice.bill_to_address ? `\n${invoice.bill_to_address}` : ''}{invoice.bill_to_tax_number ? `\n${invoice.bill_to_tax_number}` : ''}</p></section>
      <section><h3>{t('pt.invoices.from')}</h3><p dir="auto" className="ptPre"><strong>{invoice.seller.legal_name || t('pt.dash')}</strong>{invoice.seller.address ? `\n${invoice.seller.address}` : ''}
        {invoice.seller.tax_number ? `\n${invoice.seller.tax_label}: ${invoice.seller.tax_number}` : ''}{invoice.seller.email ? `\n${invoice.seller.email}` : ''}{invoice.seller.phone ? `\n${invoice.seller.phone}` : ''}</p></section>
    </div>
    {invoice.notes && <Text title={t('pt.invoices.notes')} text={invoice.notes} />}
    <section className="ptBlock" aria-labelledby="pt-print-h">
      <h3 id="pt-print-h">{t('pt.print.title')}</h3>
      <div className="ptPrintRow">
        <label className="field" htmlFor={langId}><span>{t('pt.print.language')}</span>
          <select id={langId} value={language} onChange={(event) => setLanguage(event.target.value as 'en' | 'ar')}><option value="en">{t('pt.print.en')}</option><option value="ar">{t('pt.print.ar')}</option></select></label>
        <button type="button" className="secondary" onClick={() => void print()}><Printer aria-hidden="true" />{t('pt.print.button')}</button>
      </div>
      <p className="muted" role="status">{note}</p>
      {failed && <Notice error>{failed}</Notice>}
    </section>
  </article>;
}

// ---- requests ----------------------------------------------------------------------------------

function RequestsSection({ me, onChanged, onLinkLost }: SectionProps & { me: PortalMe; onChanged: () => void }) {
  const list = useLoad(() => p.listTickets(), []);
  const [openId, setOpenId] = useState<string | null>(null), [creating, setCreating] = useState(false), [justOpened, setJustOpened] = useState<string | null>(null);
  useLinkWatch(list.state, onLinkLost);
  if (creating) return <NewRequest me={me} onBack={() => setCreating(false)} onLinkLost={onLinkLost} onCreated={(ticket) => { setCreating(false); setJustOpened(ticket.reference); setOpenId(ticket.id); list.reload(); onChanged(); }} />;
  if (openId) return <RequestDetail id={openId} fresh={justOpened} onBack={() => { setOpenId(null); setJustOpened(null); list.reload(); }} onLinkLost={onLinkLost} />;
  const newButton = <button type="button" onClick={() => setCreating(true)}>{t('pt.req.new')}</button>;
  return <section aria-labelledby="pt-req-h" className="ptStack">
    <div className="ptTitleRow"><h2 id="pt-req-h">{t('pt.tab.requests')}</h2>{list.state.kind === 'ready' && list.state.value.length > 0 && newButton}</div>
    <p className="muted">{t('pt.req.help')}</p>
    {list.state.kind === 'loading' && <Waiting what={t('pt.what.requests')} />}
    {list.state.kind === 'error' && <Problem error={list.state.error} onRetry={list.reload} />}
    {list.state.kind === 'ready' && (list.state.value.length === 0
      ? <Friendly title={t('pt.req.emptyTitle')} text={t('pt.req.emptyText')} action={newButton} />
      : <ul className="ptList">{list.state.value.map((item: PortalTicketSummary) => <li key={item.id} className="surface ptRow">
        <div><button type="button" className="linkButton" onClick={() => setOpenId(item.id)}><span dir="auto">{item.title}</span></button>
          <small className="muted">{item.reference} / {t('pt.req.updated', { date: dateTime(item.updated_at) })}{item.project_name ? ` / ${item.project_name}` : ''}</small></div>
        <div className="ptRowEnd"><span className="muted">{severityLabel(item.severity)}</span><Badge tone={ticketTone(item.status)}>{ticketStatusLabel(item.status)}</Badge></div>
      </li>)}</ul>)}
  </section>;
}

function NewRequest({ me, onBack, onCreated, onLinkLost }: { me: PortalMe; onBack: () => void; onCreated: (ticket: PortalTicket) => void; onLinkLost: (message: string | null) => void }) {
  const [title, setTitle] = useState(''), [details, setDetails] = useState(''), [severity, setSeverity] = useState<(typeof REQUEST_SEVERITIES)[number]>('normal'), [projectId, setProjectId] = useState('');
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const heading = useFocus<HTMLHeadingElement>();
  const ids = [useId(), useId(), useId(), useId()];
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (title.trim().length < 3) { setProblem(t('pt.req.titleInvalid')); return; }
    if (details.trim().length < 3) { setProblem(t('pt.req.detailsInvalid')); return; }
    setBusy(true); setProblem(null);
    try { onCreated(await p.createTicket({ title: title.trim(), description: details.trim(), severity, project_id: projectId || null })); }
    catch (error) {
      if (problemKind(statusOf(error), detailOf(error)) === 'link') onLinkLost(detailOf(error));
      setProblem(detailOf(error) ?? t('pt.err.generic')); setBusy(false);
    }
  }
  return <section className="ptStack" aria-labelledby="pt-newreq-h">
    <Back onClick={onBack} label={t('pt.req.back')} />
    <h2 id="pt-newreq-h" ref={heading} tabIndex={-1}>{t('pt.req.new')}</h2>
    <form className="surface ptForm" onSubmit={(event) => void submit(event)} noValidate aria-label={t('pt.req.new')}>
      <label className="field" htmlFor={ids[0]}><span>{t('pt.req.title')}</span><input id={ids[0]} value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} dir="auto" /></label>
      <label className="field" htmlFor={ids[1]}><span>{t('pt.req.details')}</span><textarea id={ids[1]} rows={5} maxLength={4000} value={details} onChange={(event) => setDetails(event.target.value)} dir="auto" /></label>
      <div className="ptTwo">
        <label className="field" htmlFor={ids[2]}><span>{t('pt.req.severity')}</span>
          <select id={ids[2]} value={severity} onChange={(event) => setSeverity(event.target.value as typeof severity)}>{REQUEST_SEVERITIES.map((item) => <option key={item} value={item}>{severityLabel(item)}</option>)}</select></label>
        {me.projects.length > 0 && <label className="field" htmlFor={ids[3]}><span>{t('pt.req.project')}</span>
          <select id={ids[3]} value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">{t('pt.req.noProject')}</option>{me.projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      </div>
      <p className="muted">{t('pt.req.limit')}</p>
      {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('pt.req.send')}</button><button type="button" className="secondary" onClick={onBack}>{t('common.cancel')}</button></div>
    </form>
  </section>;
}

function RequestDetail({ id, fresh, onBack, onLinkLost }: { id: string; fresh: string | null; onBack: () => void; onLinkLost: (message: string | null) => void }) {
  const detail = useLoad(() => p.getTicket(id), [id]);
  const heading = useFocus<HTMLHeadingElement>();
  useLinkWatch(detail.state, onLinkLost);
  if (detail.state.kind === 'loading') return <section className="ptStack"><Back onClick={onBack} label={t('pt.req.back')} /><Waiting what={t('pt.what.request')} /></section>;
  if (detail.state.kind === 'error') return <section className="ptStack"><Back onClick={onBack} label={t('pt.req.back')} /><Problem error={detail.state.error} onRetry={detail.reload} /></section>;
  const ticket: PortalTicket = detail.state.value;
  return <article className="ptStack" aria-labelledby="pt-reqd-h">
    <Back onClick={onBack} label={t('pt.req.back')} />
    {fresh && <Notice live>{t('pt.req.sent', { reference: fresh })}</Notice>}
    <div className="ptTitleRow"><h2 id="pt-reqd-h" ref={heading} tabIndex={-1} dir="auto">{ticket.title}</h2><Badge tone={ticketTone(ticket.status)}>{ticketStatusLabel(ticket.status)}</Badge></div>
    <p className="muted">{ticket.reference} / {severityLabel(ticket.severity)} / {t('pt.req.reported', { date: dateTime(ticket.reported_at) })}{ticket.project_name ? ` / ${ticket.project_name}` : ''}</p>
    <Text title={t('pt.req.yourText')} text={ticket.description} />
    {ticket.resolution_summary && <section className="surface ptDecision"><h3>{t('pt.req.resolution')}</h3><p dir="auto" className="ptPre">{ticket.resolution_summary}</p>{ticket.resolved_at && <p className="muted">{dateTime(ticket.resolved_at)}</p>}</section>}
    <section className="ptBlock" aria-labelledby="pt-replies-h"><h3 id="pt-replies-h"><FileText aria-hidden="true" />{t('pt.req.replies')}</h3>
      {ticket.replies.length === 0 ? <p className="muted">{t('pt.req.noReplies')}</p>
        : <ul className="ptReplies">{ticket.replies.map((reply, index) => <li key={index} className="surface"><p dir="auto" className="ptPre">{reply.body}</p><small className="muted">{dateTime(reply.sent_at)}</small></li>)}</ul>}
    </section>
  </article>;
}
