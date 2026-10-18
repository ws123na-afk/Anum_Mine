// Social media marketing (docs/marketing.md): connected accounts, a planner with per-platform
// previews in English and Arabic, a calendar to drag posts between days, campaigns with an
// owner-approved auto-post, approvals with the payload hash shown, and analytics from the platforms.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type DragEvent, type FormEvent, type ReactNode } from 'react';
import { BarChart3, CheckCircle2, ChevronLeft, ChevronRight, CircleDot, ExternalLink, Fingerprint, Link2, Lock, Plug, Plus, RefreshCw, Search, Send, ShieldAlert, Sparkles, Trash2, Upload, X } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { argumentRows, shortHash } from './lib/approvals';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, getLocale, t, type MessageKey } from './lib/i18n';
import {
  CAMPAIGN_STATUSES, PLATFORMS, POST_STATUSES, accountStatusName, accountTone, campaignStatusName, change, checkDraft, composeText, dayKey, fromLocalInput,
  isLink, monthGrid, moveToDay, oauthCallback, parseHashtags, platformName, postActions, postsByDay, rate, slotLabel, statusName, statusTone, toLocalInput, when, withUtm,
  type Account, type Campaign, type CampaignStatus, type IdeasResult, type Language, type MediaFile, type Platform, type Post, type PostStatus, type Preferences, type Provider, type Setup, type Summary, type TimeSlot, type WeeklyReport, type ChannelResult,
} from './lib/marketing';
import * as m from './lib/marketingApi';
import { listConversations } from './lib/whatsappApi';
import { contactLabel, type WaConversation } from './lib/whatsapp';
import { useIsOwner } from './useIsOwner';
import { Drawer } from './Dialog';
import { BrevoCampaigns } from './BrevoCampaigns';
import { figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { PageHeader, PageSummary } from './PageParts';
import './marketing.css';

type Tab = 'posts' | 'calendar' | 'campaigns' | 'email' | 'accounts' | 'analytics';
const TABS: Tab[] = ['posts', 'calendar', 'campaigns', 'email', 'accounts', 'analytics'];
const PAGE = 20;

function Note({ children, error, id }: { children: ReactNode; error?: boolean; id?: string }) {
  return <p id={id} className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}
function Badge({ tone, children }: { tone: string; children: ReactNode }) { return <span className={`statusBadge ${tone}`}>{children}</span>; }
function Empty({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="emptyState"><Search aria-hidden="true" /><h3>{title}</h3><p>{text}</p>{action && <div className="actions mkInline mkCenter">{action}</div>}</div>;
}
const failure = (e: unknown, what: string) => isPermissionDenied(e) ? t('marketing.denied', { message: describeError(e) }) : t('marketing.failed', { what, message: describeError(e) });

export function MarketingView({ onOpenBudgets, onOpenFiles }: { onOpenBudgets?: () => void; onOpenFiles?: () => void } = {}) {
  const owner = useIsOwner();
  const [tab, setTab] = useState<Tab>(() => (location.hash.startsWith('#marketing-') && TABS.includes(location.hash.slice(11) as Tab) ? location.hash.slice(11) as Tab : 'posts'));
  const [setup, setSetup] = useState<Setup | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'denied' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [composer, setComposer] = useState<{ post?: Post; day?: Date } | null>(null);
  const [version, setVersion] = useState(0);

  const load = useCallback(async () => {
    try {
      const [s, a, c] = await Promise.all([m.getSetup(), m.listAccounts(), m.listCampaigns({ limit: 100 })]);
      setSetup(s); setAccounts(a); setCampaigns(c); setState('ready'); setError(null);
    } catch (e) {
      setState(isPermissionDenied(e) ? 'denied' : 'error'); setError(describeError(e));
    }
  }, []);

  // The platform sends the browser back here with ?code&state after consent.
  useEffect(() => {
    const callback = oauthCallback(location.search);
    if (!callback) { void load(); return; }
    history.replaceState(null, '', `${location.pathname}#marketing-accounts`);
    setTab('accounts');
    if ('error' in callback) { setNotice(t('marketing.connect.refused', { message: callback.error })); void load(); return; }
    void m.finishConnect(callback.code, callback.state)
      .then((connected) => setNotice(t('marketing.connect.done', { count: connected.length, names: connected.map((item) => item.display_name).join(', ') })))
      .catch((e) => setNotice(failure(e, t('marketing.connect.what'))))
      .finally(() => void load());
  }, [load]);

  // Tabs are history entries, so Back and Forward move between them (docs/module-checklist.md#navigation).
  const pick = (next: Tab) => { setTab(next); if (location.hash !== `#marketing-${next}`) history.pushState(null, '', `#marketing-${next}`); };
  useEffect(() => {
    const sync = () => { const id = location.hash.slice(11); if (location.hash.startsWith('#marketing-') && TABS.includes(id as Tab)) setTab(id as Tab); else if (location.hash === '#marketing') setTab('posts'); };
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  const refreshAll = () => { setVersion((value) => value + 1); void load(); };

  const title = viewLabel('marketing');
  if (state === 'loading') return <><PageHeader title={title} subtitle={t('pages.mk.subtitle')} /><Note>{t('marketing.loading')}</Note></>;
  if (state === 'denied') return <><PageHeader title={title} subtitle={t('pages.mk.subtitle')} /><section className="surface mkPanel"><Lock aria-hidden="true" /><div><h2>{t('marketing.deniedTitle')}</h2><p className="muted">{t('marketing.deniedText')}</p></div></section></>;
  if (state === 'error' || !setup) return <><PageHeader title={title} subtitle={t('pages.mk.subtitle')} /><section className="surface mkPanel"><ShieldAlert aria-hidden="true" /><div><h2>{t('marketing.errorTitle')}</h2><p className="muted">{t('marketing.errorText', { message: error ?? '' })}</p><div className="actions"><button type="button" onClick={() => { setState('loading'); void load(); }}>{t('common.tryAgain')}</button></div></div></section></>;

  const connected = accounts.filter((item) => item.status === 'connected');
  return <div className="mkLayout">
    <PageHeader title={title} subtitle={t('pages.mk.subtitle')} crumbs={tab === 'posts' ? undefined : [{ label: viewLabel('home'), href: '#home' }, { label: title, href: '#marketing-posts', onClick: () => pick('posts') }, { label: t(`marketing.tab.${tab}`) }]} actions={setup.can_write ? <button type="button" onClick={() => setComposer({})} disabled={connected.length === 0} aria-describedby={connected.length === 0 ? 'mk-no-accounts' : undefined}><Plus aria-hidden="true" />{t('marketing.newPost')}</button> : undefined} />
    <MarketingSummary key={`s${version}`} />
    {connected.length === 0 && tab !== 'email' && <Note id="mk-no-accounts">{setup.can_manage ? t('marketing.noAccountsOwner') : t('marketing.noAccountsMember')}</Note>}
    {notice && <Note>{notice}</Note>}
    <nav className="settingsTabs mkTabs" aria-label={t('marketing.sections')}>{TABS.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => pick(id)}>{t(`marketing.tab.${id}`)}</button>)}</nav>
    {composer && <Composer setup={setup} accounts={connected} campaigns={campaigns} post={composer.post} day={composer.day} onOpenBudgets={onOpenBudgets} onOpenFiles={onOpenFiles}
      onClose={() => setComposer(null)} onSaved={(message) => { setComposer(null); setNotice(message); setVersion((value) => value + 1); }} />}
    {tab === 'posts' && <PostsTab key={`p${version}`} setup={setup} campaigns={campaigns} owner={owner === true} onEdit={(post) => setComposer({ post })} onNotice={setNotice} />}
    {tab === 'calendar' && <CalendarTab key={`c${version}`} setup={setup} onEdit={(post) => setComposer({ post })} onNew={(day) => setComposer({ day })} onNotice={setNotice} />}
    {tab === 'campaigns' && <CampaignsTab setup={setup} accounts={connected} owner={owner === true} onChanged={refreshAll} onNotice={setNotice} />}
    {tab === 'email' && <BrevoCampaigns owner={owner === true} />}
    {tab === 'accounts' && <AccountsTab setup={setup} accounts={accounts} onChanged={load} onNotice={setNotice} />}
    {tab === 'analytics' && <AnalyticsTab setup={setup} campaigns={campaigns} onNotice={setNotice} />}
  </div>;
}

// ---- approvals ------------------------------------------------------------------------------------

const HIDDEN_ARGS = new Set(['content_hash', 'post_id', 'account_id', 'campaign_id', 'account_ids']);
const ARG_KEYS = ['platform', 'account', 'accounts', 'language', 'scheduled_at', 'text', 'title', 'media', 'campaign', 'untrusted_source', 'until'];
function argLabel(key: string): string {
  const base = key.split('[')[0];
  return ARG_KEYS.includes(base) ? t(`marketing.arg.${base}` as Parameters<typeof t>[0]) : key;
}

function ApprovalBox({ approval, owner, onDecided }: { approval: Approval; owner: boolean; onDecided: (message: string) => void }) {
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const rows = argumentRows(approval.arguments ?? {}).filter((row) => !HIDDEN_ARGS.has(row.key.split('[')[0]) && row.value !== 'null' && row.value !== '[]');
  async function decide(kind: 'approve' | 'reject') {
    setBusy(true); setProblem(null);
    try {
      const result = kind === 'approve' ? await api.approveTask(approval) : await api.rejectTask(approval);
      onDecided(kind === 'approve' ? (result.run?.result ?? t('marketing.approval.approved')) : t('marketing.approval.rejected'));
    } catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }
  return <div className="mkApproval" role="group" aria-label={t('marketing.approval.label')}>
    <p className="mkApprovalTitle"><ShieldAlert aria-hidden="true" />{t('marketing.approval.waiting')}</p>
    <dl className="mkArgs">{rows.map((row) => <div key={row.key}><dt>{argLabel(row.key)}</dt><dd dir="auto" className={row.multiline ? 'mkMultiline' : undefined}>{row.value}</dd></div>)}</dl>
    <p className="mkHash"><Fingerprint aria-hidden="true" />{t('marketing.approval.hash')} <code title={approval.payloadHash ?? ''}>{shortHash(approval.payloadHash)}</code></p>
    {owner ? <div className="actions mkInline"><button type="button" disabled={busy} onClick={() => void decide('approve')}><CheckCircle2 aria-hidden="true" />{t('marketing.approval.approve')}</button><button type="button" className="secondary" disabled={busy} onClick={() => void decide('reject')}>{t('marketing.approval.reject')}</button></div>
      : <p className="muted">{t('marketing.approval.ownerDecides')}</p>}
    {problem && <Note error>{problem}</Note>}
  </div>;
}

// ---- posts ------------------------------------------------------------------------------------------

function PostCard({ post, setup, owner, onEdit, onChange, onRemove, onNotice }: { post: Post; setup: Setup; owner: boolean; onEdit: (post: Post) => void; onChange: (post: Post) => void; onRemove: (id: string) => void; onNotice: (text: string) => void }) {
  const actions = postActions(post, setup.can_write);
  const [busy, setBusy] = useState(false), [scheduling, setScheduling] = useState(false), [time, setTime] = useState(toLocalInput(post.scheduled_at));
  const timeId = useId();
  const approval = m.approvalOf(post.approval);
  async function act(fn: () => Promise<Post | void>, message?: string) {
    setBusy(true);
    try { const next = await fn(); if (next) onChange(next); if (message) onNotice(message); } catch (e) { onNotice(failure(e, t('marketing.post.what'))); } finally { setBusy(false); }
  }
  const errors = post.issues.filter((issue) => issue.severity === 'error');
  return <li className="surface mkPost">
    <div className="mkPostHead">
      <div><h3><span className={`mkPlatform ${post.platform}`}>{platformName(post.platform)}</span> <span dir="auto">{post.account_name || post.account_id}</span></h3>
        <small className="muted">{t(`marketing.language.${post.language}`)} · {post.status === 'published' || post.status === 'sent_to_inbox' ? t('marketing.publishedAt', { when: when(post.published_at) }) : when(post.scheduled_at)}</small></div>
      <Badge tone={statusTone(post.status)}>{statusName(post.status)}</Badge>
    </div>
    {post.title && <strong dir="auto">{post.title}</strong>}
    <p className="mkPostText" dir={post.language === 'ar' ? 'rtl' : 'auto'} lang={post.language}>{composeText(post.text, post.hashtags, post.link_url)}</p>
    {post.media.length > 0 && <p className="muted mkMediaLine">{post.media.map((item) => item.name || item.file_id).join(' · ')}</p>}
    <div className="mkBadges">
      <small className={post.length > post.limit ? 'mkOver' : 'muted'}>{t('marketing.length', { length: post.length, limit: post.limit })}</small>
      {post.ai_generated && <Badge tone="neutral">{t('marketing.aiDraft')}</Badge>}
      {post.untrusted_source && <Badge tone="warning">{t('marketing.untrusted')}</Badge>}
      {post.authorised_by === 'auto_post' && <Badge tone="neutral">{t('marketing.autoPosted')}</Badge>}
    </div>
    {errors.length > 0 && <ul className="mkIssues">{errors.map((issue) => <li key={issue.code}>{issue.message}</li>)}</ul>}
    {post.last_error && <div className="mkError" role="note"><strong>{post.last_error.message}</strong><span>{post.last_error.fix}</span></div>}
    {post.metrics && <p className="muted">{t('marketing.metricsLine', { reach: formatNumber(post.metrics.reach ?? post.metrics.views ?? 0), engagement: formatNumber(post.metrics.engagement ?? 0), clicks: formatNumber(post.metrics.clicks ?? 0) })}</p>}
    {approval && <ApprovalBox approval={approval} owner={owner} onDecided={(message) => { onNotice(message); void act(() => m.getPost(post.id)); }} />}
    <div className="actions mkPostActions">
      {post.external_url && <a className="secondaryLink" href={post.external_url} target="_blank" rel="noreferrer"><ExternalLink aria-hidden="true" />{t('marketing.open')}</a>}
      {actions.edit && <button type="button" className="secondary" disabled={busy} onClick={() => onEdit(post)}>{t('marketing.edit')}</button>}
      {actions.schedule && !scheduling && <button type="button" disabled={busy || errors.length > 0} onClick={() => setScheduling(true)}><Send aria-hidden="true" />{t('marketing.schedule')}</button>}
      {actions.cancel && <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => m.cancelPost(post.id), t('marketing.cancelled'))}>{t('marketing.cancel')}</button>}
      {actions.publishNow && <button type="button" disabled={busy} onClick={() => void act(() => m.publishNow(post.id))}>{t('marketing.publishNow')}</button>}
      {actions.retry && <button type="button" disabled={busy} onClick={() => void act(() => m.retryPost(post.id))}><RefreshCw aria-hidden="true" />{t('marketing.retry')}</button>}
      {actions.remove && <button type="button" className="secondary dangerText mkDelete" disabled={busy} aria-label={t('marketing.deleteOf', { name: post.account_name })} onClick={() => { if (confirm(post.status === 'published' ? t('marketing.confirmDeletePublished') : t('marketing.confirmDelete'))) void act(async () => { await m.deletePost(post.id); onRemove(post.id); }, t('marketing.deleted')); }}><Trash2 aria-hidden="true" />{t('marketing.delete')}</button>}
    </div>
    {scheduling && <form className="mkScheduleForm" onSubmit={(event) => { event.preventDefault(); setScheduling(false); void act(() => m.schedulePost(post.id, fromLocalInput(time)), t('marketing.scheduleAsked')); }}>
      <label className="field"><span>{t('marketing.when')}</span><input id={timeId} type="datetime-local" value={time} onChange={(event) => setTime(event.target.value)} /></label>
      <small className="muted">{t('marketing.scheduleHint')}</small>
      <div className="actions mkInline"><button type="submit" disabled={busy}>{t('marketing.askToPublish')}</button><button type="button" className="secondary" onClick={() => setScheduling(false)}>{t('common.cancel')}</button></div>
    </form>}
  </li>;
}

function PostsTab({ setup, campaigns, owner, onEdit, onNotice }: { setup: Setup; campaigns: Campaign[]; owner: boolean; onEdit: (post: Post) => void; onNotice: (text: string) => void }) {
  const [items, setItems] = useState<Post[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [problem, setProblem] = useState<string | null>(null);
  const [status, setStatus] = useState<PostStatus | ''>(''), [platform, setPlatform] = useState<Platform | ''>(''), [campaignId, setCampaignId] = useState('');
  const [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [more, setMore] = useState(false), [paging, setPaging] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const ids = useId();
  const load = useCallback(async () => {
    try {
      const page = await m.listPosts({ status, platform, campaignId, query, limit: PAGE });
      setItems(page); setMore(page.length === PAGE); setState('ready');
    } catch (e) { setState('error'); setProblem(failure(e, t('marketing.posts.what'))); }
  }, [status, platform, campaignId, query]);
  useEffect(() => { void load(); }, [load]);
  async function loadMore() {
    const last = items.at(-1); if (!last) return;
    setPaging(true);
    try { const page = await m.listPosts({ status, platform, campaignId, query, limit: PAGE, before: last.id }); setItems((current) => [...current, ...page]); setMore(page.length === PAGE); }
    catch (e) { onNotice(failure(e, t('marketing.posts.what'))); } finally { setPaging(false); }
  }
  const filtered = Boolean(status || platform || campaignId || query);
  const opened = items.find((item) => item.id === openId) ?? null;
  return <section aria-labelledby="mk-posts-title" className="mkPosts">
    <div className="mkPostsMain">
      <h2 id="mk-posts-title" className="visuallyHidden">{t('marketing.tab.posts')}</h2>
      <form className="toolbar mkPostFilters" role="search" onSubmit={(event: FormEvent) => { event.preventDefault(); setQuery(draft.trim()); }}>
        <label className="visuallyHidden" htmlFor={`${ids}-q`}>{t('marketing.filter.search')}</label>
        <input id={`${ids}-q`} type="search" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={t('marketing.filter.searchPlaceholder')} />
        <label className="visuallyHidden" htmlFor={`${ids}-status`}>{t('marketing.filter.status')}</label>
        <select id={`${ids}-status`} value={status} onChange={(event) => setStatus(event.target.value as PostStatus | '')}><option value="">{t('pages.mk.anyStatus')}</option>{POST_STATUSES.map((value) => <option key={value} value={value}>{statusName(value)}</option>)}</select>
        <label className="visuallyHidden" htmlFor={`${ids}-platform`}>{t('marketing.filter.platform')}</label>
        <select id={`${ids}-platform`} value={platform} onChange={(event) => setPlatform(event.target.value as Platform | '')}><option value="">{t('pages.mk.anyPlatform')}</option>{PLATFORMS.map((value) => <option key={value} value={value}>{platformName(value)}</option>)}</select>
        <label className="visuallyHidden" htmlFor={`${ids}-campaign`}>{t('marketing.filter.campaign')}</label>
        <select id={`${ids}-campaign`} value={campaignId} onChange={(event) => setCampaignId(event.target.value)}><option value="">{t('pages.mk.anyCampaign')}</option>{campaigns.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        <button type="submit" className="secondary iconOnly" aria-label={t('common.search')} title={t('common.search')}><Search aria-hidden="true" /></button>
        {filtered && <button type="button" className="secondary" onClick={() => { setStatus(''); setPlatform(''); setCampaignId(''); setDraft(''); setQuery(''); }}>{t('common.clear')}</button>}
      </form>
      {state === 'loading' && <Note>{t('marketing.posts.loading')}</Note>}
      {state === 'error' && <Note error>{problem} <button type="button" className="linkButton" onClick={() => { setState('loading'); void load(); }}>{t('common.retry')}</button></Note>}
      {state === 'ready' && items.length === 0 && (filtered
        ? <Empty title={t('marketing.posts.noMatch')} text={t('marketing.posts.noMatchText')} action={<button type="button" className="secondary" onClick={() => { setStatus(''); setPlatform(''); setCampaignId(''); setDraft(''); setQuery(''); }}>{t('common.clear')}</button>} />
        : <Empty title={t('marketing.posts.empty')} text={setup.can_write ? t('marketing.posts.emptyText') : t('marketing.posts.emptyViewer')} />)}
      {items.length > 0 && <div className="pane dataTableWrap"><table className="dataTable mkPostTable">
        <caption className="visuallyHidden">{t('marketing.tab.posts')}</caption>
        <thead><tr><th scope="col">{t('pages.mk.col.post')}</th><th scope="col" className="optional">{t('pages.mk.col.account')}</th><th scope="col">{t('pages.mk.col.status')}</th><th scope="col" className="optional">{t('pages.mk.col.when')}</th></tr></thead>
        <tbody>{items.map((post) => <tr key={post.id} className={post.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="cellButton" aria-haspopup="dialog" aria-label={t('pages.mk.openPost', { account: post.account_name || post.account_id, text: post.text.slice(0, 60) })} onClick={() => setOpenId(post.id)}>
            <strong dir={post.language === 'ar' ? 'rtl' : 'auto'} lang={post.language} className="mkExcerpt">{post.title || post.text || t('pages.mk.noText')}</strong>
            <small>{t(`marketing.language.${post.language}`)}{post.media.length > 0 ? ` · ${t('pages.mk.media', { count: post.media.length })}` : ''}{post.issues.some((issue) => issue.severity === 'error') ? ` · ${t('pages.mk.hasIssues')}` : ''}</small>
          </button></th>
          <td className="optional"><span className={`mkPlatform ${post.platform}`}>{platformName(post.platform)}</span> <span dir="auto">{post.account_name || post.account_id}</span></td>
          <td><Badge tone={statusTone(post.status)}>{statusName(post.status)}</Badge>{post.approval && <small>{t('pages.mk.awaitingApproval')}</small>}</td>
          <td className="optional nowrap muted">{post.status === 'published' || post.status === 'sent_to_inbox' ? when(post.published_at) : when(post.scheduled_at)}</td>
        </tr>)}</tbody>
      </table></div>}
      {more && <div className="actions mkMore"><button type="button" className="secondary" disabled={paging} onClick={() => void loadMore()}>{paging ? t('common.loading') : t('marketing.loadMore')}</button></div>}
    </div>
    <PlanRail />
    {opened && <Drawer title={t('pages.mk.postTitle', { account: opened.account_name || opened.account_id })} onClose={() => setOpenId(null)}>
      <ul className="mkList mkDrawerPost"><PostCard post={opened} setup={setup} owner={owner} onEdit={(post) => { setOpenId(null); onEdit(post); }} onNotice={onNotice}
        onChange={(next) => setItems((current) => current.map((item) => item.id === next.id ? next : item))} onRemove={(id) => { setOpenId(null); setItems((current) => current.filter((item) => item.id !== id)); }} /></ul>
    </Drawer>}
  </section>;
}

/** The summary band above every tab: the last 30 days from GET /marketing/analytics/summary. */
function MarketingSummary() {
  const [summary, setSummary] = useState<{ state: 'loading' } | { state: 'ready'; value: Summary } | { state: 'error' }>({ state: 'loading' });
  const load = useCallback(() => { setSummary({ state: 'loading' }); m.getSummary(30, '').then((value) => setSummary({ state: 'ready', value }), () => setSummary({ state: 'error' })); }, []);
  useEffect(() => { load(); }, [load]);
  const value = summary.state === 'ready' ? summary.value : null;
  const totals = value?.totals;
  const shift = value ? change(value.totals.engagement_rate, value.previous.engagement_rate) : null;
  const figures = [
    figure('published', t('pages.mk.fig.published'), summary, () => ({ value: formatNumber(totals!.posts), hint: t('pages.mk.fig.publishedHint', { count: value!.previous.posts }) })),
    figure('reach', t('pages.mk.fig.reach'), summary, () => ({ value: formatNumber(totals!.reach || totals!.views), hint: t('pages.mk.fig.reachHint', { clicks: formatNumber(totals!.clicks) }) })),
    figure('rate', t('pages.mk.fig.rate'), summary, () => ({ value: rate(totals!.engagement_rate), hint: shift ? t('pages.mk.fig.rateHint', { change: shift }) : t('pages.mk.fig.rateNone'), tone: 'accent' })),
    figure('upcoming', t('pages.mk.fig.upcoming'), summary, () => ({ value: formatNumber(value!.upcoming), hint: value!.failed ? t('pages.mk.fig.failed', { count: value!.failed }) : t('pages.mk.fig.noneFailed'), tone: value!.failed ? 'danger' : '' })),
    figure('pending', t('pages.mk.fig.pending'), summary, () => ({ value: formatNumber(value!.pending_approval), hint: t('pages.mk.fig.pendingHint'), tone: value!.pending_approval ? 'warn' : '' })),
  ];
  return <PageSummary label={t('pages.mk.summary')} figures={figures} onRetry={load} />;
}

/** Next to the posts: the best times to post and this week's report, both from the API. */
function PlanRail() {
  const [slots, setSlots] = useState<TimeSlot[] | null | 'error'>(null);
  const [report, setReport] = useState<WeeklyReport | null | 'error'>(null);
  const language: Language = getLocale() === 'ar' ? 'ar' : 'en';
  const loadSlots = useCallback(() => { setSlots(null); m.getBestTimes('').then(setSlots, () => setSlots('error')); }, []);
  const loadReport = useCallback(() => { setReport(null); m.getWeeklyReport(language).then(setReport, () => setReport('error')); }, [language]);
  useEffect(() => { loadSlots(); loadReport(); }, [loadSlots, loadReport]);
  return <aside className="pane mkPlanRail" aria-label={t('pages.mk.railLabel')}>
    <section className="railSection" aria-labelledby="mk-best-times">
      <h3 id="mk-best-times">{t('pages.mk.bestTimes')}</h3>
      {slots === null && <p>{t('common.loading')}</p>}
      {slots === 'error' && <p>{t('pages.mk.bestTimesFailed')} <button type="button" className="linkButton" onClick={loadSlots}>{t('pages.mk.loadAgain')}</button></p>}
      {Array.isArray(slots) && slots.length === 0 && <p>{t('pages.mk.bestTimesNone')}</p>}
      {Array.isArray(slots) && slots.length > 0 && <><ol className="railList mkSlots">{slots.map((slot) => <li key={`${slot.weekday}-${slot.hour}`} className="railItem">
        <strong>{slotLabel(slot)}</strong>
        <small>{slot.basis === 'history' ? t('pages.mk.slotHistory', { rate: rate(slot.engagement_rate), count: slot.posts }) : t('pages.mk.slotDefault')}</small>
      </li>)}</ol></>}
    </section>
    <section className="railSection" aria-labelledby="mk-week">
      <h3 id="mk-week">{t('pages.mk.thisWeek')}</h3>
      {report === null && <p>{t('common.loading')}</p>}
      {report === 'error' && <p>{t('pages.mk.reportFailed')} <button type="button" className="linkButton" onClick={loadReport}>{t('pages.mk.loadAgain')}</button></p>}
      {report && report !== 'error' && <>
        <p className="mkWeekHead"><strong dir="auto">{report.headline}</strong><small>{report.week_start} → {report.week_end}</small></p>
        {report.lines.length ? <ul className="mkWeekLines" dir={report.language === 'ar' ? 'rtl' : 'ltr'} lang={report.language}>{report.lines.map((line, index) => <li key={index}>{line}</li>)}</ul> : <p>{t('pages.mk.reportEmpty')}</p>}
      </>}
    </section>
  </aside>;
}

// ---- composer -------------------------------------------------------------------------------------------

interface Attached { file: MediaFile; width?: number | null; height?: number | null; duration_seconds?: number | null }

async function measureVideo(fileId: string): Promise<{ width: number; height: number; duration_seconds: number } | null> {
  try {
    const link = await api.createFileLink(fileId, 'inline');
    return await new Promise((resolve) => {
      const video = document.createElement('video');
      video.preload = 'metadata';
      video.onloadedmetadata = () => resolve({ width: video.videoWidth, height: video.videoHeight, duration_seconds: Math.round(video.duration * 10) / 10 });
      video.onerror = () => resolve(null);
      setTimeout(() => resolve(null), 8000);
      video.src = link.url;
    });
  } catch { return null; }
}

function Composer({ setup, accounts, campaigns, post, day, onClose, onSaved, onOpenBudgets, onOpenFiles }: { setup: Setup; accounts: Account[]; campaigns: Campaign[]; post?: Post; day?: Date; onClose: () => void; onSaved: (message: string) => void; onOpenBudgets?: () => void; onOpenFiles?: () => void }) {
  const editing = Boolean(post);
  const [chosen, setChosen] = useState<string[]>(post ? [post.account_id] : accounts.slice(0, 1).map((item) => item.id));
  const [languages, setLanguages] = useState<Language[]>(post ? [post.language] : ['en', 'ar']);
  const [text, setText] = useState<Record<Language, string>>({ en: post?.language === 'en' ? post.text : '', ar: post?.language === 'ar' ? post.text : '' });
  const [title, setTitle] = useState(post?.title ?? '');
  const [tags, setTags] = useState<Record<Language, string>>({ en: post?.language === 'en' ? post.hashtags.join(' ') : '', ar: post?.language === 'ar' ? post.hashtags.join(' ') : '' });
  const [link, setLink] = useState(post?.link_url ?? ''), [track, setTrack] = useState(!post);
  const [campaignId, setCampaignId] = useState(post?.campaign_id ?? '');
  const [time, setTime] = useState(toLocalInput(post?.scheduled_at ?? (day ? moveToDay(null, day) : null)));
  const [media, setMedia] = useState<Attached[]>(() => post?.media.map((item) => ({ file: { id: item.file_id, name: item.name, content_type: item.content_type, kind: item.kind, size_bytes: item.size_bytes, created_at: '' }, width: item.width, height: item.height, duration_seconds: item.duration_seconds })) ?? []);
  const [library, setLibrary] = useState<MediaFile[] | null>(null), [libraryOpen, setLibraryOpen] = useState(false);
  const [ideas, setIdeas] = useState<IdeasResult | null>(null), [brief, setBrief] = useState(''), [source, setSource] = useState(''), [aiUsed, setAiUsed] = useState(post?.ai_generated ?? false), [untrusted, setUntrusted] = useState(post?.untrusted_source ?? false);
  const [busy, setBusy] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null), [budget, setBudget] = useState<string | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { titleRef.current?.focus(); }, []);
  const campaign = campaigns.find((item) => item.id === campaignId) ?? null;
  const limits = useMemo(() => new Map(setup.platforms.map((item) => [item.platform, item])), [setup]);
  const targets = accounts.filter((item) => chosen.includes(item.id));
  const needsTitle = targets.some((item) => limits.get(item.platform)?.requires_title);
  const linkFor = (platform: Platform): string | null => {
    if (!link.trim() || !isLink(link)) return link.trim() || null;
    return track ? withUtm(link, platform, campaign?.utm_campaign ?? '') : link.trim();
  };
  const variants = targets.flatMap((account) => languages.map((language) => ({ account, language })));
  const previews = variants.map(({ account, language }) => {
    const spec = limits.get(account.platform)!;
    const hashtags = parseHashtags(tags[language]);
    const linkValue = linkFor(account.platform);
    return { account, language, hashtags, link: linkValue, check: checkDraft({ text: text[language], title, hashtags, link: linkValue, media: media.map((item) => ({ kind: item.file.kind, content_type: item.file.content_type })) }, spec), composed: composeText(text[language], hashtags, linkValue) };
  });
  const blocking = previews.some((item) => item.check.issues.some((issue) => issue.severity === 'error'));

  async function openLibrary() {
    setLibraryOpen(true);
    if (library === null) { try { setLibrary(await m.listMedia()); } catch (e) { setProblem(failure(e, t('marketing.media.what'))); setLibrary([]); } }
  }
  async function attach(file: MediaFile) {
    if (media.some((item) => item.file.id === file.id)) { setMedia((current) => current.filter((item) => item.file.id !== file.id)); return; }
    const measured = file.kind === 'video' ? await measureVideo(file.id) : null;
    setMedia((current) => [...current, { file, ...(measured ?? {}) }]);
  }
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy('upload'); setProblem(null);
    try {
      for (const file of [...files]) {
        const created = await api.uploadFile(file);
        const entry: MediaFile = { id: created.id, name: created.name, content_type: created.content_type, kind: created.content_type.startsWith('video/') ? 'video' : 'image', size_bytes: created.size_bytes, created_at: created.created_at };
        setLibrary((current) => [entry, ...(current ?? [])]);
        await attach(entry);
      }
    } catch (e) { setProblem(failure(e, t('marketing.media.uploadWhat'))); } finally { setBusy(null); }
  }
  async function suggest() {
    setBusy('ideas'); setProblem(null); setBudget(null);
    try {
      const platforms = [...new Set(targets.map((item) => item.platform))];
      setIdeas(await m.suggestIdeas({ brief: brief || text.en || text.ar, product: campaign?.product ?? '', feature: '', campaign_id: campaignId || null, platforms: platforms.length ? platforms : ['instagram'], languages, count: 3, source_text: source, source_label: source ? 'pasted source' : '' }));
    } catch (e) { if (isBudgetExceeded(e)) setBudget(budgetMessage(e)); else setProblem(failure(e, t('marketing.ai.what'))); } finally { setBusy(null); }
  }
  async function suggestTags(language: Language) {
    const platform = targets[0]?.platform ?? 'instagram';
    if (!text[language].trim()) { setProblem(t('marketing.ai.writeFirst')); return; }
    setBusy(`tags-${language}`); setProblem(null);
    try { const result = await m.suggestHashtags(text[language], platform, language); setTags((current) => ({ ...current, [language]: [...parseHashtags(current[language]), ...result.hashtags].join(' ') })); }
    catch (e) { if (isBudgetExceeded(e)) setBudget(budgetMessage(e)); else setProblem(failure(e, t('marketing.ai.what'))); } finally { setBusy(null); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!targets.length) { setProblem(t('marketing.composer.pickAccount')); return; }
    if (languages.some((language) => !text[language].trim()) && media.length === 0) { setProblem(t('marketing.composer.writeEach')); return; }
    if (link.trim() && !isLink(link)) { setProblem(t('marketing.composer.badLink')); return; }
    setBusy('save'); setProblem(null);
    const mediaBody = media.map((item) => ({ file_id: item.file.id, width: item.width ?? null, height: item.height ?? null, duration_seconds: item.duration_seconds ?? null }));
    try {
      if (post) {
        await m.updatePost(post.id, { text: text[post.language], title, hashtags: parseHashtags(tags[post.language]), link_url: linkFor(post.platform), media: mediaBody, campaign_id: campaignId || null, clear_campaign: !campaignId });
        const scheduled = fromLocalInput(time);
        if (scheduled && scheduled !== post.scheduled_at) await m.reschedulePost(post.id, scheduled);
        onSaved(t('marketing.composer.updated'));
      } else {
        const created = await m.createPosts({
          campaign_id: campaignId || null, scheduled_at: fromLocalInput(time), ai_generated: aiUsed, untrusted_source: untrusted,
          variants: previews.map((item) => ({ account_id: item.account.id, language: item.language, text: text[item.language], title, hashtags: item.hashtags, link_url: item.link, media: mediaBody })),
        });
        onSaved(t('marketing.composer.saved', { count: created.length }));
      }
    } catch (e) { setProblem(failure(e, t('marketing.composer.what'))); } finally { setBusy(null); }
  }

  return <section className="surface mkComposer" aria-labelledby="mk-composer-title">
    <div className="mkComposerHead"><h2 id="mk-composer-title" tabIndex={-1} ref={titleRef}>{editing ? t('marketing.composer.editTitle') : t('marketing.composer.title')}</h2>
      <button type="button" className="iconButton" aria-label={t('marketing.composer.close')} title={t('marketing.composer.close')} onClick={onClose}><X aria-hidden="true" /></button></div>
    <form onSubmit={(event) => void save(event)} className="mkComposerGrid">
      <div className="mkComposerForm">
        <fieldset className="mkFieldset" disabled={editing}><legend>{t('marketing.composer.accounts')}</legend>
          <div className="mkChoices">{accounts.map((account) => <label key={account.id} className="mkCheck"><input type="checkbox" checked={chosen.includes(account.id)} onChange={(event) => setChosen((current) => event.target.checked ? [...current, account.id] : current.filter((value) => value !== account.id))} /><span className={`mkPlatform ${account.platform}`}>{platformName(account.platform)}</span><span dir="auto">{account.display_name}</span></label>)}</div>
        </fieldset>
        <fieldset className="mkFieldset" disabled={editing}><legend>{t('marketing.composer.languages')}</legend>
          <div className="mkChoices">{(['en', 'ar'] as Language[]).map((language) => <label key={language} className="mkCheck"><input type="checkbox" checked={languages.includes(language)} onChange={(event) => setLanguages((current) => event.target.checked ? [...current, language] : current.filter((value) => value !== language))} />{t(`marketing.language.${language}`)}</label>)}</div>
        </fieldset>
        <div className="formGrid">
          <label className="field"><span>{t('marketing.composer.campaign')}</span><select value={campaignId} onChange={(event) => setCampaignId(event.target.value)}><option value="">{t('marketing.composer.noCampaign')}</option>{campaigns.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label className="field"><span>{t('marketing.composer.when')}</span><input type="datetime-local" value={time} onChange={(event) => setTime(event.target.value)} /></label>
        </div>
        <details className="mkAssist" open={!editing && !text.en && !text.ar}>
          <summary><Sparkles aria-hidden="true" />{t('marketing.ai.title')}</summary>
          <label className="field"><span>{t('marketing.ai.brief')}</span><textarea value={brief} onChange={(event) => setBrief(event.target.value)} placeholder={t('marketing.ai.briefPlaceholder')} rows={2} /></label>
          <label className="field"><span>{t('marketing.ai.source')}</span><textarea value={source} onChange={(event) => setSource(event.target.value)} placeholder={t('marketing.ai.sourcePlaceholder')} rows={2} /></label>
          <small className="muted">{t('marketing.ai.sourceHint')}</small>
          <div className="actions mkInline"><button type="button" className="secondary" disabled={busy !== null} onClick={() => void suggest()}><Sparkles aria-hidden="true" />{busy === 'ideas' ? t('marketing.ai.thinking') : t('marketing.ai.suggest')}</button></div>
          {budget && <Note error>{budget} {onOpenBudgets && <button type="button" className="linkButton" onClick={onOpenBudgets}>{t('marketing.ai.openBudgets')}</button>}</Note>}
          {ideas && <div className="mkIdeas">
            {ideas.note && <Note>{ideas.note}</Note>}
            {ideas.untrusted_source && <Note>{t('marketing.ai.untrustedNote')}</Note>}
            {ideas.ideas.map((idea, index) => <article key={index} className="mkIdea" aria-label={idea.title}>
              <strong>{idea.title}</strong><small className="muted">{platformName(idea.platform)} · {idea.angle}</small>
              {idea.en && <p dir="ltr" lang="en">{idea.en}</p>}{idea.ar && <p dir="rtl" lang="ar">{idea.ar}</p>}
              <div className="actions mkInline"><button type="button" className="secondary" onClick={() => { setText({ en: idea.en ?? text.en, ar: idea.ar ?? text.ar }); setTags({ en: idea.hashtags_en.join(' ') || tags.en, ar: idea.hashtags_ar.join(' ') || tags.ar }); setAiUsed(ideas.generator === 'model'); setUntrusted(ideas.untrusted_source); }}>{t('marketing.ai.use')}</button></div>
            </article>)}
            {ideas.best_times.length > 0 && <p className="muted">{t('marketing.ai.bestTimes', { slots: ideas.best_times.map(slotLabel).join(', ') })}</p>}
          </div>}
        </details>
        {languages.map((language) => <div key={language} className="mkLanguage">
          <label className="field"><span>{t('marketing.composer.textIn', { language: t(`marketing.language.${language}`) })}</span><textarea dir={language === 'ar' ? 'rtl' : 'ltr'} lang={language} rows={5} value={text[language]} onChange={(event) => setText((current) => ({ ...current, [language]: event.target.value }))} /></label>
          <div className="mkTagRow"><label className="field"><span>{t('marketing.composer.hashtagsIn', { language: t(`marketing.language.${language}`) })}</span><input dir={language === 'ar' ? 'rtl' : 'ltr'} value={tags[language]} onChange={(event) => setTags((current) => ({ ...current, [language]: event.target.value }))} placeholder={language === 'ar' ? '#إطارات #السعودية' : '#Tyres #KSA'} /></label>
            <div className="actions mkInline"><button type="button" className="secondary" disabled={busy !== null} onClick={() => void suggestTags(language)}>{t('marketing.ai.hashtags')}</button></div></div>
        </div>)}
        {needsTitle && <label className="field"><span>{t('marketing.composer.videoTitle')}</span><input value={title} maxLength={100} onChange={(event) => setTitle(event.target.value)} /></label>}
        <label className="field"><span>{t('marketing.composer.link')}</span><input type="url" inputMode="url" value={link} onChange={(event) => setLink(event.target.value)} placeholder="https://" /></label>
        <label className="mkCheck"><input type="checkbox" checked={track} onChange={(event) => setTrack(event.target.checked)} /><Link2 aria-hidden="true" />{t('marketing.composer.utm')}</label>
        <div className="mkMedia">
          <div className="actions mkInline"><button type="button" className="secondary" onClick={() => void openLibrary()}>{t('marketing.media.choose')}</button>
            <label className="secondary uploadLabel"><Upload aria-hidden="true" />{busy === 'upload' ? t('common.loading') : t('marketing.media.upload')}<input type="file" accept="image/jpeg,image/png,image/gif,image/webp,video/mp4,video/quicktime,video/webm" multiple className="visuallyHidden" onChange={(event) => { void upload(event.target.files); event.target.value = ''; }} /></label></div>
          {media.length > 0 && <ul className="mkAttached">{media.map((item) => <li key={item.file.id}><span dir="auto">{item.file.name}</span><small className="muted">{item.width && item.height ? `${item.width}×${item.height}` : ''}{item.duration_seconds ? ` · ${item.duration_seconds}s` : ''}</small><button type="button" className="iconButton" aria-label={t('marketing.media.removeOf', { name: item.file.name })} onClick={() => setMedia((current) => current.filter((x) => x.file.id !== item.file.id))}><X aria-hidden="true" /></button></li>)}</ul>}
          {libraryOpen && <div className="mkLibrary" role="group" aria-label={t('marketing.media.library')}>
            {library === null ? <Note>{t('common.loading')}</Note> : library.length === 0 ? <p className="muted">{t('marketing.media.empty')} {onOpenFiles && <button type="button" className="linkButton" onClick={onOpenFiles}>{t('marketing.media.openFiles')}</button>}</p>
              : library.map((file) => <label key={file.id} className="mkCheck"><input type="checkbox" checked={media.some((item) => item.file.id === file.id)} onChange={() => void attach(file)} /><span dir="auto">{file.name}</span><small className="muted">{t(`marketing.media.${file.kind}`)}</small></label>)}
          </div>}
        </div>
        {problem && <Note error>{problem}</Note>}
        <div className="actions"><button type="submit" disabled={busy !== null || blocking || variants.length === 0}>{busy === 'save' ? t('common.loading') : editing ? t('marketing.composer.update') : t('marketing.composer.save', { count: variants.length })}</button>
          <button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
        {blocking && <small className="muted">{t('marketing.composer.fixFirst')}</small>}
      </div>
      <div className="mkPreviews" aria-label={t('marketing.composer.previews')} role="region">
        {previews.length === 0 && <p className="muted">{t('marketing.composer.previewEmpty')}</p>}
        {previews.map((item) => <article key={`${item.account.id}-${item.language}`} className={`mkPreview ${item.account.platform}`} aria-label={t('marketing.composer.previewOf', { platform: platformName(item.account.platform), language: t(`marketing.language.${item.language}`) })}>
          <header><span className={`mkPlatform ${item.account.platform}`}>{platformName(item.account.platform)}</span><span dir="auto">{item.account.handle ? `@${item.account.handle}` : item.account.display_name}</span></header>
          {item.account.platform === 'youtube' && title && <strong dir="auto">{title}</strong>}
          {media.length > 0 && <div className="mkPreviewMedia" aria-hidden="true">{media.length} · {t(`marketing.media.${media[0].file.kind}`)}</div>}
          <p dir={item.language === 'ar' ? 'rtl' : 'ltr'} lang={item.language}>{item.composed || <span className="muted" dir="auto">{t('marketing.composer.previewBlank')}</span>}</p>
          <small className={item.check.length > item.check.limit ? 'mkOver' : 'muted'}>{t('marketing.length', { length: item.check.length, limit: item.check.limit })}</small>
          {item.check.issues.length > 0 && <ul className="mkIssues">{item.check.issues.map((issue) => <li key={issue.code} className={issue.severity}>{issue.message}</li>)}</ul>}
        </article>)}
      </div>
    </form>
  </section>;
}

// ---- calendar ---------------------------------------------------------------------------------------------

function CalendarTab({ setup, onEdit, onNew, onNotice }: { setup: Setup; onEdit: (post: Post) => void; onNew: (day: Date) => void; onNotice: (text: string) => void }) {
  const today = new Date();
  const [month, setMonth] = useState({ year: today.getFullYear(), month: today.getMonth() });
  const [posts, setPosts] = useState<Post[]>([]), [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null), [moving, setMoving] = useState<string | null>(null);
  const weeks = useMemo(() => monthGrid(month.year, month.month), [month]);
  const load = useCallback(async () => {
    const from = weeks[0][0], to = new Date(weeks.at(-1)![6].getFullYear(), weeks.at(-1)![6].getMonth(), weeks.at(-1)![6].getDate() + 1);
    try { setPosts(await m.listPosts({ from: from.toISOString(), to: to.toISOString(), limit: 500 })); setState('ready'); }
    catch (e) { setState('error'); setProblem(failure(e, t('marketing.calendar.what'))); }
  }, [weeks]);
  useEffect(() => { void load(); }, [load]);
  const byDay = useMemo(() => postsByDay(posts), [posts]);
  const movable = (post: Post) => setup.can_write && ['draft', 'pending_approval', 'scheduled', 'failed', 'cancelled'].includes(post.status);
  async function move(postId: string, day: Date) {
    const post = posts.find((item) => item.id === postId);
    if (!post || !movable(post)) return;
    try { const next = await m.reschedulePost(post.id, moveToDay(post.scheduled_at, day).toISOString()); setPosts((current) => current.map((item) => item.id === next.id ? next : item)); onNotice(t('marketing.calendar.moved', { when: when(next.scheduled_at) })); }
    catch (e) { onNotice(failure(e, t('marketing.calendar.moveWhat'))); }
  }
  const label = new Intl.DateTimeFormat(getLocale() === 'ar' ? 'ar-u-nu-latn' : 'en', { month: 'long', year: 'numeric' }).format(new Date(month.year, month.month, 1));
  const shift = (delta: number) => setMonth(({ year, month: current }) => { const next = new Date(year, current + delta, 1); return { year: next.getFullYear(), month: next.getMonth() }; });
  const chip = (post: Post) => <button type="button" key={post.id} className={`mkChip ${post.platform} ${post.status}`} draggable={movable(post)} onDragStart={(event: DragEvent) => { event.dataTransfer.setData('text/plain', post.id); setDragging(post.id); }} onDragEnd={() => setDragging(null)} onClick={() => (movable(post) ? onEdit(post) : post.external_url && window.open(post.external_url, '_blank', 'noreferrer'))}
    aria-label={t('marketing.calendar.chip', { platform: platformName(post.platform), account: post.account_name, status: statusName(post.status), when: when(post.scheduled_at ?? post.published_at) })}>
    <span className="mkChipTime">{formatDateTime(post.scheduled_at ?? post.published_at ?? post.created_at, { hour: '2-digit', minute: '2-digit' })}</span> {platformName(post.platform)}</button>;
  return <section aria-labelledby="mk-calendar-title" className="mkCalendar">
    <div className="mkCalendarHead"><button type="button" className="iconButton" aria-label={t('marketing.calendar.previous')} onClick={() => shift(-1)}><ChevronLeft aria-hidden="true" className="mkFlip" /></button>
      <h2 id="mk-calendar-title">{label}</h2>
      <button type="button" className="iconButton" aria-label={t('marketing.calendar.next')} onClick={() => shift(1)}><ChevronRight aria-hidden="true" className="mkFlip" /></button>
      <div className="actions mkInline"><button type="button" className="secondary" onClick={() => setMonth({ year: today.getFullYear(), month: today.getMonth() })}>{t('marketing.calendar.today')}</button></div></div>
    <p className="muted">{setup.can_write ? t('marketing.calendar.hint') : t('marketing.calendar.viewerHint')}</p>
    {state === 'loading' && <Note>{t('marketing.calendar.loading')}</Note>}
    {state === 'error' && <Note error>{problem} <button type="button" className="linkButton" onClick={() => void load()}>{t('common.retry')}</button></Note>}
    <div className="mkMonth" role="grid" aria-label={label}>
      <div className="mkWeek mkWeekdays" role="row">{[6, 0, 1, 2, 3, 4, 5].map((weekday) => <div key={weekday} role="columnheader" className="mkWeekday">{t(`marketing.weekdayShort.${weekday}` as MessageKey)}</div>)}</div>
      {weeks.map((week) => <div className="mkWeek" role="row" key={dayKey(week[0])}>{week.map((date) => {
        const key = dayKey(date), items = (byDay.get(key) ?? []).map((index) => posts[index]);
        const outside = date.getMonth() !== month.month, isToday = key === dayKey(today);
        return <div key={key} role="gridcell" className={`mkDay ${outside ? 'outside' : ''} ${isToday ? 'today' : ''} ${moving ? 'target' : ''}`} aria-label={formatDateTime(date, { dateStyle: 'full' })}
          onDragOver={(event) => { if (dragging) event.preventDefault(); }} onDrop={(event) => { event.preventDefault(); const id = event.dataTransfer.getData('text/plain'); setDragging(null); if (id) void move(id, date); }}>
          <div className="mkDayHead"><span>{date.getDate()}</span>
            {moving ? <button type="button" className="linkButton" onClick={() => { void move(moving, date); setMoving(null); }}>{t('marketing.calendar.moveHere')}</button>
              : setup.can_write && !outside && <button type="button" className="mkAdd" aria-label={t('marketing.calendar.newOn', { day: formatDateTime(date, { dateStyle: 'medium' }) })} onClick={() => onNew(date)}><Plus aria-hidden="true" /></button>}</div>
          {items.map((post) => <div key={post.id} className="mkChipRow">{chip(post)}{movable(post) && !moving && <button type="button" className="mkMoveButton" aria-label={t('marketing.calendar.moveOf', { platform: platformName(post.platform), account: post.account_name })} onClick={() => setMoving(post.id)}>⇄</button>}</div>)}
        </div>;
      })}</div>)}
    </div>
    {moving && <div className="actions"><Note>{t('marketing.calendar.pickDay')}</Note><button type="button" className="secondary" onClick={() => setMoving(null)}>{t('common.cancel')}</button></div>}
    <ol className="mkAgenda" aria-label={t('marketing.calendar.agenda')}>
      {state === 'ready' && posts.length === 0 && <li className="muted">{t('marketing.calendar.empty')}</li>}
      {[...posts].sort((a, b) => (a.scheduled_at ?? a.published_at ?? '').localeCompare(b.scheduled_at ?? b.published_at ?? '')).map((post) => <li key={post.id} className="surface mkAgendaItem"><span>{when(post.scheduled_at ?? post.published_at)}</span>{chip(post)}<Badge tone={statusTone(post.status)}>{statusName(post.status)}</Badge></li>)}
    </ol>
  </section>;
}

// ---- campaigns --------------------------------------------------------------------------------------------

function CampaignsTab({ setup, accounts, owner, onChanged, onNotice }: { setup: Setup; accounts: Account[]; owner: boolean; onChanged: () => void; onNotice: (text: string) => void }) {
  const [items, setItems] = useState<Campaign[]>([]), [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [problem, setProblem] = useState<string | null>(null);
  const [query, setQuery] = useState(''), [draftQuery, setDraftQuery] = useState(''), [status, setStatus] = useState<CampaignStatus | ''>(''), [more, setMore] = useState(false);
  const [form, setForm] = useState<m.CampaignBody | null>(null), [busy, setBusy] = useState(false), [autoFor, setAutoFor] = useState<string | null>(null), [autoAccounts, setAutoAccounts] = useState<string[]>([]);
  const load = useCallback(async (before?: string) => {
    try { const page = await m.listCampaigns({ query, status, before, limit: PAGE }); setItems((current) => before ? [...current, ...page] : page); setMore(page.length === PAGE); setState('ready'); }
    catch (e) { setState('error'); setProblem(failure(e, t('marketing.campaigns.what'))); }
  }, [query, status]);
  useEffect(() => { void load(); }, [load]);
  async function run(fn: () => Promise<unknown>, message: string) {
    setBusy(true);
    try { await fn(); onNotice(message); await load(); onChanged(); } catch (e) { onNotice(failure(e, t('marketing.campaigns.what'))); } finally { setBusy(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form?.name?.trim()) { onNotice(t('marketing.campaigns.nameRequired')); return; }
    await run(() => m.createCampaign({ ...form, name: form.name!.trim(), start_date: form.start_date || null, end_date: form.end_date || null }), t('marketing.campaigns.created'));
    setForm(null);
  }
  return <section aria-labelledby="mk-campaigns-title">
    <div className="mkSectionHead"><h2 id="mk-campaigns-title">{t('marketing.tab.campaigns')}</h2>{setup.can_write && !form && <div className="actions mkInline"><button type="button" onClick={() => setForm({ name: '', languages: ['en', 'ar'], status: 'draft' })}><Plus aria-hidden="true" />{t('marketing.campaigns.new')}</button></div>}</div>
    {form && <form className="surface mkCampaignForm" onSubmit={(event) => void submit(event)}>
      <div className="formGrid">
        <label className="field"><span>{t('marketing.campaigns.name')}</span><input required maxLength={160} value={form.name ?? ''} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
        <label className="field"><span>{t('marketing.campaigns.product')}</span><input maxLength={200} value={form.product ?? ''} onChange={(event) => setForm({ ...form, product: event.target.value })} placeholder="TyrePulse" /></label>
        <label className="field"><span>{t('marketing.campaigns.start')}</span><input type="date" value={form.start_date ?? ''} onChange={(event) => setForm({ ...form, start_date: event.target.value })} /></label>
        <label className="field"><span>{t('marketing.campaigns.end')}</span><input type="date" value={form.end_date ?? ''} onChange={(event) => setForm({ ...form, end_date: event.target.value })} /></label>
        <label className="field"><span>{t('marketing.campaigns.status')}</span><select value={form.status ?? 'draft'} onChange={(event) => setForm({ ...form, status: event.target.value as CampaignStatus })}>{CAMPAIGN_STATUSES.map((value) => <option key={value} value={value}>{campaignStatusName(value)}</option>)}</select></label>
        <label className="field"><span>{t('marketing.campaigns.utm')}</span><input pattern="[A-Za-z0-9_.\-]*" maxLength={100} value={form.utm_campaign ?? ''} onChange={(event) => setForm({ ...form, utm_campaign: event.target.value })} placeholder="summer_tyres" /></label>
      </div>
      <label className="field"><span>{t('marketing.campaigns.objective')}</span><input maxLength={500} value={form.objective ?? ''} onChange={(event) => setForm({ ...form, objective: event.target.value })} /></label>
      <label className="field"><span>{t('marketing.campaigns.brief')}</span><textarea rows={3} maxLength={4000} value={form.brief ?? ''} onChange={(event) => setForm({ ...form, brief: event.target.value })} /></label>
      <div className="actions"><button type="submit" disabled={busy}>{t('marketing.campaigns.create')}</button><button type="button" className="secondary" onClick={() => setForm(null)}>{t('common.cancel')}</button></div>
    </form>}
    <form className="surface mkFilters" role="search" onSubmit={(event) => { event.preventDefault(); setQuery(draftQuery.trim()); }}>
      <label className="field"><span>{t('marketing.filter.search')}</span><input type="search" value={draftQuery} onChange={(event) => setDraftQuery(event.target.value)} /></label>
      <label className="field"><span>{t('marketing.filter.status')}</span><select value={status} onChange={(event) => setStatus(event.target.value as CampaignStatus | '')}><option value="">{t('marketing.filter.all')}</option>{CAMPAIGN_STATUSES.map((value) => <option key={value} value={value}>{campaignStatusName(value)}</option>)}</select></label>
      <div className="actions mkInline"><button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button></div>
    </form>
    {state === 'loading' && <Note>{t('marketing.campaigns.loading')}</Note>}
    {state === 'error' && <Note error>{problem} <button type="button" className="linkButton" onClick={() => void load()}>{t('common.retry')}</button></Note>}
    {state === 'ready' && items.length === 0 && <Empty title={query || status ? t('marketing.campaigns.noMatch') : t('marketing.campaigns.empty')} text={setup.can_write ? t('marketing.campaigns.emptyText') : t('marketing.campaigns.emptyViewer')} />}
    <ul className="mkList">{items.map((campaign) => {
      const approval = m.approvalOf(campaign.auto_post_approval);
      const total = Object.values(campaign.counts).reduce((sum, value) => sum + value, 0);
      return <li key={campaign.id} className="surface mkCampaign">
        <div className="mkPostHead"><div><h3 dir="auto">{campaign.name}</h3><small className="muted">{[campaign.product, campaign.start_date && campaign.end_date ? `${campaign.start_date} → ${campaign.end_date}` : campaign.start_date, campaign.utm_campaign && `utm_campaign=${campaign.utm_campaign}`].filter(Boolean).join(' · ')}</small></div>
          <Badge tone={campaign.status === 'active' ? 'success' : campaign.status === 'paused' ? 'warning' : 'neutral'}>{campaignStatusName(campaign.status)}</Badge></div>
        {campaign.brief && <p className="muted" dir="auto">{campaign.brief}</p>}
        <p className="muted">{t('marketing.campaigns.posts', { count: total, published: campaign.counts.published ?? 0, scheduled: campaign.counts.scheduled ?? 0 })}</p>
        <p>{campaign.auto_post ? <><CheckCircle2 aria-hidden="true" className="mkInlineIcon" />{t('marketing.campaigns.autoOn', { count: campaign.auto_post_accounts.length })}</> : t('marketing.campaigns.autoOff')}</p>
        {approval && <ApprovalBox approval={approval} owner={owner} onDecided={(message) => { onNotice(message); void load(); onChanged(); }} />}
        {setup.can_write && <div className="actions mkPostActions">
          {CAMPAIGN_STATUSES.filter((value) => value !== campaign.status && value !== 'draft').map((value) => <button key={value} type="button" className="secondary" disabled={busy} onClick={() => void run(() => m.updateCampaign(campaign.id, { status: value }), t('marketing.campaigns.updated'))}>{t(`marketing.campaigns.to.${value}` as MessageKey)}</button>)}
          {setup.can_manage && campaign.auto_post && <button type="button" className="secondary" disabled={busy} onClick={() => void run(() => m.setAutoPost(campaign.id, false, []), t('marketing.campaigns.autoStopped'))}>{t('marketing.campaigns.autoStop')}</button>}
          {setup.can_manage && !campaign.auto_post && !approval && campaign.status === 'active' && <button type="button" className="secondary" disabled={busy} onClick={() => { setAutoFor(campaign.id); setAutoAccounts(accounts.map((item) => item.id)); }}>{t('marketing.campaigns.autoStart')}</button>}
          <button type="button" className="iconButton" disabled={busy} aria-label={t('marketing.campaigns.deleteOf', { name: campaign.name })} onClick={() => { if (confirm(t('marketing.campaigns.confirmDelete', { name: campaign.name }))) void run(() => m.deleteCampaign(campaign.id), t('marketing.campaigns.deleted')); }}><Trash2 aria-hidden="true" /></button>
        </div>}
        {autoFor === campaign.id && <form className="mkAutoForm" onSubmit={(event) => { event.preventDefault(); setAutoFor(null); void run(() => m.setAutoPost(campaign.id, true, autoAccounts), t('marketing.campaigns.autoRequested')); }}>
          <fieldset className="mkFieldset"><legend>{t('marketing.campaigns.autoAccounts')}</legend><div className="mkChoices">{accounts.map((account) => <label key={account.id} className="mkCheck"><input type="checkbox" checked={autoAccounts.includes(account.id)} onChange={(event) => setAutoAccounts((current) => event.target.checked ? [...current, account.id] : current.filter((value) => value !== account.id))} />{platformName(account.platform)} · <span dir="auto">{account.display_name}</span></label>)}</div></fieldset>
          <small className="muted">{t('marketing.campaigns.autoHint')}</small>
          <div className="actions mkInline"><button type="submit" disabled={autoAccounts.length === 0}>{t('marketing.campaigns.autoAsk')}</button><button type="button" className="secondary" onClick={() => setAutoFor(null)}>{t('common.cancel')}</button></div>
        </form>}
      </li>;
    })}</ul>
    {more && <div className="actions mkMore"><button type="button" className="secondary" onClick={() => void load(items.at(-1)?.id)}>{t('marketing.loadMore')}</button></div>}
  </section>;
}

// ---- accounts ---------------------------------------------------------------------------------------------

function AccountsTab({ setup, accounts, onChanged, onNotice }: { setup: Setup; accounts: Account[]; onChanged: () => Promise<void>; onNotice: (text: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null), [prefState, setPrefState] = useState<string | null>(null);
  useEffect(() => { void m.getPreferences().then(setPrefs).catch((e) => setPrefState(failure(e, t('marketing.prefs.what')))); }, []);
  async function connect(provider: Provider) {
    setBusy(provider);
    try { const { authorize_url } = await m.startConnect(provider); window.location.assign(authorize_url); }
    catch (e) { onNotice(failure(e, t('marketing.connect.what'))); setBusy(null); }
  }
  async function run(id: string, fn: () => Promise<unknown>, message: string) {
    setBusy(id);
    try { await fn(); onNotice(message); await onChanged(); } catch (e) { onNotice(failure(e, t('marketing.accounts.what'))); } finally { setBusy(null); }
  }
  const reviews = new Map(setup.platforms.map((item) => [item.platform, item.review]));
  return <section aria-labelledby="mk-accounts-title" className="mkAccounts">
    <h2 id="mk-accounts-title">{t('marketing.accounts.title')}</h2>
    {!setup.can_manage && <Note>{t('marketing.accounts.ownerOnly')}</Note>}
    <div className="pane dataTableWrap"><table className="dataTable mkProviderTable">
      <caption>{t('pages.mk.providers')}</caption>
      <thead><tr><th scope="col">{t('pages.mk.col.provider')}</th><th scope="col">{t('pages.mk.col.status')}</th><th scope="col" className="optional">{t('pages.mk.col.review')}</th><th scope="col"><span className="visuallyHidden">{t('pages.files.col.actions')}</span></th></tr></thead>
      <tbody>{setup.providers.map((provider) => <tr key={provider.provider}>
        <th scope="row"><strong><Plug aria-hidden="true" className="mkInlineIcon" />{provider.label}</strong><small>{provider.platforms.map(platformName).join(' · ')}</small></th>
        <td><Badge tone={provider.configured ? 'success' : 'warning'}>{provider.configured ? t('marketing.accounts.ready') : t('marketing.accounts.notSetUp')}</Badge>{!provider.configured && <small>{t('marketing.accounts.missing')} {provider.missing.map((name) => <code key={name}>{name}</code>)}</small>}</td>
        <td className="optional"><details><summary>{t('marketing.accounts.review')}</summary>{provider.platforms.map((platform) => <p key={platform} className="muted"><strong>{platformName(platform)}:</strong> {reviews.get(platform)}</p>)}{provider.provider === 'tiktok' && <p className="muted">{setup.tiktok_direct_post ? t('marketing.accounts.tiktokDirect') : t('marketing.accounts.tiktokInbox')}</p>}{provider.provider === 'meta' && !setup.public_media && <p className="muted">{t('marketing.accounts.publicMedia')}</p>}</details></td>
        <td>{setup.can_manage && <div className="cellActions"><button type="button" className="secondary" disabled={!provider.configured || busy !== null} onClick={() => void connect(provider.provider)}>{busy === provider.provider ? t('common.loading') : t('marketing.accounts.connect', { name: provider.label })}</button></div>}</td>
      </tr>)}</tbody>
    </table></div>
    <h3>{t('marketing.accounts.connected')}</h3>
    {accounts.length === 0 ? <Empty title={t('marketing.accounts.none')} text={setup.can_manage ? t('marketing.accounts.noneOwner') : t('marketing.accounts.noneMember')} /> : <div className="pane dataTableWrap"><table className="dataTable mkAccountTable">
      <caption className="visuallyHidden">{t('marketing.accounts.connected')}</caption>
      <thead><tr><th scope="col">{t('pages.mk.col.account')}</th><th scope="col">{t('pages.mk.col.status')}</th><th scope="col" className="optional">{t('pages.mk.col.checked')}</th><th scope="col"><span className="visuallyHidden">{t('pages.files.col.actions')}</span></th></tr></thead>
      <tbody>{accounts.map((account) => <tr key={account.id}>
        <th scope="row"><span className={`mkPlatform ${account.platform}`}>{platformName(account.platform)}</span> <strong dir="auto">{account.display_name}</strong>{account.handle && <small dir="ltr">@{account.handle}</small>}{account.last_error && <div className="mkError" role="note"><strong>{account.last_error.message}</strong><span>{account.last_error.fix}</span></div>}</th>
        <td><Badge tone={accountTone(account.status)}>{accountStatusName(account.status)}</Badge></td>
        <td className="optional nowrap muted">{account.last_checked_at ? formatDateTime(account.last_checked_at) : '-'}</td>
        <td>{setup.can_manage && <div className="cellActions">
          {account.status !== 'revoked' && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void run(account.id, () => m.checkAccount(account.id), t('marketing.accounts.checkedNote', { name: account.display_name }))}><RefreshCw aria-hidden="true" />{t('marketing.accounts.check')}</button>}
          {account.status !== 'connected' && <button type="button" className="secondary" onClick={() => void connect(account.provider)}>{t('marketing.accounts.reconnect')}</button>}
          {account.status !== 'revoked' && <button type="button" className="secondary dangerText" disabled={busy !== null} onClick={() => { if (confirm(t('marketing.accounts.confirmDisconnect', { name: account.display_name }))) void run(account.id, () => m.disconnectAccount(account.id), t('marketing.accounts.disconnected', { name: account.display_name })); }}>{t('marketing.accounts.disconnect')}</button>}
        </div>}</td>
      </tr>)}</tbody>
    </table></div>}
    <form className="surface mkPrefs" onSubmit={(event) => { event.preventDefault(); if (!prefs) return; setBusy('prefs'); void m.savePreferences({ brand_voice: prefs.brand_voice, timezone: prefs.timezone, default_hashtags_en: prefs.default_hashtags_en, default_hashtags_ar: prefs.default_hashtags_ar }).then((saved) => { setPrefs(saved); setPrefState(t('marketing.prefs.saved')); }).catch((e) => setPrefState(failure(e, t('marketing.prefs.what')))).finally(() => setBusy(null)); }}>
      <h3>{t('marketing.prefs.title')}</h3>
      {prefs ? <fieldset className="mkFieldset" disabled={!setup.can_manage}>
        <label className="field"><span>{t('marketing.prefs.voice')}</span><textarea rows={3} maxLength={2000} value={prefs.brand_voice} onChange={(event) => setPrefs({ ...prefs, brand_voice: event.target.value })} placeholder={t('marketing.prefs.voicePlaceholder')} /></label>
        <div className="formGrid">
          <label className="field"><span>{t('marketing.prefs.timezone')}</span><select value={prefs.timezone} onChange={(event) => setPrefs({ ...prefs, timezone: event.target.value })}>{['Asia/Riyadh', 'Asia/Dubai', 'Asia/Qatar', 'Asia/Kuwait', 'Asia/Bahrain', 'Asia/Muscat', 'UTC'].map((zone) => <option key={zone} value={zone}>{zone}</option>)}</select></label>
          <label className="field"><span>{t('marketing.prefs.tagsEn')}</span><input value={prefs.default_hashtags_en.join(' ')} onChange={(event) => setPrefs({ ...prefs, default_hashtags_en: parseHashtags(event.target.value) })} /></label>
          <label className="field"><span>{t('marketing.prefs.tagsAr')}</span><input dir="rtl" value={prefs.default_hashtags_ar.join(' ')} onChange={(event) => setPrefs({ ...prefs, default_hashtags_ar: parseHashtags(event.target.value) })} /></label>
        </div>
        {setup.can_manage && <div className="actions"><button type="submit" disabled={busy === 'prefs'}>{t('marketing.prefs.save')}</button></div>}
      </fieldset> : !prefState && <Note>{t('common.loading')}</Note>}
      {prefState && <Note>{prefState}</Note>}
    </form>
  </section>;
}

// ---- weekly report delivery (approval first, docs/marketing.md#weekly-report) -------------------------------

function deliveryNotice(channel: 'email' | 'whatsapp', result: ChannelResult): string {
  const name = t(`marketing.report.channel.${channel}`);
  if (result.outcome === 'requested') return t('marketing.report.requested', { channel: name });
  return t('marketing.report.notSent', { channel: name, message: result.detail || t(`marketing.report.outcome.${result.outcome}` as MessageKey) });
}

function ReportDelivery({ report, language, onNotice }: { report: WeeklyReport; language: Language; onNotice: (text: string) => void }) {
  const emailReady = report.delivery.email === 'available', whatsappReady = report.delivery.whatsapp === 'available';
  const [conversations, setConversations] = useState<WaConversation[] | null>(null), [conversationId, setConversationId] = useState('');
  const [busy, setBusy] = useState<'' | 'email' | 'whatsapp'>('');
  const selectId = useId();
  useEffect(() => {
    if (!whatsappReady) return;
    void listConversations({ limit: 50 }).then((items) => { setConversations(items); setConversationId((current) => current || items[0]?.id || ''); }).catch(() => setConversations([]));
  }, [whatsappReady]);
  async function send(channel: 'email' | 'whatsapp') {
    setBusy(channel);
    try {
      const result = await m.deliverWeeklyReport(language, channel === 'email' ? { email: true } : { email: false, whatsapp_conversation_id: conversationId });
      onNotice(deliveryNotice(channel, result[channel]));
    } catch (e) { onNotice(failure(e, t('marketing.report.what'))); } finally { setBusy(''); }
  }
  if (!emailReady && !whatsappReady) return <small className="muted">{t('marketing.report.delivery')}</small>;
  return <div className="mkReportSend">
    <small className="muted">{t('marketing.report.approvalNote')}</small>
    <div className="actions mkInline">
      {emailReady && <button type="button" className="secondary" disabled={busy !== ''} onClick={() => void send('email')}><Send aria-hidden="true" className="mkInlineIcon" />{t('marketing.report.sendEmail')}</button>}
      {whatsappReady && (conversations === null ? <small className="muted">{t('common.loading')}</small> : conversations.length === 0 ? <small className="muted">{t('marketing.report.noConversations')}</small> : <>
        <label className="visuallyHidden" htmlFor={selectId}>{t('marketing.report.conversation')}</label>
        <select id={selectId} value={conversationId} onChange={(event) => setConversationId(event.target.value)}>{conversations.map((item) => <option key={item.id} value={item.id}>{contactLabel(item)}</option>)}</select>
        <button type="button" className="secondary" disabled={busy !== '' || !conversationId} onClick={() => void send('whatsapp')}><Send aria-hidden="true" className="mkInlineIcon" />{t('marketing.report.sendWhatsapp')}</button>
      </>)}
    </div>
  </div>;
}

// ---- analytics --------------------------------------------------------------------------------------------

function AnalyticsTab({ setup, campaigns, onNotice }: { setup: Setup; campaigns: Campaign[]; onNotice: (text: string) => void }) {
  const [days, setDays] = useState(30), [platform, setPlatform] = useState<Platform | ''>(''), [campaignId, setCampaignId] = useState('');
  const [summary, setSummary] = useState<Summary | null>(null), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [report, setReport] = useState<WeeklyReport | null>(null), [reportLanguage, setReportLanguage] = useState<Language>(getLocale() === 'ar' ? 'ar' : 'en');
  const load = useCallback(async () => {
    try { setSummary(await m.getSummary(days, platform, campaignId)); setProblem(null); } catch (e) { setProblem(failure(e, t('marketing.analytics.what'))); }
  }, [days, platform, campaignId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void m.getWeeklyReport(reportLanguage).then(setReport).catch(() => setReport(null)); }, [reportLanguage]);
  async function refresh() {
    setBusy(true);
    try {
      const result = await m.refreshMetrics(Math.min(days, 90));
      onNotice(result.failures.length ? t('marketing.analytics.refreshedWithFailures', { count: result.updated, failures: result.failures.map((item) => `${item.account}: ${item.error.message} ${item.error.fix}`).join(' ') }) : t('marketing.analytics.refreshed', { count: result.updated }));
      await load();
    } catch (e) { onNotice(failure(e, t('marketing.analytics.refreshWhat'))); } finally { setBusy(false); }
  }
  const totals = summary?.totals;
  const peak = Math.max(1, ...(summary?.series.map((point) => point.engagement) ?? [1]));
  return <section aria-labelledby="mk-analytics-title" className="mkAnalytics">
    <div className="mkSectionHead"><h2 id="mk-analytics-title">{t('marketing.tab.analytics')}</h2>{setup.can_write && <div className="actions mkInline"><button type="button" className="secondary" disabled={busy} onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{busy ? t('common.loading') : t('marketing.analytics.refresh')}</button></div>}</div>
    <div className="surface mkFilters">
      <label className="field"><span>{t('marketing.analytics.period')}</span><select value={days} onChange={(event) => setDays(Number(event.target.value))}>{[7, 30, 90].map((value) => <option key={value} value={value}>{t('marketing.analytics.days', { count: value })}</option>)}</select></label>
      <label className="field"><span>{t('marketing.filter.platform')}</span><select value={platform} onChange={(event) => setPlatform(event.target.value as Platform | '')}><option value="">{t('marketing.filter.all')}</option>{PLATFORMS.map((value) => <option key={value} value={value}>{platformName(value)}</option>)}</select></label>
      <label className="field"><span>{t('marketing.filter.campaign')}</span><select value={campaignId} onChange={(event) => setCampaignId(event.target.value)}><option value="">{t('marketing.filter.all')}</option>{campaigns.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    </div>
    {problem && <Note error>{problem} <button type="button" className="linkButton" onClick={() => void load()}>{t('common.retry')}</button></Note>}
    {!summary && !problem && <Note>{t('marketing.analytics.loading')}</Note>}
    {summary && totals && <>
      {totals.posts === 0 ? <Empty title={t('marketing.analytics.empty')} text={t('marketing.analytics.emptyText')} /> : <>
        <div className="mkMetrics">
          {([['reach', totals.reach || totals.views], ['impressions', totals.impressions || totals.views], ['engagement', totals.engagement], ['clicks', totals.clicks]] as const).map(([key, value]) => <article key={key} className="metric"><span>{t(`marketing.analytics.${key}`)}</span><strong>{formatNumber(value)}</strong><small>{key === 'engagement' ? t('marketing.analytics.rate', { rate: rate(totals.engagement_rate), change: change(totals.engagement_rate, summary.previous.engagement_rate) ?? '-' }) : t('marketing.analytics.posts', { count: totals.posts })}</small></article>)}
        </div>
        {totals.without_metrics > 0 && <Note>{t('marketing.analytics.noNumbers', { count: totals.without_metrics })}</Note>}
        <div className="surface mkChart" role="img" aria-label={t('marketing.analytics.chartLabel', { days })}>{summary.series.map((point) => <span key={point.day} title={`${point.day}: ${point.engagement}`} style={{ blockSize: `${Math.max(2, (point.engagement / peak) * 100)}%` }} />)}</div>
        <div className="mkTables">
          <table className="surface mkTable" tabIndex={0}><caption>{t('marketing.analytics.byPlatform')}</caption><thead><tr><th scope="col">{t('marketing.filter.platform')}</th><th scope="col">{t('marketing.analytics.postsShort')}</th><th scope="col">{t('marketing.analytics.reach')}</th><th scope="col">{t('marketing.analytics.engagement')}</th><th scope="col">{t('marketing.analytics.clicks')}</th><th scope="col">{t('marketing.analytics.rateShort')}</th></tr></thead>
            <tbody>{summary.platforms.map((item) => <tr key={item.platform}><th scope="row">{platformName(item.platform)}</th><td>{item.posts}</td><td>{formatNumber(item.reach || item.views)}</td><td>{formatNumber(item.engagement)}</td><td>{formatNumber(item.clicks)}</td><td>{rate(item.engagement_rate)}</td></tr>)}</tbody></table>
          <table className="surface mkTable" tabIndex={0}><caption>{t('marketing.analytics.byCampaign')}</caption><thead><tr><th scope="col">{t('marketing.filter.campaign')}</th><th scope="col">{t('marketing.analytics.postsShort')}</th><th scope="col">{t('marketing.analytics.engagement')}</th><th scope="col">{t('marketing.analytics.rateShort')}</th></tr></thead>
            <tbody>{summary.campaigns.map((item) => <tr key={item.campaign_id ?? 'none'}><th scope="row" dir="auto">{item.campaign_id ? item.name : t('marketing.analytics.noCampaign')}</th><td>{item.posts}</td><td>{formatNumber(item.engagement)}</td><td>{rate(item.engagement_rate)}</td></tr>)}</tbody></table>
        </div>
        <h3>{t('marketing.analytics.top')}</h3>
        <ol className="mkTop">{summary.top_posts.map((post) => <li key={post.id} className="surface"><span className={`mkPlatform ${post.platform}`}>{platformName(post.platform)}</span><span dir="auto">{post.excerpt}</span><small className="muted">{t('marketing.metricsLine', { reach: formatNumber(post.metrics?.reach ?? post.metrics?.views ?? 0), engagement: formatNumber(post.metrics?.engagement ?? 0), clicks: formatNumber(post.metrics?.clicks ?? 0) })}</small>{post.external_url && <a href={post.external_url} target="_blank" rel="noreferrer"><ExternalLink aria-hidden="true" />{t('marketing.open')}</a>}</li>)}</ol>
      </>}
      <p className="muted">{t('marketing.analytics.queue', { upcoming: summary.upcoming, pending: summary.pending_approval, failed: summary.failed })}{summary.metrics_updated_at ? ` ${t('marketing.analytics.updated', { when: formatDateTime(summary.metrics_updated_at) })}` : ''}</p>
    </>}
    <section className="surface mkReport" aria-labelledby="mk-report-title">
      <div className="mkSectionHead"><h3 id="mk-report-title"><BarChart3 aria-hidden="true" className="mkInlineIcon" />{report?.headline ?? t('marketing.report.title')}</h3>
        <div className="actions mkInline"><select aria-label={t('marketing.report.language')} value={reportLanguage} onChange={(event) => setReportLanguage(event.target.value as Language)}><option value="en">English</option><option value="ar">العربية</option></select>
          {report && <button type="button" className="secondary" onClick={() => void navigator.clipboard?.writeText([report.headline, ...report.lines].join('\n')).then(() => onNotice(t('marketing.report.copied')), () => onNotice(t('marketing.report.copyFailed')))}>{t('marketing.report.copy')}</button>}</div></div>
      {report ? <><p className="muted">{report.week_start} → {report.week_end}</p><ul dir={report.language === 'ar' ? 'rtl' : 'ltr'} lang={report.language}>{report.lines.map((line, index) => <li key={index}>{line}</li>)}</ul><ReportDelivery report={report} language={reportLanguage} onNotice={onNotice} /></> : <p className="muted">{t('common.loading')}</p>}
    </section>
  </section>;
}

