// The Marketing overview and audience tabs (docs/marketing.md#overview): the KPI row over every tab,
// the campaigns table, the content calendar, the engagement trend, what needs attention, the top
// posts and the connected channels; and the Brevo contact lists as the audience. Every figure comes
// from the API. ANUM does not track spend, leads, conversions, cost per lead or return on spend, so
// those mockup figures are left out rather than shown with made-up numbers.
import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import {
  AlertTriangle, BarChart3, CalendarDays, CheckCircle2, ChevronLeft, ChevronRight, Clock3, ExternalLink, Eye, Megaphone, MousePointerClick, Plug,
  Plus, Search, Send, ShieldAlert, Sparkles, TrendingUp, Users,
} from 'lucide-react';
import type { ContactList, Status as BrevoStatus } from './lib/brevo';
import * as b from './lib/brevoApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { delta } from './lib/financeNav';
import { formatDateTime, formatNumber, getLocale, t, type MessageKey } from './lib/i18n';
import {
  campaignStatusName, change, dayKey, monthGrid, platformName, rate, type Account, type Campaign, type CampaignStatus, type MarketingTab, type Post, type Setup, type Summary,
} from './lib/marketing';
import * as m from './lib/marketingApi';
import { audienceTotals, campaignRows, postCountByDay, reachOf, reachablePercent, trendPath, trendPoints } from './lib/marketingHub';
import { Empty, LoadError, Notice } from './FinanceParts';
import { FinBanner, FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';

export type SummaryLoad = { kind: 'loading' } | { kind: 'ready'; value: Summary } | { kind: 'error'; message: string };
type Chip = 'ok' | 'warn' | 'bad' | 'info' | 'muted';
const campaignChip: Record<CampaignStatus, Chip> = { active: 'ok', draft: 'muted', paused: 'warn', completed: 'info', archived: 'muted' };
const shortDate = (iso: string) => formatDateTime(`${iso.slice(0, 10)}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' });

/** The five figures over every Marketing tab: the last 30 days against the 30 before. */
export function MarketingKpis({ load, onRetry }: { load: SummaryLoad; onRetry: () => void }) {
  if (load.kind === 'error') return <LoadError message={t('mkHub.kpi.failed', { error: load.message })} retry={onRetry} />;
  const s = load.kind === 'ready' ? load.value : null;
  const wait = <span className="muted">{t('common.loading')}</span>;
  const now = s?.totals, before = s?.previous;
  const vs = t('mkHub.kpi.vsPrevious');
  const points = now && before ? change(now.engagement_rate, before.engagement_rate) : null;
  return <KpiRow label={t('pages.mk.summary')}>
    <KpiCard icon={<Users />} label={t('mkHub.kpi.reach')} value={now ? formatNumber(reachOf(now)) : wait} delta={now && before ? delta(reachOf(now), reachOf(before)) : null} hint={now ? vs : undefined} />
    <KpiCard icon={<Eye />} label={t('mkHub.kpi.impressions')} value={now ? formatNumber(now.impressions) : wait} delta={now && before ? delta(now.impressions, before.impressions) : null} hint={now ? vs : undefined} />
    <KpiCard icon={<TrendingUp />} label={t('mkHub.kpi.rate')} value={now ? rate(now.engagement_rate) : wait}
      hint={now ? (points ? t('mkHub.kpi.ratePoints', { change: points }) : t('mkHub.kpi.rateNone')) : undefined} />
    <KpiCard icon={<MousePointerClick />} label={t('mkHub.kpi.clicks')} value={now ? formatNumber(now.clicks) : wait} delta={now && before ? delta(now.clicks, before.clicks) : null} hint={now ? vs : undefined} />
    <KpiCard icon={<Send />} label={t('mkHub.kpi.published')} value={now ? formatNumber(now.posts) : wait} delta={now && before ? delta(now.posts, before.posts) : null}
      hint={now ? (now.without_metrics ? t('mkHub.kpi.withoutMetrics', { count: now.without_metrics }) : vs) : undefined} />
  </KpiRow>;
}

export function OverviewTab({ setup, accounts, campaigns, load, onGo, onNewPost }: {
  setup: Setup; accounts: Account[]; campaigns: Campaign[]; load: SummaryLoad; onGo: (tab: MarketingTab) => void; onNewPost: (day?: Date) => void;
}) {
  const summary = load.kind === 'ready' ? load.value : null;
  return <div className="mkOverview">
    <div className="mkOverviewMain">
      <CampaignsCard campaigns={campaigns} summary={summary} canWrite={setup.can_write} onGo={onGo} />
      <div className="mkOverviewPair">
        <ContentCalendarCard canWrite={setup.can_write} onGo={onGo} onNewPost={onNewPost} />
        <TrendCard load={load} onGo={onGo} />
      </div>
    </div>
    <div className="mkOverviewSide">
      <AttentionCard summary={summary} onGo={onGo} />
      <TopPostsCard summary={summary} onGo={onGo} />
      <ChannelsCard accounts={accounts} canManage={setup.can_manage} onGo={onGo} />
    </div>
  </div>;
}

function CampaignsCard({ campaigns, summary, canWrite, onGo }: { campaigns: Campaign[]; summary: Summary | null; canWrite: boolean; onGo: (tab: MarketingTab) => void }) {
  const rows = campaignRows(campaigns, summary);
  return <FinCard title={t('mkHub.campaigns.title')} icon={<Megaphone />} subtitle={campaigns.length ? t('mkHub.campaigns.count', { count: campaigns.length }) : undefined} className="mkCampaignsCard"
    actions={<button type="button" className="secondary mkSmall" onClick={() => onGo('campaigns')}>{canWrite ? <><Plus aria-hidden="true" />{t('mkHub.campaigns.manage')}</> : t('mkHub.campaigns.all')}</button>}>
    {rows.length === 0 ? <Empty icon={<Megaphone aria-hidden="true" />} title={t('mkHub.campaigns.emptyTitle')} text={canWrite ? t('mkHub.campaigns.emptyText') : t('mkHub.campaigns.emptyViewer')}
      action={canWrite ? <button type="button" onClick={() => onGo('campaigns')}><Plus aria-hidden="true" />{t('mkHub.campaigns.create')}</button> : undefined} />
      : <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('mkHub.campaigns.title')}>
        <table className="finGrid mkHubTable">
          <thead><tr><th scope="col">{t('mkHub.col.campaign')}</th><th scope="col">{t('mkHub.col.channels')}</th><th scope="col">{t('mkHub.col.status')}</th>
            <th scope="col">{t('mkHub.col.dates')}</th><th scope="col" className="num">{t('mkHub.col.posts')}</th><th scope="col" className="num">{t('mkHub.col.reach')}</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id} data-campaign-row={row.id}>
            <th scope="row"><button type="button" className="linkButton mkCampaignLink" onClick={() => onGo('campaigns')} aria-label={t('mkHub.campaigns.openOf', { name: row.name })}><span dir="auto">{row.name}</span></button>
              {row.autoPost && <small className="muted">{t('mkHub.campaigns.autoPost')}</small>}</th>
            <td>{row.platforms.length ? row.platforms.map((p) => <span key={p} className={`mkPlatform ${p}`}>{platformName(p)}</span>) : '-'}</td>
            <td><StatusChip tone={campaignChip[row.status] ?? 'muted'}>{campaignStatusName(row.status)}</StatusChip></td>
            <td className="nowrap">{row.start || row.end ? `${row.start ? shortDate(row.start) : '-'} ${t('mkHub.to')} ${row.end ? shortDate(row.end) : '-'}` : '-'}</td>
            <td className="num">{t('mkHub.campaigns.postsCell', { published: row.published, total: row.posts })}</td>
            <td className="num">{row.reach === null ? '-' : formatNumber(row.reach)}</td>
          </tr>)}</tbody>
        </table>
      </div>}
  </FinCard>;
}

function ContentCalendarCard({ canWrite, onGo, onNewPost }: { canWrite: boolean; onGo: (tab: MarketingTab) => void; onNewPost: (day?: Date) => void }) {
  const today = new Date();
  const [month, setMonth] = useState({ year: today.getFullYear(), month: today.getMonth() });
  const [picked, setPicked] = useState(dayKey(today));
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const weeks = useMemo(() => monthGrid(month.year, month.month), [month]);
  const fetchMonth = useCallback(async () => {
    setPosts(null); setProblem(null);
    const from = weeks[0][0], last = weeks.at(-1)![6], to = new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1);
    try { setPosts(await m.listPosts({ from: from.toISOString(), to: to.toISOString(), limit: 500 })); }
    catch (e) { setProblem(describeError(e)); }
  }, [weeks]);
  useEffect(() => { void fetchMonth(); }, [fetchMonth]);
  const counts = useMemo(() => postCountByDay(posts ?? [], dayKey), [posts]);
  const dayPosts = (posts ?? []).filter((p) => { const at = p.published_at ?? p.scheduled_at; return at && dayKey(new Date(at)) === picked; })
    .sort((a, b) => (a.scheduled_at ?? a.published_at ?? '').localeCompare(b.scheduled_at ?? b.published_at ?? ''));
  const label = new Intl.DateTimeFormat(getLocale() === 'ar' ? 'ar-u-nu-latn' : 'en', { month: 'long', year: 'numeric' }).format(new Date(month.year, month.month, 1));
  const shift = (by: number) => setMonth(({ year, month: current }) => { const next = new Date(year, current + by, 1); return { year: next.getFullYear(), month: next.getMonth() }; });
  const pickedDate = new Date(`${picked}T12:00:00`);
  return <FinCard title={t('mkHub.calendar.title')} icon={<CalendarDays />} className="mkMiniCalCard"
    actions={<button type="button" className="secondary mkSmall" onClick={() => onGo('calendar')}>{t('mkHub.calendar.full')}</button>}>
    <div className="mkMiniCalHead">
      <button type="button" className="iconButton" aria-label={t('marketing.calendar.previous')} onClick={() => shift(-1)}><ChevronLeft aria-hidden="true" className="mkFlip" /></button>
      <strong aria-live="polite">{label}</strong>
      <button type="button" className="iconButton" aria-label={t('marketing.calendar.next')} onClick={() => shift(1)}><ChevronRight aria-hidden="true" className="mkFlip" /></button>
    </div>
    {problem && <LoadError message={t('mkHub.calendar.failed', { error: problem })} retry={() => void fetchMonth()} />}
    <table className="mkMiniCal" aria-label={label}>
      <thead><tr>{[6, 0, 1, 2, 3, 4, 5].map((weekday) => <th key={weekday} scope="col">{t(`marketing.weekdayShort.${weekday}` as MessageKey)}</th>)}</tr></thead>
      <tbody>{weeks.map((week) => <tr key={dayKey(week[0])}>{week.map((date) => {
        const key = dayKey(date), n = counts.get(key) ?? 0, outside = date.getMonth() !== month.month;
        return <td key={key} className={outside ? 'outside' : undefined}>
          <button type="button" className={`mkMiniDay ${key === picked ? 'picked' : ''} ${key === dayKey(today) ? 'today' : ''}`} aria-pressed={key === picked}
            aria-label={`${formatDateTime(date, { dateStyle: 'full' })}. ${t('mkHub.calendar.posts', { count: n })}`} onClick={() => setPicked(key)}>
            <span aria-hidden="true">{date.getDate()}</span>{n > 0 && <span className="mkMiniDot" aria-hidden="true" />}
          </button>
        </td>;
      })}</tr>)}</tbody>
    </table>
    <div className="mkMiniDayList">
      <h3>{formatDateTime(pickedDate, { weekday: 'short', day: 'numeric', month: 'short' })}</h3>
      {posts === null && !problem && <Notice busy>{t('common.loading')}</Notice>}
      {posts !== null && dayPosts.length === 0 && <p className="muted">{t('mkHub.calendar.none')}{canWrite && <> <button type="button" className="linkButton" onClick={() => onNewPost(pickedDate)}>{t('mkHub.calendar.add')}</button></>}</p>}
      {dayPosts.length > 0 && <ul>{dayPosts.slice(0, 4).map((p) => <li key={p.id}>
        <span className={`mkPlatform ${p.platform}`}>{platformName(p.platform)}</span>
        <span className="mkMiniText" dir="auto">{p.title || p.text || t('pages.mk.noText')}</span>
        <small className="muted">{formatDateTime(p.published_at ?? p.scheduled_at ?? p.created_at, { hour: '2-digit', minute: '2-digit' })}</small>
      </li>)}</ul>}
      {dayPosts.length > 4 && <button type="button" className="linkButton" onClick={() => onGo('calendar')}>{t('mkHub.calendar.more', { count: dayPosts.length - 4 })}</button>}
    </div>
  </FinCard>;
}

function TrendCard({ load, onGo }: { load: SummaryLoad; onGo: (tab: MarketingTab) => void }) {
  const titleId = useId();
  const series = load.kind === 'ready' ? load.value.series : [];
  const W = 320, H = 140;
  const points = trendPoints(series, W, H);
  const total = series.reduce((n, s) => n + s.engagement, 0);
  return <FinCard title={t('mkHub.trend.title')} icon={<BarChart3 />} subtitle={t('mkHub.trend.sub')} className="mkTrendCard"
    actions={<button type="button" className="secondary mkSmall" onClick={() => onGo('analytics')}>{t('mkHub.trend.analytics')}</button>}>
    {load.kind === 'loading' && <Notice busy>{t('common.loading')}</Notice>}
    {load.kind === 'ready' && total === 0 && <Empty icon={<BarChart3 aria-hidden="true" />} title={t('mkHub.trend.emptyTitle')} text={t('mkHub.trend.emptyText')} />}
    {load.kind === 'ready' && total > 0 && <figure className="mkTrend" aria-labelledby={titleId}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={titleId} preserveAspectRatio="none">
        <line x1="0" x2={W} y1={H - 1} y2={H - 1} className="mkTrendAxis" />
        <path d={`${trendPath(points)} L${points.at(-1)!.x} ${H} L${points[0].x} ${H} Z`} className="mkTrendArea" />
        <path d={trendPath(points)} className="mkTrendLine" />
        {points.length <= 31 && points.map((p) => <circle key={p.day} cx={p.x} cy={p.y} r="2.5" className="mkTrendDot" />)}
      </svg>
      <figcaption id={titleId}>{t('mkHub.trend.caption', { total, first: shortDate(series[0].day), last: shortDate(series.at(-1)!.day) })}</figcaption>
      <table className="visuallyHidden"><caption>{t('mkHub.trend.title')}</caption><thead><tr><th scope="col">{t('mkHub.col.day')}</th><th scope="col">{t('mkHub.col.engagement')}</th></tr></thead>
        <tbody>{series.map((s) => <tr key={s.day}><th scope="row">{shortDate(s.day)}</th><td>{formatNumber(s.engagement)}</td></tr>)}</tbody></table>
    </figure>}
  </FinCard>;
}

function AttentionCard({ summary, onGo }: { summary: Summary | null; onGo: (tab: MarketingTab) => void }) {
  const rows: { key: string; icon: ReactNode; label: string; count: number; tone: Chip; tab: MarketingTab }[] = summary ? [
    { key: 'pending', icon: <ShieldAlert />, label: t('mkHub.attention.pending'), count: summary.pending_approval, tone: summary.pending_approval ? 'warn' : 'muted', tab: 'posts' },
    { key: 'failed', icon: <AlertTriangle />, label: t('mkHub.attention.failed'), count: summary.failed, tone: summary.failed ? 'bad' : 'muted', tab: 'posts' },
    { key: 'upcoming', icon: <Clock3 />, label: t('mkHub.attention.upcoming'), count: summary.upcoming, tone: summary.upcoming ? 'info' : 'muted', tab: 'calendar' },
  ] : [];
  const clear = summary !== null && !summary.pending_approval && !summary.failed;
  return <FinCard title={t('mkHub.attention.title')} icon={<Sparkles />}>
    {!summary && <Notice busy>{t('common.loading')}</Notice>}
    {clear && <FinBanner tone="ok" icon={<CheckCircle2 />} title={t('mkHub.attention.clear')} />}
    {summary && <ul className="mkAttention">{rows.map((row) => <li key={row.key}>
      <span className="mkAttentionIcon" aria-hidden="true">{row.icon}</span><span>{row.label}</span>
      <StatusChip tone={row.tone}>{formatNumber(row.count)}</StatusChip>
      <button type="button" className="secondary mkSmall" onClick={() => onGo(row.tab)} aria-label={t('mkHub.attention.openOf', { what: row.label })}>{t('mkHub.open')}</button>
    </li>)}</ul>}
    {summary?.metrics_updated_at && <p className="muted mkSmallText">{t('mkHub.attention.metricsAt', { when: formatDateTime(summary.metrics_updated_at) })}</p>}
  </FinCard>;
}

function TopPostsCard({ summary, onGo }: { summary: Summary | null; onGo: (tab: MarketingTab) => void }) {
  const top = summary?.top_posts.slice(0, 3) ?? [];
  return <FinCard title={t('mkHub.top.title')} icon={<TrendingUp />} actions={<button type="button" className="secondary mkSmall" onClick={() => onGo('analytics')}>{t('mkHub.top.all')}</button>}>
    {!summary && <Notice busy>{t('common.loading')}</Notice>}
    {summary && top.length === 0 && <p className="muted">{t('mkHub.top.none')}</p>}
    {top.length > 0 && <ol className="mkTopPosts">{top.map((p) => <li key={p.id}>
      <div><span className={`mkPlatform ${p.platform}`}>{platformName(p.platform)}</span> <span dir="auto" className="mkMiniText">{p.excerpt || t('pages.mk.noText')}</span></div>
      <small className="muted">{p.metrics ? t('mkHub.top.metrics', { reach: p.metrics.reach || p.metrics.views || 0, engagement: p.metrics.engagement ?? 0 }) : t('mkHub.top.noMetrics')}</small>
      {p.external_url && <a className="secondaryLink mkSmallText" href={p.external_url} target="_blank" rel="noreferrer"><ExternalLink aria-hidden="true" />{t('mkHub.top.open', { platform: platformName(p.platform) })}</a>}
    </li>)}</ol>}
  </FinCard>;
}

function ChannelsCard({ accounts, canManage, onGo }: { accounts: Account[]; canManage: boolean; onGo: (tab: MarketingTab) => void }) {
  const live = accounts.filter((a) => a.status !== 'revoked');
  return <FinCard title={t('mkHub.channels.title')} icon={<Plug />} actions={<button type="button" className="secondary mkSmall" onClick={() => onGo('accounts')}>{canManage ? t('mkHub.channels.manage') : t('mkHub.channels.view')}</button>}>
    {live.length === 0 ? <p className="muted">{canManage ? t('mkHub.channels.noneOwner') : t('mkHub.channels.noneMember')}</p>
      : <ul className="mkChannels">{live.map((a) => <li key={a.id}>
        <span className={`mkPlatform ${a.platform}`}>{platformName(a.platform)}</span><span dir="auto" className="mkMiniText">{a.display_name}</span>
        <StatusChip tone={a.status === 'connected' ? 'ok' : a.status === 'needs_reauth' ? 'warn' : 'bad'}>{t(`marketing.accountStatus.${a.status}` as MessageKey)}</StatusChip>
      </li>)}</ul>}
  </FinCard>;
}

// ---- audience -------------------------------------------------------------------------------------------

type AudienceLoad = { kind: 'loading' } | { kind: 'denied' } | { kind: 'error'; message: string } | { kind: 'ready'; status: BrevoStatus; lists: ContactList[]; total: number };
const LIST_PAGE = 50;

/** Audience: the Brevo contact lists email campaigns go to, with their sizes and blocked contacts. */
export function AudienceTab({ onGo }: { onGo: (tab: MarketingTab) => void }) {
  const [load, setLoad] = useState<AudienceLoad>({ kind: 'loading' });
  const [query, setQuery] = useState('');
  const [more, setMore] = useState(false);
  const searchId = useId();
  const fetchFirst = useCallback(async () => {
    setLoad({ kind: 'loading' });
    try {
      const status = await b.getStatus();
      if (!status.connected) { setLoad({ kind: 'ready', status, lists: [], total: 0 }); return; }
      const page = await b.listLists(LIST_PAGE, 0);
      setLoad({ kind: 'ready', status, lists: page.items, total: page.total });
    } catch (e) { setLoad(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', message: describeError(e) }); }
  }, []);
  useEffect(() => { void fetchFirst(); }, [fetchFirst]);
  const loadMore = async () => {
    if (load.kind !== 'ready') return;
    setMore(true);
    try { const page = await b.listLists(LIST_PAGE, load.lists.length); setLoad({ ...load, lists: [...load.lists, ...page.items], total: page.total }); }
    catch (e) { setLoad({ kind: 'error', message: describeError(e) }); } finally { setMore(false); }
  };
  if (load.kind === 'loading') return <FinCard title={t('mkHub.audience.title')} icon={<Users />}><Notice busy>{t('common.loading')}</Notice></FinCard>;
  if (load.kind === 'denied') return <FinCard title={t('mkHub.audience.title')} icon={<Users />}><Notice>{t('mkHub.audience.denied')}</Notice></FinCard>;
  if (load.kind === 'error') return <FinCard title={t('mkHub.audience.title')} icon={<Users />}><LoadError message={t('mkHub.audience.failed', { error: load.message })} retry={() => void fetchFirst()} /></FinCard>;
  if (!load.status.connected) {
    return <FinCard title={t('mkHub.audience.title')} icon={<Users />}>
      <Empty icon={<Users aria-hidden="true" />} title={t('mkHub.audience.notConnectedTitle')} text={load.status.can_manage ? t('mkHub.audience.notConnectedOwner') : t('mkHub.audience.notConnectedMember')}
        action={<button type="button" onClick={() => onGo('email')}>{t('mkHub.audience.openEmail')}</button>} />
    </FinCard>;
  }
  const totals = audienceTotals(load.lists);
  const words = query.trim().toLowerCase();
  const shown = words ? load.lists.filter((l) => l.name.toLowerCase().includes(words)) : load.lists;
  return <div className="mkAudience">
    <KpiRow label={t('mkHub.audience.kpis')}>
      <KpiCard icon={<Users />} label={t('mkHub.audience.kpi.lists')} value={formatNumber(load.total)} hint={load.lists.length < load.total ? t('mkHub.audience.kpi.loaded', { count: load.lists.length }) : undefined} />
      <KpiCard icon={<Send />} label={t('mkHub.audience.kpi.reachable')} value={formatNumber(totals.subscribers)} hint={t('mkHub.audience.kpi.reachableHint')} />
      <KpiCard icon={<ShieldAlert />} label={t('mkHub.audience.kpi.blocked')} value={formatNumber(totals.blocked)} hint={t('mkHub.audience.kpi.blockedHint')} tone={totals.blocked ? 'warn' : ''} />
      <KpiCard icon={<TrendingUp />} label={t('mkHub.audience.kpi.largest')} value={totals.largest ? <span dir="auto">{totals.largest.name}</span> : '-'}
        hint={totals.largest ? t('mkHub.audience.kpi.largestHint', { count: totals.largest.subscribers }) : undefined} />
    </KpiRow>
    <FinCard title={t('mkHub.audience.title')} icon={<Users />} subtitle={t('mkHub.audience.sub')}
      actions={<button type="button" className="secondary mkSmall" onClick={() => onGo('email')}>{t('mkHub.audience.openEmail')}</button>}>
      {load.lists.length === 0 ? <Empty icon={<Users aria-hidden="true" />} title={t('mkHub.audience.emptyTitle')} text={t('mkHub.audience.emptyText')} />
        : <>
          <label className="mkAudienceSearch" htmlFor={searchId}><span className="visuallyHidden">{t('mkHub.audience.search')}</span><Search aria-hidden="true" />
            <input id={searchId} type="search" value={query} placeholder={t('mkHub.audience.search')} onChange={(e) => setQuery(e.target.value)} /></label>
          {shown.length === 0 ? <p className="muted">{t('mkHub.audience.noMatch')}</p>
            : <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('mkHub.audience.title')}>
              <table className="finGrid mkHubTable">
                <thead><tr><th scope="col">{t('mkHub.col.list')}</th><th scope="col" className="num">{t('mkHub.col.contacts')}</th><th scope="col" className="num">{t('mkHub.col.blocked')}</th><th scope="col">{t('mkHub.col.reachable')}</th></tr></thead>
                <tbody>{shown.map((list) => {
                  const pct = reachablePercent(list);
                  return <tr key={list.id} data-list-row={list.id}>
                    <th scope="row" dir="auto">{list.name}</th>
                    <td className="num">{formatNumber(list.subscribers)}</td>
                    <td className="num">{formatNumber(list.blacklisted)}</td>
                    <td>{pct === null ? '-' : <span className="mkBar"><span className="mkBarTrack" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span><span>{t('mkHub.pct', { pct })}</span></span>}</td>
                  </tr>;
                })}</tbody>
              </table>
            </div>}
          {load.lists.length < load.total && <div className="actions"><button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{more ? t('common.loading') : t('mkHub.audience.more')}</button></div>}
        </>}
    </FinCard>
  </div>;
}
