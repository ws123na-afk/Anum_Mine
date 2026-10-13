// Home, the owner's overview (docs/home.md#the-web-home): what needs the person today. A greeting,
// today at a glance (approvals with Approve and Deny, tasks, unread inbox and WhatsApp, today's
// meetings, automation alerts, budget use), Today's focus, what ANUM noticed, recent activity, pinned
// apps and quick links into every area. Giving orders is the ANUM Command page; Home links to it.
// Every number comes from the workspace's API (lib/homeApi.ts and the shell's tasks and approvals).
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArrowDown, ArrowRight, ArrowUp, BellRing, Bot, CalendarDays, CheckCircle2, ExternalLink, FileText, Inbox, LayoutGrid, Lightbulb, ListTodo,
  Mail, MessageCircle, MessageSquare, Mic, Pin as PinIcon, Plus, RefreshCw, ShieldCheck, Sparkles, Trash2, Wallet, Workflow, X, XCircle,
} from 'lucide-react';
import type { Approval, ModelBudgetOverview, Task } from '@anum/contracts';
import type { AutomationAlert } from './lib/automation';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { riskLabel, statusLabel } from './lib/labels';
import { canApprove, shortHash } from './lib/approvals';
import { relativeTime } from './lib/commandCenter';
import { commandTone } from './lib/commands';
import { listEmailAccounts } from './lib/emailApi';
import {
  PINNABLE_FEATURES, QUICK_LINKS, budgetUse, dayPart, greetingName, movePin, page, pendingApprovals, sourceState, taskGlance, todaysMeetings,
  validLink, viewForFeature, type CalendarEvent, type FocusItem, type HomeSummary, type HomeTarget, type Insight, type Pin, type PinKind,
} from './lib/home';
import * as homeApi from './lib/homeApi';
import type { View } from './lib/shell';
import { viewLabel } from './lib/shell';
import { EmptyPanel, PanelDenied, PanelError, PanelLoading, ShowMore } from './Panels';
import './commandCenter.css';
import './home.css';

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied' } | { state: 'error'; message: string };
const settle = <T,>(set: (value: Load<T>) => void, promise: Promise<T>) => {
  set({ state: 'loading' });
  promise.then((value) => set({ state: 'ready', value })).catch((e: unknown) => set(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) }));
};

type Decide = (approval: Approval, decision: 'approve' | 'reject', reason?: string) => Promise<string | null>;

export interface HomeViewProps {
  tasks: readonly Task[]; approvals: readonly Approval[]; loading: boolean; userName: string | null; owner: boolean | null;
  alerts: readonly AutomationAlert[]; busy: boolean; decide: Decide;
  /** Bumped by the shell on workspace events, so Home re-reads its parts. */
  live: number;
  onGo: (view: View) => void; onOpenTask: (taskId: string) => void; onOpenAlert: (alert: AutomationAlert) => void;
  /** Hand a request to ANUM Command, which shows its plan before anything runs. */
  onCommand: (text: string) => void;
  onSwitchWorkspace: (workspaceId: string) => void;
}

const FOCUS_ICON: Record<string, ReactNode> = { approvals: <ShieldCheck />, email: <Mail />, tasks: <ListTodo />, automations: <Workflow />, meetings: <CalendarDays /> };
const LINK_ICON: Partial<Record<View, ReactNode>> = {
  command: <Sparkles />, inbox: <Inbox />, whatsapp: <MessageCircle />, tasks: <ListTodo />, approvals: <ShieldCheck />, meetings: <CalendarDays />,
  automation: <Workflow />, reports: <FileText />, files: <FileText />, agents: <Bot />, voice: <Mic />, assistant: <MessageSquare />,
};

export function HomeView({ tasks, approvals, loading, userName, owner, alerts, busy, decide, live, onGo, onOpenTask, onOpenAlert, onCommand, onSwitchWorkspace }: HomeViewProps) {
  const [summary, setSummary] = useState<Load<HomeSummary>>({ state: 'loading' });
  const [insights, setInsights] = useState<Load<{ items: Insight[]; hidden: number }>>({ state: 'loading' });
  const [pins, setPins] = useState<Load<Pin[]>>({ state: 'loading' });
  const [email, setEmail] = useState<Load<{ unread: number; mailboxes: number }>>({ state: 'loading' });
  const [whatsapp, setWhatsapp] = useState<Load<{ chats: number; messages: number; more: boolean }>>({ state: 'loading' });
  const [meetings, setMeetings] = useState<Load<CalendarEvent[]>>({ state: 'loading' });
  const [budget, setBudget] = useState<Load<ModelBudgetOverview>>({ state: 'loading' });
  const [now, setNow] = useState(() => new Date());

  const loadSummary = useCallback(() => settle(setSummary, homeApi.getSummary()), []);
  const loadInsights = useCallback(() => settle(setInsights, homeApi.getInsights()), []);
  const loadPins = useCallback(() => settle(setPins, homeApi.listPins()), []);
  const loadEmail = useCallback(() => settle(setEmail, listEmailAccounts().then((a) => ({ mailboxes: a.length, unread: a.reduce((sum, x) => sum + x.unread_count, 0) }))), []);
  const loadWhatsapp = useCallback(() => settle(setWhatsapp, homeApi.whatsappUnread()), []);
  const loadMeetings = useCallback(() => settle(setMeetings, homeApi.todaysEvents(new Date()).then((page) => todaysMeetings(page.items, new Date()))), []);
  const loadBudget = useCallback(() => {
    if (owner === false) { setBudget({ state: 'denied' }); return; }
    if (owner === null) return;
    settle(setBudget, homeApi.budgets());
  }, [owner]);
  const loadAll = useCallback(() => { setNow(new Date()); loadSummary(); loadInsights(); loadPins(); loadEmail(); loadWhatsapp(); loadMeetings(); }, [loadSummary, loadInsights, loadPins, loadEmail, loadWhatsapp, loadMeetings]);
  useEffect(loadAll, [loadAll]);
  useEffect(loadBudget, [loadBudget]);
  // Workspace events (tasks, approvals, automations) change the summary and the insights: re-read them quietly.
  useEffect(() => {
    if (live === 0) return;
    void homeApi.getSummary().then((value) => setSummary({ state: 'ready', value })).catch(() => undefined);
    void homeApi.getInsights().then((value) => setInsights({ state: 'ready', value })).catch(() => undefined);
  }, [live]);

  const name = greetingName(summary.state === 'ready' ? summary.value.profile.display_name : null, userName);
  const greeting = t(`home.greeting.${dayPart(now)}` as MessageKey, { name: name ?? '' });
  const waiting = useMemo(() => pendingApprovals(approvals, now), [approvals, now]);
  const glance = useMemo(() => taskGlance(tasks), [tasks]);
  const failedRuns = summary.state === 'ready' ? summary.value.counts.automations ?? 0 : 0;

  return <div className="homePage">
    <header className="homeHeader">
      <div>
        <p className="eyebrow">{formatDateTime(now, { weekday: 'long', day: 'numeric', month: 'long' })}</p>
        <h1 id="view-title">{name ? greeting : t(`home.greetingUnnamed.${dayPart(now)}` as MessageKey)}</h1>
        <p className="homeLead">{t('home.lead')}</p>
      </div>
      <div className="homeHeaderActions">
        <button type="button" className="secondary smallButton" onClick={() => { loadAll(); loadBudget(); }}><RefreshCw aria-hidden="true" />{t('home.refresh')}</button>
        <button type="button" className="primaryButton" onClick={() => onGo('command')}><Sparkles aria-hidden="true" />{t('home.giveOrder')}</button>
      </div>
    </header>

    <section className="glance" aria-labelledby="glance-title">
      <h2 id="glance-title" className="sectionTitle">{t('home.glance.title')}</h2>
      <ul className="glanceGrid">
        <Glance icon={<ShieldCheck />} tone={waiting.length ? 'warning' : 'ok'} label={t('home.glance.approvals')} value={loading && !approvals.length ? null : String(waiting.length)}
          detail={waiting.length ? t('home.glance.approvalsDetail', { count: waiting.length }) : t('home.glance.approvalsNone')} onOpen={() => onGo('approvals')} />
        <Glance icon={<ListTodo />} tone={glance.failed ? 'danger' : glance.running || glance.waiting ? 'info' : 'ok'} label={t('home.glance.tasks')}
          value={loading && !tasks.length ? null : String(glance.running)} unit={t('home.glance.running')}
          detail={t('home.glance.tasksDetail', { failed: glance.failed, waiting: glance.waiting, queued: glance.queued })} onOpen={() => onGo('tasks')} />
        <Glance icon={<Inbox />} tone={email.state === 'ready' && email.value.unread ? 'info' : 'ok'} label={t('home.glance.inbox')}
          value={email.state === 'ready' ? String(email.value.unread) : email.state === 'loading' ? null : '-'}
          detail={email.state === 'ready' ? (email.value.mailboxes ? t('home.glance.inboxDetail', { count: email.value.mailboxes }) : t('home.glance.inboxNone')) : email.state === 'denied' ? t('home.glance.notForRole') : email.state === 'error' ? t('home.glance.couldNotLoad') : ''}
          onOpen={() => onGo('inbox')} />
        <Glance icon={<MessageCircle />} tone={whatsapp.state === 'ready' && whatsapp.value.chats ? 'info' : 'ok'} label={t('home.glance.whatsapp')}
          value={whatsapp.state === 'ready' ? `${whatsapp.value.messages}${whatsapp.value.more ? '+' : ''}` : whatsapp.state === 'loading' ? null : '-'}
          detail={whatsapp.state === 'ready' ? t('home.glance.whatsappDetail', { count: whatsapp.value.chats }) : whatsapp.state === 'denied' ? t('home.glance.notForRole') : whatsapp.state === 'error' ? t('home.glance.whatsappUnavailable') : ''}
          onOpen={() => onGo('whatsapp')} />
        <Glance icon={<CalendarDays />} tone="info" label={t('home.glance.meetings')}
          value={meetings.state === 'ready' ? String(meetings.value.length) : meetings.state === 'loading' ? null : '-'}
          detail={meetings.state === 'ready' ? nextMeetingLine(meetings.value, now) : meetings.state === 'denied' ? t('home.glance.notForRole') : meetings.state === 'error' ? t('home.glance.couldNotLoad') : ''}
          onOpen={() => onGo('meetings')} />
        <Glance icon={<BellRing />} tone={alerts.length || failedRuns ? 'danger' : 'ok'} label={t('home.glance.automations')}
          value={summary.state === 'loading' ? null : String(alerts.length + failedRuns)}
          detail={alerts.length + failedRuns ? t('home.glance.automationsDetail', { count: alerts.length + failedRuns }) : t('home.glance.automationsNone')} onOpen={() => onGo('automation')} />
        <BudgetGlance budget={budget} onOpen={() => onGo('budgets')} />
      </ul>
    </section>

    <div className="homeGrid">
      <div className="homeColumn">
        <ApprovalsPanel waiting={waiting} busy={busy} decide={decide} loading={loading && !approvals.length} onOpenAll={() => onGo('approvals')} onOpenTask={onOpenTask} />
        <FocusPanel summary={summary} onRetry={loadSummary} onOpen={(target) => openTarget(target, onGo, onOpenTask)} onCommand={() => onGo('command')} />
        <ActivityPanel summary={summary} onRetry={loadSummary} onOpen={(target) => openTarget(target, onGo, onOpenTask)} onGo={onGo} />
      </div>
      <div className="homeColumn">
        <MeetingsPanel meetings={meetings} now={now} onRetry={loadMeetings} onGo={onGo} />
        <AlertsPanel alerts={alerts} summary={summary} onOpenAlert={onOpenAlert} onGo={onGo} onOpenTarget={(target) => openTarget(target, onGo, onOpenTask)} />
        <InsightsPanel insights={insights} onRetry={loadInsights} onChanged={loadInsights}
          onOpen={(target) => openTarget(target, onGo, onOpenTask)} onCommand={onCommand} />
      </div>
    </div>

    <PinsPanel pins={pins} onRetry={loadPins} onChanged={setPins} onGo={onGo} onSwitchWorkspace={onSwitchWorkspace} />

    <nav className="quickLinks" aria-labelledby="links-title">
      <h2 id="links-title" className="sectionTitle">{t('home.links.title')}</h2>
      <ul>{QUICK_LINKS.map((view) => <li key={view}><button type="button" className="quickLink" onClick={() => onGo(view)}>
        <span className="quickLinkIcon" aria-hidden="true">{LINK_ICON[view] ?? <LayoutGrid />}</span><span>{viewLabel(view)}</span><ArrowRight className="flipRtl" aria-hidden="true" />
      </button></li>)}</ul>
    </nav>
  </div>;
}

function openTarget(target: HomeTarget, onGo: (view: View) => void, onOpenTask: (id: string) => void) {
  if (target.feature === 'tasks' && target.id) { onOpenTask(target.id); return; }
  const view = viewForFeature(target.feature);
  if (view) onGo(view);
}

function nextMeetingLine(events: readonly CalendarEvent[], now: Date): string {
  const next = events.find((event) => new Date(event.ends_at) > now);
  if (!next) return events.length ? t('home.glance.meetingsDone') : t('home.glance.meetingsNone');
  return t('home.glance.meetingsNext', { time: formatDateTime(next.starts_at, { timeStyle: 'short' }) });
}

function Glance({ icon, tone, label, value, unit, detail, onOpen }: { icon: ReactNode; tone: 'ok' | 'info' | 'warning' | 'danger'; label: string; value: string | null; unit?: string; detail: string; onOpen: () => void }) {
  return <li><button type="button" className={`glanceCard ${tone}`} onClick={onOpen}>
    <span className="glanceIcon" aria-hidden="true">{icon}</span>
    <span className="glanceLabel">{label}</span>
    <span className="glanceValue">{value === null ? <span className="skeletonRow tiny" aria-hidden="true" /> : <>{value}{unit && <small>{unit}</small>}</>}{value === null && <span className="visuallyHidden">{t('common.loading')}</span>}</span>
    <span className="glanceDetail">{detail}</span>
  </button></li>;
}

function BudgetGlance({ budget, onOpen }: { budget: Load<ModelBudgetOverview>; onOpen: () => void }) {
  if (budget.state !== 'ready') {
    const detail = budget.state === 'denied' ? t('home.budget.ownersOnly') : budget.state === 'error' ? t('home.glance.couldNotLoad') : '';
    return <Glance icon={<Wallet />} tone="ok" label={t('home.glance.budget')} value={budget.state === 'loading' ? null : '-'} detail={detail} onOpen={onOpen} />;
  }
  const use = budgetUse(budget.value);
  const tone = use.exceeded || (use.percent ?? 0) >= 100 ? 'danger' : (use.percent ?? 0) >= 80 ? 'warning' : 'ok';
  const spent = t('home.budget.spent', { cost: `$${use.costUsd.toFixed(2)}`, tokens: formatNumber(use.tokens) });
  return <Glance icon={<Wallet />} tone={tone} label={t('home.glance.budget')} value={use.percent === null ? t('home.budget.noLimit') : `${use.percent}%`}
    detail={use.percent === null ? spent : `${t(use.scope === 'tenant' ? 'home.budget.ofOrganisation' : 'home.budget.ofWorkspace')} · ${spent}`} onOpen={onOpen} />;
}

function ApprovalsPanel({ waiting, busy, decide, loading, onOpenAll, onOpenTask }: { waiting: Approval[]; busy: boolean; decide: Decide; loading: boolean; onOpenAll: () => void; onOpenTask: (id: string) => void }) {
  const [shown, setShown] = useState(3);
  const [refusals, setRefusals] = useState<Record<string, string>>({});
  const visible = page(waiting, shown);
  const act = (approval: Approval, decision: 'approve' | 'reject') => {
    setRefusals((r) => { const next = { ...r }; delete next[approval.id]; return next; });
    void decide(approval, decision).then((refusal) => { if (refusal) setRefusals((r) => ({ ...r, [approval.id]: refusal })); });
  };
  return <section className="surface commandPanel" aria-labelledby="home-approvals-title">
    <div className="panelHead"><h2 id="home-approvals-title">{t('home.approvals.title')} <span className="countPill">{waiting.length}</span></h2><button type="button" className="linkButton" onClick={onOpenAll}>{t('home.viewAll')}</button></div>
    {loading ? <PanelLoading /> : waiting.length === 0
      ? <EmptyPanel icon={<CheckCircle2 />} title={t('home.approvals.emptyTitle')} text={t('home.approvals.emptyText')} action={t('home.approvals.emptyAction')} onAction={onOpenAll} />
      : <ul className="approvalCards">{visible.items.map((approval) => {
        const approvable = canApprove(approval);
        return <li key={approval.id} className="approvalCard">
          <div className="approvalCardTop"><code dir="ltr">{approval.action}</code><span className={`riskBadge risk-${approval.riskLevel}`}>{riskLabel(approval.riskLevel)}</span></div>
          <p dir="auto">{approval.reason}</p>
          <small className="approvalCardMeta">{t('home.approvals.meta', { when: relativeTime(approval.createdAt), hash: shortHash(approval.payloadHash) })}</small>
          {!approval.payloadHash && <p className="approvalWarning">{t('approvals.notBoundWarning')}</p>}
          {refusals[approval.id] && <p className="approvalRefusalLine" role="alert">{refusals[approval.id]}</p>}
          <div className="approvalCardActions">
            <button type="button" className="primaryButton smallButton" disabled={busy || !approvable} onClick={() => act(approval, 'approve')}><CheckCircle2 aria-hidden="true" />{t('home.approvals.approve')}</button>
            <button type="button" className="secondary smallButton" disabled={busy} onClick={() => act(approval, 'reject')}><XCircle aria-hidden="true" />{t('home.approvals.deny')}</button>
            <button type="button" className="linkButton" onClick={() => onOpenTask(approval.taskId)}>{t('home.approvals.openTask')}</button>
          </div>
        </li>;
      })}</ul>}
    <ShowMore more={visible.more} onMore={() => setShown((n) => n + 3)} />
  </section>;
}

function FocusPanel({ summary, onRetry, onOpen, onCommand }: { summary: Load<HomeSummary>; onRetry: () => void; onOpen: (target: HomeTarget) => void; onCommand: () => void }) {
  const [shown, setShown] = useState(5);
  const items = summary.state === 'ready' ? summary.value.focus : [];
  const visible = page(items, shown);
  const failing = summary.state === 'ready' ? summary.value.sources.filter((s) => s.status === 'unavailable') : [];
  const progress = summary.state === 'ready' ? summary.value.progress : null;
  return <section className="surface commandPanel" aria-labelledby="home-focus-title">
    <div className="panelHead"><h2 id="home-focus-title">{t('home.focus.title')}</h2>{progress && <span className="activePill">{t('home.focus.progress', { done: progress.completed, open: progress.open })}</span>}</div>
    {summary.state === 'loading' && <PanelLoading />}
    {summary.state === 'error' && <PanelError message={t('home.summaryFailed', { reason: summary.message })} onRetry={onRetry} />}
    {summary.state === 'denied' && <PanelDenied title={t('home.denied.title')} text={t('home.denied.text')} />}
    {failing.length > 0 && <p className="partNote" role="status">{t('home.focus.partFailed', { parts: failing.map((s) => t(`home.source.${s.name}` as MessageKey)).join(', ') })} <button type="button" className="linkButton" onClick={onRetry}>{t('common.retry')}</button></p>}
    {summary.state === 'ready' && items.length === 0 && <EmptyPanel icon={<CheckCircle2 />} title={t('home.focus.emptyTitle')} text={t('home.focus.emptyText')} action={t('home.giveOrder')} onAction={onCommand} />}
    {visible.items.length > 0 && <ul className="rowList light">{visible.items.map((item) => <li key={item.id}><FocusRow item={item} onOpen={onOpen} /></li>)}</ul>}
    <ShowMore more={visible.more} onMore={() => setShown((n) => n + 5)} />
  </section>;
}

function FocusRow({ item, onOpen }: { item: FocusItem; onOpen: (target: HomeTarget) => void }) {
  const tone = item.severity === 'high' ? 'dangerTone' : item.severity === 'medium' ? 'warning' : 'neutral';
  return <button type="button" className="listRow" onClick={() => onOpen(item.target)}>
    <span className={`rowIcon ${tone}`} aria-hidden="true">{FOCUS_ICON[item.source] ?? <Lightbulb />}</span>
    <span className="rowText"><strong dir="auto">{item.title}</strong>{item.detail && <small dir="auto">{item.detail}</small>}
      {item.progress !== null && item.progress !== undefined && <span className="progress mini" role="progressbar" aria-label={t('home.focus.progressLabel', { title: item.title })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(item.progress * 100)}><span style={{ width: `${Math.round(item.progress * 100)}%` }} /></span>}
    </span>
    <span className={`statusBadge ${tone}`}>{t(`home.severity.${item.severity}` as MessageKey)}</span>
  </button>;
}

function ActivityPanel({ summary, onRetry, onOpen, onGo }: { summary: Load<HomeSummary>; onRetry: () => void; onOpen: (target: HomeTarget) => void; onGo: (view: View) => void }) {
  const [shown, setShown] = useState(6);
  const items = summary.state === 'ready' ? summary.value.activity : [];
  const visible = page(items, shown);
  return <section className="surface commandPanel" aria-labelledby="home-activity-title">
    <div className="panelHead"><h2 id="home-activity-title">{t('home.activity.title')}</h2><button type="button" className="linkButton" onClick={() => onGo('activity')}>{t('home.viewAll')}</button></div>
    {summary.state === 'loading' && <PanelLoading />}
    {summary.state === 'error' && <PanelError message={t('home.summaryFailed', { reason: summary.message })} onRetry={onRetry} />}
    {summary.state === 'ready' && items.length === 0 && <EmptyPanel icon={<ListTodo />} title={t('home.activity.emptyTitle')} text={t('home.activity.emptyText')} action={t('home.giveOrder')} onAction={() => onGo('command')} />}
    {visible.items.length > 0 && <ul className="rowList light">{visible.items.map((item) => <li key={item.id}><button type="button" className="listRow" onClick={() => onOpen(item.target)}>
      <span className={`rowIcon ${commandTone(item.status)}`} aria-hidden="true">{item.kind.startsWith('automation') ? <Workflow /> : <ListTodo />}</span>
      <span className="rowText"><strong dir="auto">{item.title}</strong><small>{relativeTime(item.at)}</small></span>
      <span className={`statusBadge ${commandTone(item.status)}`}>{statusLabel(item.status)}</span>
    </button></li>)}</ul>}
    <ShowMore more={visible.more} onMore={() => setShown((n) => n + 6)} />
  </section>;
}

function MeetingsPanel({ meetings, now, onRetry, onGo }: { meetings: Load<CalendarEvent[]>; now: Date; onRetry: () => void; onGo: (view: View) => void }) {
  const [shown, setShown] = useState(4);
  const items = meetings.state === 'ready' ? meetings.value : [];
  const visible = page(items, shown);
  return <section className="surface commandPanel" aria-labelledby="home-meetings-title">
    <div className="panelHead"><h2 id="home-meetings-title">{t('home.meetings.title')}</h2><button type="button" className="linkButton" onClick={() => onGo('meetings')}>{t('home.viewAll')}</button></div>
    {meetings.state === 'loading' && <PanelLoading />}
    {meetings.state === 'error' && <PanelError message={t('home.meetings.failed', { reason: meetings.message })} onRetry={onRetry} />}
    {meetings.state === 'denied' && <PanelDenied title={t('home.meetings.deniedTitle')} text={t('home.meetings.deniedText')} />}
    {meetings.state === 'ready' && items.length === 0 && <EmptyPanel icon={<CalendarDays />} title={t('home.meetings.emptyTitle')} text={t('home.meetings.emptyText')} action={t('home.meetings.emptyAction')} onAction={() => onGo('meetings')} />}
    {visible.items.length > 0 && <ul className="meetingList">{visible.items.map((event) => {
      const done = new Date(event.ends_at) <= now, current = !done && new Date(event.starts_at) <= now;
      return <li key={event.id} className={done ? 'done' : ''}>
        <span className="meetingTime"><bdi>{formatDateTime(event.starts_at, { timeStyle: 'short' })}</bdi><small><bdi>{formatDateTime(event.ends_at, { timeStyle: 'short' })}</bdi></small></span>
        <span className="meetingBody"><strong dir="auto">{event.title}</strong>{event.location && <small dir="auto">{event.location}</small>}
          {current && <span className="statusBadge warning">{t('home.meetings.now')}</span>}{done && <span className="statusBadge neutral">{t('home.meetings.done')}</span>}</span>
        {event.conference_url && !done && <a className="secondary smallButton joinLink" href={event.conference_url} target="_blank" rel="noopener noreferrer">{t('home.meetings.join')}<ExternalLink aria-hidden="true" /><span className="visuallyHidden">{t('home.meetings.joinNamed', { title: event.title })}</span></a>}
      </li>;
    })}</ul>}
    <ShowMore more={visible.more} onMore={() => setShown((n) => n + 4)} />
  </section>;
}

function AlertsPanel({ alerts, summary, onOpenAlert, onGo, onOpenTarget }: { alerts: readonly AutomationAlert[]; summary: Load<HomeSummary>; onOpenAlert: (alert: AutomationAlert) => void; onGo: (view: View) => void; onOpenTarget: (target: HomeTarget) => void }) {
  const failed = summary.state === 'ready' ? summary.value.focus.filter((item) => item.source === 'automations') : [];
  const state = summary.state === 'ready' ? sourceState(summary.value.sources, 'automations') : null;
  const empty = alerts.length === 0 && failed.length === 0;
  return <section className="surface commandPanel" aria-labelledby="home-alerts-title">
    <div className="panelHead"><h2 id="home-alerts-title">{t('home.alerts.title')}</h2><button type="button" className="linkButton" onClick={() => onGo('automation')}>{t('home.viewAll')}</button></div>
    {summary.state === 'loading' && alerts.length === 0 && <PanelLoading />}
    {state === 'not_permitted' && <PanelDenied title={t('home.alerts.deniedTitle')} text={t('home.alerts.deniedText')} />}
    {summary.state !== 'loading' && state !== 'not_permitted' && empty && <EmptyPanel icon={<CheckCircle2 />} title={t('home.alerts.emptyTitle')} text={t('home.alerts.emptyText')} action={t('home.alerts.emptyAction')} onAction={() => onGo('automation')} />}
    {!empty && <ul className="rowList light">
      {alerts.map((alert) => <li key={alert.id}><button type="button" className="listRow" onClick={() => onOpenAlert(alert)}>
        <span className={`rowIcon ${alert.kind === 'failed' ? 'dangerTone' : 'warning'}`} aria-hidden="true"><BellRing /></span>
        <span className="rowText"><strong dir="auto">{alert.title}</strong><small dir="auto">{alert.message}</small></span>
        <span className={`statusBadge ${alert.kind === 'failed' ? 'dangerTone' : 'warning'}`}>{t(alert.kind === 'failed' ? 'home.alerts.failed' : 'home.alerts.notice')}</span>
      </button></li>)}
      {failed.map((item) => <li key={item.id}><FocusRow item={item} onOpen={onOpenTarget} /></li>)}
    </ul>}
  </section>;
}

function InsightsPanel({ insights, onRetry, onChanged, onOpen, onCommand }: { insights: Load<{ items: Insight[]; hidden: number }>; onRetry: () => void; onChanged: () => void; onOpen: (target: HomeTarget) => void; onCommand: (text: string) => void }) {
  const [shown, setShown] = useState(3);
  const [error, setError] = useState<string | null>(null);
  const items = insights.state === 'ready' ? insights.value.items.filter((i) => i.state === 'new') : [];
  const visible = page(items, shown);
  const hide = (key: string, how: 'dismiss' | 'snooze') => {
    setError(null);
    (how === 'dismiss' ? homeApi.dismissInsight(key) : homeApi.snoozeInsight(key)).then(onChanged).catch((e: unknown) => setError(describeError(e)));
  };
  const act = (insight: Insight) => {
    if (insight.action.type === 'command' && insight.action.command) onCommand(insight.action.command);
    else if (insight.action.target) onOpen(insight.action.target);
  };
  return <section className="surface commandPanel" aria-labelledby="home-insights-title">
    <div className="panelHead"><h2 id="home-insights-title">{t('home.insights.title')}</h2>{insights.state === 'ready' && insights.value.hidden > 0 && <span className="countPill" title={t('home.insights.hiddenHint')}>{t('home.insights.hidden', { count: insights.value.hidden })}</span>}</div>
    {insights.state === 'loading' && <PanelLoading />}
    {insights.state === 'error' && <PanelError message={t('home.insights.failed', { reason: insights.message })} onRetry={onRetry} />}
    {insights.state === 'denied' && <PanelDenied title={t('home.denied.title')} text={t('home.denied.text')} />}
    {error && <PanelError message={error} />}
    {insights.state === 'ready' && items.length === 0 && <EmptyPanel icon={<Lightbulb />} title={t('home.insights.emptyTitle')} text={t('home.insights.emptyText')} />}
    {visible.items.length > 0 && <ul className="insightList">{visible.items.map((insight) => <li key={insight.key} className={`insight ${insight.severity}`}>
      <strong dir="auto">{insight.title}</strong>
      <p dir="auto">{insight.finding}</p>
      <div className="insightActions">
        <button type="button" className="primaryButton smallButton" onClick={() => act(insight)}>{t(`home.insights.verb.${insight.action.verb}` as MessageKey)}</button>
        <button type="button" className="secondary smallButton" onClick={() => hide(insight.key, 'snooze')}>{t('home.insights.later')}</button>
        <button type="button" className="iconButton small" aria-label={t('home.insights.dismissNamed', { title: insight.title })} title={t('home.insights.dismiss')} onClick={() => hide(insight.key, 'dismiss')}><X aria-hidden="true" /></button>
      </div>
    </li>)}</ul>}
    <ShowMore more={visible.more} onMore={() => setShown((n) => n + 3)} />
  </section>;
}

function PinsPanel({ pins, onRetry, onChanged, onGo, onSwitchWorkspace }: { pins: Load<Pin[]>; onRetry: () => void; onChanged: (pins: Load<Pin[]>) => void; onGo: (view: View) => void; onSwitchWorkspace: (id: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const items = pins.state === 'ready' ? pins.value : [];
  const open = (pin: Pin) => {
    if (pin.kind === 'feature') { const view = viewForFeature(pin.target); if (view) onGo(view); }
    else if (pin.kind === 'workspace') onSwitchWorkspace(pin.target);
    else window.open(pin.target, '_blank', 'noopener,noreferrer');
  };
  const run = (fn: () => Promise<Pin[]>) => {
    setSaving(true); setError(null);
    fn().then((value) => onChanged({ state: 'ready', value })).catch((e: unknown) => setError(describeError(e))).finally(() => setSaving(false));
  };
  const move = (id: string, by: -1 | 1) => { const ids = movePin(items.map((p) => p.id), id, by); if (ids) run(() => homeApi.reorderPins(ids)); };
  const remove = (id: string) => run(async () => { await homeApi.removePin(id); return items.filter((p) => p.id !== id); });
  const add = (pin: { kind: PinKind; label: string; target: string }) => run(async () => [...items, await homeApi.addPin(pin)]);
  return <section className="surface commandPanel pinsPanel" aria-labelledby="home-pins-title">
    <div className="panelHead"><h2 id="home-pins-title"><PinIcon aria-hidden="true" />{t('home.pins.title')}</h2>
      {pins.state === 'ready' && <button type="button" className="linkButton" aria-pressed={editing} onClick={() => setEditing((e) => !e)}>{editing ? t('home.pins.done') : t('home.pins.edit')}</button>}</div>
    {pins.state === 'loading' && <PanelLoading />}
    {pins.state === 'error' && <PanelError message={t('home.pins.failed', { reason: pins.message })} onRetry={onRetry} />}
    {pins.state === 'denied' && <PanelDenied title={t('home.denied.title')} text={t('home.denied.text')} />}
    {error && <PanelError message={error} />}
    {pins.state === 'ready' && items.length === 0 && !editing && <EmptyPanel icon={<PinIcon />} title={t('home.pins.emptyTitle')} text={t('home.pins.emptyText')} action={t('home.pins.add')} onAction={() => setEditing(true)} />}
    {items.length > 0 && <ul className="pinList">{items.map((pin, index) => <li key={pin.id} className="pinItem">
      <button type="button" className="pinChip" onClick={() => open(pin)}>{pin.kind === 'link' ? <ExternalLink aria-hidden="true" /> : <PinIcon aria-hidden="true" />}<span dir="auto">{pin.label}</span></button>
      {editing && <span className="pinTools">
        <button type="button" className="iconButton small" disabled={saving || index === 0} aria-label={t('home.pins.up', { label: pin.label })} title={t('home.pins.upShort')} onClick={() => move(pin.id, -1)}><ArrowUp aria-hidden="true" /></button>
        <button type="button" className="iconButton small" disabled={saving || index === items.length - 1} aria-label={t('home.pins.down', { label: pin.label })} title={t('home.pins.downShort')} onClick={() => move(pin.id, 1)}><ArrowDown aria-hidden="true" /></button>
        <button type="button" className="iconButton small" disabled={saving} aria-label={t('home.pins.remove', { label: pin.label })} title={t('home.pins.removeShort')} onClick={() => remove(pin.id)}><Trash2 aria-hidden="true" /></button>
      </span>}
    </li>)}</ul>}
    {editing && pins.state === 'ready' && <PinForm saving={saving} full={items.length >= 24} onAdd={add} />}
  </section>;
}

function PinForm({ saving, full, onAdd }: { saving: boolean; full: boolean; onAdd: (pin: { kind: PinKind; label: string; target: string }) => void }) {
  const [kind, setKind] = useState<'feature' | 'link'>('feature');
  const [feature, setFeature] = useState(PINNABLE_FEATURES[0]);
  const [link, setLink] = useState('');
  const [label, setLabel] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const id = useId();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (kind === 'link' && !validLink(link)) { setProblem(t('home.pins.badLink')); return; }
    const view = viewForFeature(feature);
    const name = label.trim() || (kind === 'feature' && view ? viewLabel(view) : '');
    if (!name) { setProblem(t('home.pins.needLabel')); return; }
    setProblem(null);
    onAdd({ kind, label: name.slice(0, 60), target: kind === 'feature' ? feature : link.trim() });
    setLabel(''); setLink('');
  };
  if (full) return <p className="muted">{t('home.pins.full')}</p>;
  return <form className="pinForm" onSubmit={submit} aria-label={t('home.pins.add')}>
    <label htmlFor={`${id}-kind`}><span>{t('home.pins.kind')}</span>
      <select id={`${id}-kind`} value={kind} onChange={(e) => setKind(e.target.value as 'feature' | 'link')}><option value="feature">{t('home.pins.kindFeature')}</option><option value="link">{t('home.pins.kindLink')}</option></select></label>
    {kind === 'feature'
      ? <label htmlFor={`${id}-feature`}><span>{t('home.pins.feature')}</span><select id={`${id}-feature`} value={feature} onChange={(e) => setFeature(e.target.value)}>{PINNABLE_FEATURES.map((key) => { const view = viewForFeature(key); return <option key={key} value={key}>{view ? viewLabel(view) : key}</option>; })}</select></label>
      : <label htmlFor={`${id}-link`}><span>{t('home.pins.link')}</span><input id={`${id}-link`} type="url" inputMode="url" dir="ltr" placeholder="https://" value={link} onChange={(e) => setLink(e.target.value)} /></label>}
    <label htmlFor={`${id}-label`}><span>{t('home.pins.label')}</span><input id={`${id}-label`} maxLength={60} dir="auto" value={label} placeholder={kind === 'feature' ? t('home.pins.labelOptional') : ''} onChange={(e) => setLabel(e.target.value)} /></label>
    <button type="submit" className="primaryButton smallButton" disabled={saving}><Plus aria-hidden="true" />{t('home.pins.add')}</button>
    {problem && <p className="fieldError" role="alert">{problem}</p>}
  </form>;
}
