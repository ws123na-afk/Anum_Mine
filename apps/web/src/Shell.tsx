// The ANUM 360 web shell (docs/design/anum360.md#web-command-center): the logo mark, the sidebar
// (views, the person's workspaces with switch and Add workspace, More and Admin), and the top bar
// (global search with the Cmd+K command palette, notifications, the user menu). App state stays
// in main.tsx; these components only render it and report what the person picked.
import { useCallback, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import {
  Activity, Bell, BellOff, BookOpen, GraduationCap, Phone, ShoppingCart, Wallet, Bot, Building2, ChevronDown, CircleAlert, FileText, Gauge, Hammer, House, ListTodo, LogOut, Mail, MessageCircle,
  MessageSquare, Mic, Plug, Plus, RefreshCw, Scale, ScrollText, Search, Settings, ShieldCheck, SlidersHorizontal, Sparkles, Store, Users, Workflow, Disc3, PhoneCall, FileBarChart, Library, Star,
  ClipboardCheck, ClipboardList, Building, BriefcaseBusiness, Megaphone, SunMoon, CalendarDays, AtSign, Handshake, PhoneOutgoing, Landmark } from 'lucide-react';
import type { Approval, Task } from '@anum/contracts';
import type { WorkspacePermissions } from '@anum/contracts';
import * as api from './lib/api';
import { mailUnread, pollMailUnread, subscribeMailUnread } from './lib/mailUnread';
import { getMailUnread } from './lib/mailRulesApi';
import * as admin from './lib/adminApi';
import { workspaceIdInput, workspaceName } from './lib/audit';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { riskLabel, statusLabel } from './lib/labels';
import type { MembershipEntry } from './lib/workspaces';
import type { AutomationAlert } from './lib/automation';
import type { ActionNotice } from './lib/autonomy';
import type { EmailMessage } from './lib/email';
import { searchEmails, searchFiles, searchTasks } from './lib/commandApi';
import { NAV, badgeText, initials, isPaletteShortcut, matchViews, moveActive, notificationCount, viewLabel, type NavGroup, type PaletteItem, type View } from './lib/shell';
import { Modal } from './Dialog';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { getPeople } from './lib/teamApi';
import { TeamNotificationsSection, type TeamNotifications } from './NotificationBell';
import { HelpButton } from './HelpLink';

const ICONS: Record<View, ReactNode> = {
  home: <House />, command: <Sparkles />, assistant: <MessageSquare />, agents: <Bot />, voice: <Mic />, call: <Phone />, calls: <PhoneOutgoing />, tyre: <Disc3 />, meetings: <PhoneCall />, calendar: <CalendarDays />, commerce: <ShoppingCart />, finance: <Wallet />, marketing: <Megaphone />, business: <Handshake />, learning: <GraduationCap />, automation: <Workflow />, tasks: <ListTodo />,
  integrations: <Plug />, files: <FileText />, memory: <BookOpen />, knowledge: <Library />, reports: <FileBarChart />, favorites: <Star />, skills: <Store />, inbox: <Mail />, email: <AtSign />, whatsapp: <MessageCircle />,
  approvals: <ShieldCheck />, activity: <Activity />, autonomy: <SlidersHorizontal />, settings: <Settings />, governance: <Building2 />, setup: <ClipboardList />, suppressions: <BellOff />,
  enterprise: <Store />, members: <Users />, audit: <ScrollText />, budgets: <Gauge />, policy: <Scale />, developer: <Hammer />,
  team: <ClipboardCheck />, assistants: <BriefcaseBusiness />, organisations: <Building />, platformBilling: <Landmark />,
};
export const viewIcon = (view: View) => ICONS[view];

const storage = (() => { try { return window.localStorage; } catch { return null; } })();
const GROUPS_KEY = 'anum.sidebar.groups';
type Folds = Partial<Record<NavGroup | 'workspaces', boolean>>;
const readFolds = (): Folds => { try { return JSON.parse(storage?.getItem(GROUPS_KEY) ?? '{}') as Folds; } catch { return {}; } };

/** The ANUM 360 mark: two aurora peaks (an "A" doubled), drawn in the accent gradient. */
export function AnumMark({ size = 32, className }: { size?: number; className?: string }) {
  const id = useId().replace(/:/g, '');
  return <svg className={className} width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
    <defs><linearGradient id={`anum-${id}`} x1="0" y1="1" x2="1" y2="0"><stop offset="0" stopColor="var(--d-mark-a)" /><stop offset=".55" stopColor="var(--d-mark-b)" /><stop offset="1" stopColor="var(--d-mark-c)" /></linearGradient></defs>
    <path fill={`url(#anum-${id})`} d="M3 41 15.5 9h6L34 41h-7.2l-8.3-22.4L10.2 41H3Zm17.6 0L29 18.6l3.6 9.3L28.9 41h-8.3Zm12.6-17.7L38 9h1.9L45 41h-6.9l-4.9-17.7Z" />
  </svg>;
}

// ---------------------------------------------------------------- sidebar

type Directory = { state: 'loading' } | { state: 'ready'; rows: MembershipEntry[] } | { state: 'error' };

export function Sidebar({ view, views, pendingApprovals, stream, lastEvent, workspaceId, onGo, onSwitchWorkspace, branding }: {
  view: View; views: readonly View[]; pendingApprovals: number; stream: 'connecting' | 'live' | 'offline'; lastEvent: string | null;
  workspaceId: string; onGo: (view: View) => void; onSwitchWorkspace: (workspaceId: string) => Promise<void>;
  /** The organisation's branding (docs/customer-organisations.md#branding): its logo and name replace the mark and tagline. */
  branding?: { name: string | null; logo: string | null };
}) {
  const [folds, setFolds] = useState<Folds>(readFolds);
  const toggle = (group: NavGroup | 'workspaces') => setFolds((old) => {
    const next = { ...old, [group]: !old[group] };
    try { storage?.setItem(GROUPS_KEY, JSON.stringify(next)); } catch { /* remembered for this page only */ }
    return next;
  });
  const allowed = new Set(views);
  const mailUnreadCount = useSyncExternalStore(subscribeMailUnread, mailUnread, () => 0);
  // The open Email page publishes its own counts; elsewhere the background sync's counts keep the badge current.
  const pollMail = allowed.has('email') && view !== 'email';
  useEffect(() => (pollMail ? pollMailUnread(getMailUnread) : undefined), [pollMail]);
  // The nav scrolls on its own: a view opened by link, the palette or a page button brings its
  // entry into sight (to the nearest edge, so an entry already in view never moves), again once the
  // views the person may open are known, since an owner-only entry appears only then. It moves the
  // nav's scroll position only: scrollIntoView would also move the keyboard's starting point, so
  // the first Tab would skip the "Skip to content" link.
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const nav = navRef.current, entry = nav?.querySelector<HTMLElement>('.navItem.active');
    if (!nav || !entry) return;
    const box = nav.getBoundingClientRect(), at = entry.getBoundingClientRect();
    if (at.top < box.top) nav.scrollTop -= box.top - at.top;
    else if (at.bottom > box.bottom) nav.scrollTop += at.bottom - box.bottom;
    if (at.left < box.left) nav.scrollLeft -= box.left - at.left;
    else if (at.right > box.right) nav.scrollLeft += at.right - box.right;
  }, [view, views.length]);
  const item = (id: View) => <li key={id}><button type="button" className={`navItem ${view === id ? 'active' : ''}`} aria-current={view === id ? 'page' : undefined} onClick={() => onGo(id)}>
    {ICONS[id]}<span>{viewLabel(id)}</span>
    {id === 'approvals' && pendingApprovals > 0 && <span className="navCount" aria-label={t('nav.pendingCount', { count: pendingApprovals })}>{pendingApprovals}</span>}
    {id === 'email' && mailUnreadCount > 0 && <span className="navCount" aria-label={t('mailc.unreadBadge', { count: mailUnreadCount })}>{badgeText(mailUnreadCount)}</span>}
  </button></li>;
  const group = (name: NavGroup | 'workspaces', label: MessageKey, body: ReactNode) => {
    const open = !folds[name];
    const listId = `nav-group-${name}`;
    return <div className={`navGroup navGroup-${name}`}>
      <button type="button" className="navGroupToggle" aria-expanded={open} aria-controls={listId} onClick={() => toggle(name)}>
        <span>{t(label)}</span><ChevronDown aria-hidden="true" />
      </button>
      <div id={listId} hidden={!open}>{body}</div>
    </div>;
  };
  return <aside className="sidebar">
    <div className="brand">{branding?.logo ? <img className="brandLogo" src={branding.logo} alt="" /> : <AnumMark className="brandMark" size={36} />}<div><span className="brandName">{t('brand.name')}</span><small>{branding?.name ?? t('brand.tagline')}</small></div></div>
    <nav ref={navRef} aria-label={t('nav.primary')}>
      <ul className="navList">{NAV[0].views.filter((id) => allowed.has(id)).map(item)}</ul>
      {group('workspaces', 'sidebar.workspaces', <SidebarWorkspaces workspaceId={workspaceId} onSwitchWorkspace={onSwitchWorkspace} />)}
      {NAV.slice(1).map((g) => {
        const ids = g.views.filter((id) => allowed.has(id));
        return ids.length ? <div key={g.group}>{group(g.group, `sidebar.group.${g.group}` as MessageKey, <ul className="navList">{ids.map(item)}</ul>)}</div> : null;
      })}
    </nav>
    <div className="systemState" role="status"><span className={`onlineDot ${stream}`} aria-hidden="true" />{t(`shell.stream.${stream}`)}<small>{lastEvent ?? t('shell.waitingEvents')}</small></div>
  </aside>;
}

function SidebarWorkspaces({ workspaceId, onSwitchWorkspace }: { workspaceId: string; onSwitchWorkspace: (workspaceId: string) => Promise<void> }) {
  const [directory, setDirectory] = useState<Directory>({ state: 'loading' });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const load = useCallback(() => {
    setDirectory({ state: 'loading' });
    api.listMyWorkspaces().then((rows) => setDirectory({ state: 'ready', rows })).catch(() => setDirectory({ state: 'error' }));
  }, []);
  useEffect(load, [load]);
  const rows = directory.state === 'ready' ? directory.rows : [];
  const list = rows.some((row) => row.workspaceId === workspaceId) ? rows : [{ workspaceId, workspaceName: null, role: '' }, ...rows];
  const go = (id: string) => {
    if (id === workspaceId || busy) return;
    setBusy(id); setError(null);
    void onSwitchWorkspace(id).catch((e: unknown) => { setError(describeError(e)); setBusy(null); });
  };
  return <>
    <ul className="navList workspaceNav" aria-label={t('sidebar.workspaces')}>
      {list.map((row, index) => {
        const name = row.workspaceName ?? row.workspaceId;
        const current = row.workspaceId === workspaceId;
        return <li key={row.workspaceId}><button type="button" className={`navItem workspaceItem ${current ? 'current' : ''}`} aria-current={current ? 'true' : undefined} disabled={busy !== null && !current} onClick={() => go(row.workspaceId)} title={row.workspaceId}>
          <span className={`workspaceDot tone${index % 5}`} aria-hidden="true">{initials(name).slice(0, 1)}</span>
          <span dir="auto">{name}</span>
          {busy === row.workspaceId ? <small className="workspaceNote">{t('sidebar.switching')}</small> : row.archived ? <small className="workspaceNote">{t('sidebar.archived')}</small> : null}
        </button></li>;
      })}
    </ul>
    {directory.state === 'loading' && <p className="navNote" role="status">{t('sidebar.workspacesLoading')}</p>}
    {directory.state === 'error' && <p className="navNote">{t('sidebar.workspacesError')} <button type="button" className="linkButton" onClick={load}>{t('sidebar.workspacesRetry')}</button></p>}
    {error && <p className="navNote navNoteError" role="alert">{error}</p>}
    <button type="button" className="navItem addWorkspace" onClick={() => setAdding(true)}><Plus aria-hidden="true" /><span>{t('sidebar.addWorkspace')}</span></button>
    {adding && <AddWorkspaceDialog onClose={() => setAdding(false)} onCreated={load} onSwitchWorkspace={onSwitchWorkspace} />}
  </>;
}

/** Create a workspace in this organization (organization admins), then offer to switch to it. */
function AddWorkspaceDialog({ onClose, onCreated, onSwitchWorkspace }: { onClose: () => void; onCreated: () => void; onSwitchWorkspace: (workspaceId: string) => Promise<void> }) {
  const [permissions, setPermissions] = useState<WorkspacePermissions | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [id, setId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null);
  useEffect(() => { admin.getWorkspacePermissions().then(setPermissions).catch((e: unknown) => setLoadError(describeError(e))); }, []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const checkedName = workspaceName(name);
    if (!checkedName.ok) { setError(checkedName.error); return; }
    const checkedId = workspaceIdInput(id);
    if (!checkedId.ok) { setError(checkedId.error); return; }
    setBusy(true); setError(null);
    admin.createWorkspace(checkedName.value, checkedId.value)
      .then((next) => { api.rememberWorkspace(next.id); setCreated({ id: next.id, name: next.name }); onCreated(); })
      .catch((e: unknown) => setError(describeError(e)))
      .finally(() => setBusy(false));
  };
  return <Modal title={t('sidebar.addTitle')} onClose={onClose} className="addWorkspaceDialog">
    {!permissions && !loadError && <p className="muted" role="status">{t('sidebar.addChecking')}</p>}
    {loadError && <p className="notice errorNotice" role="alert"><CircleAlert aria-hidden="true" />{t('sidebar.addLoadError', { reason: loadError })}</p>}
    {permissions && !permissions.canCreateWorkspace && <p className="notice" role="note">{t('sidebar.addDenied')}</p>}
    {permissions?.canCreateWorkspace && !created && <form onSubmit={submit} noValidate>
      <p className="muted">{t('workspaceAdmin.createText')}</p>
      <label className="field"><span>{t('workspaceAdmin.name')}</span><input value={name} maxLength={160} dir="auto" data-autofocus onChange={(event) => setName(event.target.value)} placeholder={t('workspaceAdmin.namePlaceholder')} autoComplete="off" /></label>
      <label className="field"><span>{t('workspaceAdmin.idOptional')}</span><input value={id} maxLength={80} dir="ltr" onChange={(event) => setId(event.target.value)} placeholder={t('workspaceAdmin.idPlaceholder')} autoComplete="off" spellCheck={false} /></label>
      {error && <p className="notice errorNotice" role="alert"><CircleAlert aria-hidden="true" />{error}</p>}
      <div className="actions"><button type="submit" disabled={busy}><Plus aria-hidden="true" />{busy ? t('dialog.working') : t('workspaceAdmin.createButton')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>}
    {created && <>
      <p className="notice" role="status">{t('workspaceAdmin.created', { name: created.name })}</p>
      {error && <p className="notice errorNotice" role="alert"><CircleAlert aria-hidden="true" />{error}</p>}
      <div className="actions"><button type="button" data-autofocus disabled={busy} onClick={() => { setBusy(true); void onSwitchWorkspace(created.id).catch((e: unknown) => { setError(describeError(e)); setBusy(false); }); }}>{t('workspaceAdmin.switchTo', { name: created.name })}</button><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>
    </>}
    {(!permissions?.canCreateWorkspace || loadError) && !created && <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>}
  </Modal>;
}

// ---------------------------------------------------------------- top bar

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export function TopBar({ tenantId, workspaceId, onSwitchWorkspace, onSwitchOrganisation, onRefresh, onOpenPalette, bell, user }: {
  tenantId: string; workspaceId: string; onSwitchWorkspace: (workspaceId: string) => Promise<void>; onRefresh: () => void; onOpenPalette: () => void;
  onSwitchOrganisation?: (tenantId: string, workspaceId: string) => Promise<void>;
  bell: ReactNode; user: ReactNode;
}) {
  return <header className="topbar">
    <button type="button" className="searchTrigger" onClick={onOpenPalette} aria-keyshortcuts={isMac ? 'Meta+K' : 'Control+K'}>
      <Search aria-hidden="true" /><span>{t('search.placeholder')}</span><kbd>{isMac ? '⌘K' : 'Ctrl K'}</kbd>
    </button>
    <div className="topActions">
      <WorkspaceSwitcher tenantId={tenantId} workspaceId={workspaceId} onSwitch={onSwitchWorkspace} onSwitchOrganisation={onSwitchOrganisation} />
      <button type="button" className="iconButton refreshButton" title={t('shell.refresh')} aria-label={t('shell.refresh')} onClick={onRefresh}><RefreshCw aria-hidden="true" /></button>
      <HelpButton />
      {bell}
      {user}
    </div>
  </header>;
}

/** Closes a popover on Escape or a click outside it. */
function usePopover(open: boolean, setOpen: (open: boolean) => void) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node)) {
        setOpen(false);
        if (event instanceof KeyboardEvent) root.current?.querySelector<HTMLButtonElement>('button')?.focus();
      }
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open, setOpen]);
  return root;
}

export function NotificationsMenu({ approvals, alerts, notices, team, onOpenApprovals, onOpenAlert, onOpenActivity }: {
  approvals: readonly Approval[]; alerts: readonly AutomationAlert[]; notices: readonly ActionNotice[];
  /** The caller's team notifications (docs/team-work.md#notifications); their unread ones count too. */
  team?: TeamNotifications;
  onOpenApprovals: () => void; onOpenAlert: (alert: AutomationAlert) => void; onOpenActivity: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = usePopover(open, setOpen);
  const panelId = useId();
  const pending = approvals.filter((a) => a.status === 'pending');
  const count = notificationCount({ pendingApprovals: pending.length, alerts: alerts.length, notices: notices.length, team: team?.unread ?? 0 });
  const pick = (action: () => void) => { setOpen(false); action(); };
  return <div className="popoverRoot" ref={root}>
    <button type="button" className="iconButton bellButton" aria-expanded={open} aria-controls={panelId} aria-label={t('notifications.button', { count })} title={t('notifications.title')} onClick={() => setOpen(!open)}>
      <Bell aria-hidden="true" />{count > 0 && <span className="bellCount" aria-hidden="true">{badgeText(count)}</span>}
    </button>
    {open && <section className="popover notificationsPanel" id={panelId} role="dialog" aria-label={t('notifications.title')}>
      <h2>{t('notifications.title')}</h2>
      {count === 0 && !team?.items.length && <p className="muted">{t('notifications.empty')}</p>}
      <ul className="popoverList">
        {pending.slice(0, 5).map((a) => <li key={a.id}><button type="button" onClick={() => pick(onOpenApprovals)}>
          <ShieldCheck aria-hidden="true" /><span><strong dir="ltr">{a.action}</strong><small>{t('notifications.approval', { risk: riskLabel(a.riskLevel) })}</small></span>
        </button></li>)}
        {pending.length > 5 && <li><button type="button" onClick={() => pick(onOpenApprovals)}><ShieldCheck aria-hidden="true" /><span><strong>{t('notifications.moreApprovals', { count: pending.length - 5 })}</strong></span></button></li>}
        {alerts.map((a) => <li key={a.id}><button type="button" onClick={() => pick(() => onOpenAlert(a))}>
          <Workflow aria-hidden="true" /><span><strong dir="auto">{a.title}</strong><small dir="auto">{a.message}</small></span>
        </button></li>)}
        {notices.map((n) => <li key={n.actionId}><button type="button" onClick={() => pick(onOpenActivity)}>
          <Activity aria-hidden="true" /><span><strong dir="auto">{n.summary}</strong><small>{t('notifications.acted')}</small></span>
        </button></li>)}
      </ul>
      {team && <TeamNotificationsSection state={team} pick={pick} />}
    </section>}
  </div>;
}

export function UserMenu({ name, userId, role, signOut, onGo }: {
  name: string | null; userId: string | null; role: string | null; signOut: (() => void) | null; onGo: (target: 'settings' | 'language' | 'appearance' | 'notifications' | 'privacy') => void;
}) {
  const [open, setOpen] = useState(false);
  const root = usePopover(open, setOpen);
  const panelId = useId();
  // Without a name in the sign-in token (development sessions), the person's team profile names them,
  // so the menu shows "Omar Owner" rather than an id such as user_local.
  const [profileName, setProfileName] = useState<string | null>(null);
  useEffect(() => {
    if (name || !userId) return undefined;
    let live = true;
    getPeople().then((people) => {
      if (!Array.isArray(people)) return;
      const own = people.find((person) => person.user_id === userId)?.display_name?.trim();
      if (live && own && own !== userId) setProfileName(own);
    }).catch(() => undefined);
    return () => { live = false; };
  }, [name, userId]);
  const shown = name ?? profileName ?? userId ?? t('user.unknown');
  const roleText = role ? t(`workspaceAdmin.role.${role}` as MessageKey) : null;
  const pick = (target: 'settings' | 'language' | 'appearance' | 'notifications' | 'privacy') => { setOpen(false); onGo(target); };
  return <div className="popoverRoot" ref={root}>
    <button type="button" className="userButton" aria-expanded={open} aria-controls={panelId} aria-label={roleText ? `${t('user.menu', { name: shown })} ${roleText}` : t('user.menu', { name: shown })} onClick={() => setOpen(!open)}>
      <span className="avatar" aria-hidden="true">{initials(shown)}</span>
      <span className="userText"><strong dir="auto">{shown}</strong>{roleText && <span className="planPill">{roleText}</span>}</span>
      <ChevronDown aria-hidden="true" />
    </button>
    {open && <section className="popover userPanel" id={panelId} role="dialog" aria-label={t('user.menuTitle')}>
      <div className="userSummary"><span className="avatar large" aria-hidden="true">{initials(shown)}</span><div><strong dir="auto">{shown}</strong>{userId && <small dir="ltr">{userId}</small>}{roleText && <small>{t('user.role', { role: roleText })}</small>}</div></div>
      <ul className="popoverList">
        <li><button type="button" onClick={() => pick('settings')}><Settings aria-hidden="true" /><span><strong>{t('nav.settings')}</strong></span></button></li>
        <li><button type="button" onClick={() => pick('notifications')}><Bell aria-hidden="true" /><span><strong>{t('settings.tab.notifications')}</strong></span></button></li>
        <li><button type="button" onClick={() => pick('language')}><MessageSquare aria-hidden="true" /><span><strong>{t('settings.tab.language')}</strong></span></button></li>
        <li><button type="button" onClick={() => pick('appearance')}><SunMoon aria-hidden="true" /><span><strong>{t('settings.tab.appearance')}</strong></span></button></li>
        <li><button type="button" onClick={() => pick('privacy')}><ShieldCheck aria-hidden="true" /><span><strong>{t('settings.tab.privacy')}</strong></span></button></li>
        {signOut && <li><button type="button" onClick={() => { setOpen(false); signOut(); }}><LogOut aria-hidden="true" /><span><strong>{t('shell.signOut')}</strong></span></button></li>}
      </ul>
    </section>}
  </div>;
}

// ---------------------------------------------------------------- command palette

type Section<T> = { state: 'idle' | 'loading' | 'ready' | 'error'; items: T[] };
const idle = <T,>(): Section<T> => ({ state: 'idle', items: [] });

export function CommandPalette({ views, recentTasks, onClose, onPick }: {
  views: readonly View[]; recentTasks: readonly Task[]; onClose: () => void; onPick: (item: PaletteItem) => void;
}) {
  const [query, setQuery] = useState('');
  const [tasks, setTasks] = useState<Section<Task>>(idle());
  const [files, setFiles] = useState<Section<api.FileRecord>>(idle());
  const [emails, setEmails] = useState<Section<EmailMessage>>(idle());
  const [active, setActive] = useState(0);
  const listId = useId();
  const q = query.trim();
  useEffect(() => {
    if (!q) { setTasks(idle()); setFiles(idle()); setEmails(idle()); return; }
    let live = true;
    const run = <T,>(set: (s: Section<T>) => void, load: () => Promise<T[]>) => {
      set({ state: 'loading', items: [] });
      load().then((items) => { if (live) set({ state: 'ready', items }); }).catch(() => { if (live) set({ state: 'error', items: [] }); });
    };
    const timer = window.setTimeout(() => { run(setTasks, () => searchTasks(q)); run(setFiles, () => searchFiles(q)); run(setEmails, () => searchEmails(q)); }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [q]);
  useEffect(() => setActive(0), [q]);

  const sections = useMemo(() => {
    const viewItems = matchViews(q, views).slice(0, q ? 6 : views.length);
    const taskItems: PaletteItem[] = (q ? tasks.items : [...recentTasks].slice(0, 5)).map((task) => ({ kind: 'task', id: task.id, label: task.title, detail: statusLabel(task.status) }));
    const fileItems: PaletteItem[] = files.items.map((file) => ({ kind: 'file', id: file.id, label: file.name, detail: file.content_type }));
    const emailItems: PaletteItem[] = emails.items.map((message) => ({ kind: 'email', id: message.id, label: message.subject || t('search.noSubject'), detail: message.from_name ?? message.from_address }));
    return [
      { key: 'views', title: t('search.views'), items: viewItems, state: 'ready' as const },
      { key: 'tasks', title: q ? t('search.tasks') : t('search.recentTasks'), items: taskItems, state: q ? tasks.state : 'ready' as const },
      ...(q ? [{ key: 'files', title: t('search.files'), items: fileItems, state: files.state }, { key: 'emails', title: t('search.emails'), items: emailItems, state: emails.state }] : []),
    ];
  }, [q, views, recentTasks, tasks, files, emails]);
  const flat = sections.flatMap((section) => section.items);
  const optionId = (index: number) => `${listId}-opt-${index}`;
  const onKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && flat.length) { event.preventDefault(); setActive((current) => moveActive(current, flat.length, event.key)); return; }
    if (event.key === 'Enter' && flat[active]) { event.preventDefault(); onPick(flat[active]); }
  };
  const searching = sections.some((section) => section.state === 'loading');
  let index = -1;
  return <Modal title={t('search.title')} titleHidden onClose={onClose} className="paletteDialog">
    <div className="paletteInput">
      <Search aria-hidden="true" />
      <input data-autofocus role="combobox" aria-expanded="true" aria-controls={listId} aria-autocomplete="list" aria-label={t('search.label')}
        aria-activedescendant={flat[active] ? optionId(active) : undefined} value={query} placeholder={t('search.inputPlaceholder')} onChange={(event) => setQuery(event.target.value)} onKeyDown={onKey} autoComplete="off" spellCheck={false} />
      <kbd>Esc</kbd>
    </div>
    <p className="visuallyHidden" role="status" aria-live="polite">{searching ? t('search.searching') : q ? t('search.resultCount', { count: flat.length }) : ''}</p>
    <div className="paletteResults" id={listId} role="listbox" aria-label={t('search.results')}>
      {sections.map((section) => (section.items.length > 0 || section.state !== 'ready') && <div key={section.key} role="group" aria-label={section.title} className="paletteGroup">
        <p className="paletteGroupTitle" aria-hidden="true">{section.title}</p>
        {section.state === 'loading' && <p className="paletteNote">{t('search.searching')}</p>}
        {section.state === 'error' && <p className="paletteNote">{t(`search.error.${section.key}` as MessageKey)}</p>}
        {section.state === 'ready' && section.items.length === 0 && <p className="paletteNote">{t('search.none')}</p>}
        {section.items.map((item) => {
          index += 1;
          const mine = index;
          return <div key={`${item.kind}:${item.id}`} id={optionId(mine)} role="option" aria-selected={mine === active} className={`paletteOption ${mine === active ? 'active' : ''}`} onMouseEnter={() => setActive(mine)} onMouseDown={(event) => event.preventDefault()} onClick={() => onPick(item)}>
            <span className="paletteIcon" aria-hidden="true">{item.kind === 'view' ? ICONS[item.id as View] : item.kind === 'task' ? <ListTodo /> : item.kind === 'file' ? <FileText /> : <Mail />}</span>
            <span className="paletteText"><strong dir="auto">{item.label}</strong>{item.detail && <small dir="auto">{item.detail}</small>}</span>
          </div>;
        })}
      </div>)}
      {q && !searching && flat.length === 0 && <p className="paletteEmpty">{t('search.nothing', { query: q })}</p>}
    </div>
  </Modal>;
}

/** Opens the palette on Cmd+K / Ctrl+K (and "/" when not typing). */
export function usePaletteShortcut(open: () => void) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = Boolean(target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)));
      if (isPaletteShortcut(event, typing)) { event.preventDefault(); open(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);
}
