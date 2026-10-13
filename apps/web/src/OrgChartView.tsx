// The AI team org chart (docs/org-chart.md): the owner, ANUM as chief of staff, the departments
// with their agents, department assistants and people, each agent with live status, counts,
// skills and the tools it uses. A table view lists every member with filters; a drawer shows a
// member's complete profile; the chart exports as a PNG and prints as a document of every profile.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  AlertTriangle, BarChart3, Bot, Briefcase, Calendar, ChevronDown, ChevronRight, CircleDot, Crown, Download, Eye, FileText, Heart, LayoutList, Mail,
  Network, Phone, Plus, Printer, RefreshCw, Search, ShieldCheck, ShoppingCart, Sparkles, Truck, User, UserRound, Wrench,
} from 'lucide-react';
import type { AgentProfile } from '@anum/contracts';
import * as api from './lib/api';
import * as auth from './lib/auth';
import { describeError, isPermissionDenied } from './lib/errors';
import { CATALOGS, formatDateTime, getLocale, t, type MessageKey } from './lib/i18n';
import { groupName, itemName } from './lib/integrationSetup';
import {
  autonomyRules, balanceColumns, buildOrgChart, columnsForWidth, departmentAliases, departmentCounts, departmentHealth, departmentLead, filterMembers,
  loadOrgSources, NO_FILTER, orgKpis, REFRESH_MS, toolOptions, visibleMembers, type ProfileRef,
  type MemberFilter, type OrgChart, type OrgDepartment, type OrgMember, type OrgSources, type ToolChip,
} from './lib/orgChart';
import { DEFAULT_PALETTE, imageFileName, orgChartSvg, type ImageModel, type ImagePalette } from './lib/orgChartImage';
import { displayName, initials } from './lib/shell';
import { OrgMemberDrawer } from './OrgMemberDrawer';
import './orgChart.css';

export type OrgGo = 'integrations' | 'assistants' | 'team' | 'approvals' | 'tasks' | 'automation';
type Role = 'owner' | 'member' | 'viewer' | null;
type Layout = 'chart' | 'table';

const ICONS: Record<string, typeof Bot> = {
  bot: Bot, truck: Truck, search: Search, mail: Mail, calendar: Calendar, 'file-text': FileText, phone: Phone,
  briefcase: Briefcase, user: User, sparkles: Sparkles, wrench: Wrench, chart: BarChart3, 'shopping-cart': ShoppingCart, heart: Heart,
};
const LAYOUT_KEY = 'anum.orgChart.layout';
export const ALIASES = departmentAliases([CATALOGS.en, CATALOGS.ar] as unknown as Record<string, unknown>[]);

/** Opens another part of the app when the shell gave no callback: the hash names the view on load. */
export function openByHash(view: string): void {
  window.location.hash = view;
  window.location.reload();
}

function readLayout(): Layout {
  try { return localStorage.getItem(LAYOUT_KEY) === 'table' ? 'table' : 'chart'; } catch { return 'chart'; }
}

export function Avatar({ icon, color, small }: { icon: string; color: string; small?: boolean }) {
  const Icon = ICONS[icon] ?? Bot;
  return <span className={`agentAvatar orgAvatar ${small ? 'sm' : ''}`} style={{ '--agent-color': color } as CSSProperties} aria-hidden="true"><Icon /></span>;
}

export function departmentLabel(d: Pick<OrgDepartment, 'known' | 'name' | 'key'>): string {
  if (d.known) return t(`team.dept.${d.known}` as MessageKey);
  if (d.key === 'none') return t('org.dept.none');
  return d.name ?? d.key;
}

export function statusText(member: Pick<OrgMember, 'status' | 'kind'>): string {
  return member.kind === 'person' ? t('org.kind.person') : t(`org.status.${member.status}` as MessageKey);
}

export function chipLabel(chip: ToolChip): string {
  if (chip.label.kind === 'group') return groupName(chip.label.id);
  if (chip.label.kind === 'item') return itemName(chip.label.id);
  return chip.label.id;
}

export function chipStatus(chip: ToolChip): string {
  return t(`org.tool.${chip.status}` as MessageKey);
}

/** "2 running · 1 waiting for approval · 1 failed today", or "No open work". */
export function countsText(member: Pick<OrgMember, 'running' | 'approvals' | 'failed' | 'kind'>): string {
  if (member.kind === 'person') return '';
  const parts = [
    member.running ? t('org.count.running', { count: member.running }) : '',
    member.approvals ? t('org.count.approvals', { count: member.approvals }) : '',
    member.failed ? t('org.count.failed', { count: member.failed }) : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : t('org.count.none');
}

/** The member's role line: what an agent does, an assistant's autonomy, a person's title. */
export function roleText(member: OrgMember): string {
  if (member.kind === 'person') return [member.jobTitle, member.role && ['owner', 'member', 'viewer'].includes(member.role) ? t(`org.role.${member.role}` as MessageKey) : member.role].filter(Boolean).join(' · ');
  if (member.kind === 'assistant') return member.assistant ? `${t('org.kind.assistant')} · ${t(`assistants.autonomy.${member.assistant.autonomy}` as MessageKey)}` : t('org.kind.assistant');
  return member.description || t(`agents.category.${member.agent?.category ?? 'work'}` as MessageKey);
}

function StatusPill({ member }: { member: Pick<OrgMember, 'status' | 'kind'> }) {
  return <span className={`orgStatus is-${member.kind === 'person' ? 'person' : member.status}`}><span className="orgDot" aria-hidden="true" />{statusText(member)}</span>;
}

function readPalette(): ImagePalette {
  try {
    const style = getComputedStyle(document.documentElement);
    const v = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
    return {
      bg: v('--d-bg', DEFAULT_PALETTE.bg), surface: v('--d-surface-1', DEFAULT_PALETTE.surface), surface2: v('--d-surface-2', DEFAULT_PALETTE.surface2),
      line: v('--d-line-strong', DEFAULT_PALETTE.line), text: v('--d-text', DEFAULT_PALETTE.text), muted: v('--d-muted', DEFAULT_PALETTE.muted),
      accent: v('--d-primary', DEFAULT_PALETTE.accent), onAccent: v('--d-on-primary', DEFAULT_PALETTE.onAccent), sky: v('--d-sky', DEFAULT_PALETTE.sky),
      ok: v('--d-ok', DEFAULT_PALETTE.ok), warn: v('--d-warn', DEFAULT_PALETTE.warn), stop: v('--d-stop', DEFAULT_PALETTE.stop),
    };
  } catch { return DEFAULT_PALETTE; }
}

async function svgToPng(svg: string, width: number, height: number): Promise<Blob> {
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }));
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    // Twice the size for sharp text, kept under the browsers' canvas limits (side and area).
    const scale = Math.min(2, 16000 / height, 16000 / width, Math.sqrt(16_000_000 / (width * height)));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error(t('org.export.noCanvas'));
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, width, height);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error(t('org.export.noCanvas'))), 'image/png'));
  } finally { URL.revokeObjectURL(url); }
}

interface Owner { name: string; role: string; initials: string; isYou: boolean }

/**
 * The top of the chart: you when you own the workspace (your sign-in name, else your Team
 * profile), otherwise the owner from the Team directory when it lists one.
 */
function useOwner(role: Role, people: OrgSources['people']): Owner & { workspace: string | null } {
  const [workspace, setWorkspace] = useState<string | null>(null);
  useEffect(() => { void api.getCurrentWorkspace().then((w) => setWorkspace(w.name)).catch(() => setWorkspace(null)); }, []);
  const isYou = role === 'owner';
  const me = people?.find((p) => p.user_id === api.currentUserId());
  const owner = people?.find((p) => p.role === 'owner');
  const name = isYou ? displayName(auth.currentClaims()) ?? me?.display_name ?? null : owner?.display_name ?? null;
  const shown = name ?? (isYou ? t('org.owner.you') : t('org.owner.title'));
  // While the caller's role is still loading, say only "Owner".
  const roleLine = isYou || !role ? t('org.owner.youRole') : `${t('org.owner.youRole')} · ${t('org.owner.notYou', { role: t(`org.role.${role}` as MessageKey) })}`;
  return { name: shown, role: roleLine, initials: name ? initials(name) : '★', isYou, workspace };
}

export function OrgChartView({ role, onOpenProfile, onOpenAgent, onEditAgent, onCreate, onOpenApprovals, onOpenTasks, onOpenBudgets, onGo }: {
  role: Role;
  /** Opens a member's full profile page (linkable: `#agents/team/<kind>/<id>`). */
  onOpenProfile: (ref: ProfileRef) => void;
  onOpenAgent: (id: string) => void;
  onEditAgent: (agent: AgentProfile) => void;
  onCreate: () => void;
  onOpenApprovals?: () => void;
  onOpenTasks?: () => void;
  onOpenBudgets?: () => void;
  onGo?: (view: OrgGo) => void;
}) {
  const [sources, setSources] = useState<OrgSources | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'denied'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [updated, setUpdated] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [layout, setLayout] = useState<Layout>(readLayout);
  const [selected, setSelected] = useState<OrgMember | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportNote, setExportNote] = useState<{ text: string; error?: boolean } | null>(null);
  const owner = useOwner(role, sources?.people ?? null);
  const go = useCallback((view: OrgGo) => {
    if (view === 'approvals' && onOpenApprovals) return onOpenApprovals();
    if (view === 'tasks' && onOpenTasks) return onOpenTasks();
    if (onGo) return onGo(view);
    openByHash(view);
  }, [onGo, onOpenApprovals, onOpenTasks]);
  const load = useCallback(async (quiet = false) => {
    if (!quiet) { setState('loading'); setError(null); }
    setRefreshing(true);
    try {
      setSources(await loadOrgSources(api.apiRequest, Date.now(), quiet ? sources : null));
      setUpdated(Date.now()); setState('ready'); setError(null);
    } catch (e) {
      if (quiet && sources) { setError(t('org.err.refresh', { reason: describeError(e) })); return; }
      setError(describeError(e)); setState(isPermissionDenied(e) ? 'denied' : 'error');
    } finally { setRefreshing(false); }
  }, [sources]);
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => { void loadRef.current(); }, []);
  // Live: refresh while the page is visible, and as soon as it becomes visible again.
  useEffect(() => {
    const tick = () => { if (document.visibilityState === 'visible') void loadRef.current(true); };
    const timer = window.setInterval(tick, REFRESH_MS);
    document.addEventListener('visibilitychange', tick);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
  }, []);
  const chart = useMemo(() => sources ? buildOrgChart({ ...sources, aliases: ALIASES }) : null, [sources]);
  const setView = (next: Layout) => { setLayout(next); try { localStorage.setItem(LAYOUT_KEY, next); } catch { /* storage blocked */ } };
  const canCreate = role === 'owner' || role === 'member';

  async function exportImage() {
    if (!chart) return;
    setExporting(true); setExportNote(null);
    try {
      const { svg, width, height } = orgChartSvg(imageModel(chart, owner), readPalette());
      const blob = await svgToPng(svg, width, height);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = imageFileName(); document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      setExportNote({ text: t('org.export.done', { file: link.download }) });
    } catch (e) { setExportNote({ text: t('org.export.failed', { reason: e instanceof Error ? e.message : String(e) }), error: true }); }
    finally { setExporting(false); }
  }

  const head = <section className="moduleIntro orgIntro">
    <div><h2>{t('org.title')}</h2><p>{t('org.intro')}</p></div>
  </section>;
  const toolbar = chart && chart.members > 0 && <div className="orgToolbar">
    <div className="segmented" role="group" aria-label={t('org.layout')}>
      <button type="button" aria-pressed={layout === 'chart'} onClick={() => setView('chart')}><Network aria-hidden="true" />{t('org.layout.chart')}</button>
      <button type="button" aria-pressed={layout === 'table'} onClick={() => setView('table')}><LayoutList aria-hidden="true" />{t('org.layout.table')}</button>
    </div>
    <button type="button" className="secondary" onClick={() => void exportImage()} disabled={exporting}><Download aria-hidden="true" />{exporting ? t('org.export.working') : t('org.export.image')}</button>
    <button type="button" className="secondary" onClick={() => window.print()}><Printer aria-hidden="true" />{t('org.export.print')}</button>
    <p className="orgUpdated muted">
      {updated && <span>{t('org.updated', { time: formatDateTime(updated, { timeStyle: 'short' }) })}</span>}
      <button type="button" className="linkButton" onClick={() => void load(true)} disabled={refreshing}><RefreshCw aria-hidden="true" />{refreshing ? t('org.refreshing') : t('org.refresh')}</button>
    </p>
  </div>;

  if (state === 'loading') return <div className="orgChartView">{head}<OrgSkeleton /></div>;
  if (state === 'denied') return <div className="orgChartView">{head}<div className="emptyState" role="alert"><ShieldCheck aria-hidden="true" /><h3>{t('org.deniedTitle')}</h3><p>{t('org.deniedText')}</p>{error && <p className="muted">{error}</p>}</div></div>;
  if (state === 'error' || !chart || !sources) return <div className="orgChartView">{head}
    <div className="agentState" role="alert"><p className="notice errorNotice"><CircleDot aria-hidden="true" />{t('org.err.load', { reason: error ?? '' })}</p>
      <div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>
  </div>;

  const partial = [...new Set(sources.partial.map((p) => p.source))];
  return <div className="orgChartView">
    {head}
    {toolbar}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {exportNote && <p className={`notice ${exportNote.error ? 'errorNotice' : ''}`} role={exportNote.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{exportNote.text}</p>}
    {partial.length > 0 && <p className="notice" role="status"><AlertTriangle aria-hidden="true" />{t('org.partial', { what: partial.map((p) => t(`org.source.${p}` as MessageKey)).join(t('common.listSeparator')) })}</p>}
    {chart.members === 0
      ? <div className="emptyState"><Network aria-hidden="true" /><h3>{t('org.emptyTitle')}</h3><p>{canCreate ? t('org.emptyText') : t('org.emptyViewer')}</p>
          {canCreate && <div className="actions"><button type="button" onClick={onCreate}><Plus aria-hidden="true" />{t('org.createAgent')}</button></div>}</div>
      : layout === 'chart'
        ? <OrgTree chart={chart} owner={owner} onOpen={(m) => onOpenProfile({ kind: m.kind, id: m.id })} onPeek={setSelected} onGo={go} />
        : <OrgTable chart={chart} onSelect={(m) => onOpenProfile({ kind: m.kind, id: m.id })} onGo={go} />}
    {selected && <OrgMemberDrawer member={selected} sources={sources} role={role} onClose={() => setSelected(null)} onOpenAgent={onOpenAgent}
      onOpenProfile={(m) => { setSelected(null); onOpenProfile({ kind: m.kind, id: m.id }); }}
      onEditAgent={onEditAgent} onGo={go} onOpenBudgets={onOpenBudgets} onChanged={() => void load(true)} />}
    {chart.members > 0 && createPortal(<OrgPrint chart={chart} sources={sources} owner={owner} />, document.body)}
  </div>;
}

function OrgSkeleton() {
  return <div className="orgSkeleton" role="status" aria-live="polite">
    <span className="visuallyHidden">{t('org.loading')}</span>
    <div className="orgSkel orgSkelRoot" aria-hidden="true" /><div className="orgSkel orgSkelRoot" aria-hidden="true" />
    <div className="orgSkelRow" aria-hidden="true">{[0, 1, 2].map((i) => <div key={i} className="orgSkel orgSkelDept" />)}</div>
  </div>;
}

// ---------------------------------------------------------------------------- chart

function OrgTree({ chart, owner, onOpen, onPeek, onGo }: {
  chart: OrgChart; owner: Owner & { workspace: string | null }; onOpen: (m: OrgMember) => void; onPeek: (m: OrgMember) => void; onGo: (view: OrgGo) => void;
}) {
  const kpis = orgKpis(chart);
  const box = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(() => typeof window === 'undefined' ? 3 : columnsForWidth(Math.max(320, window.innerWidth - 320)));
  useEffect(() => {
    const element = box.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setColumns(columnsForWidth(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const stacks = balanceColumns(chart.departments, columns);
  const tile = (label: string, value: number | null, tone: string, action?: () => void, hint?: string) => {
    const shown = value === null ? '-' : String(value);
    return <div className={`orgKpi tone-${tone}`}>
      <dt>{label}</dt>
      <dd>{action ? <button type="button" className="orgKpiLink" onClick={action} aria-label={`${label}: ${shown}. ${hint ?? ''}`}>{shown}</button> : shown}</dd>
    </div>;
  };
  return <div className="orgTree" role="group" aria-label={t('org.treeLabel')}>
    <section className="surface orgBand" aria-label={t('org.band')}>
      <div className="orgBandChain">
        <article className="orgChip orgOwnerChip" aria-labelledby="org-owner-name">
          <span className="orgInitials" aria-hidden="true">{owner.initials}</span>
          <div className="orgNodeText">
            <h3 id="org-owner-name" dir="auto"><Crown aria-hidden="true" />{owner.name}</h3>
            <p className="muted">{owner.role}{owner.workspace ? <> · <bdi>{owner.workspace}</bdi></> : null}</p>
          </div>
        </article>
        <ChevronRight className="orgBandArrow flipRtl" aria-hidden="true" />
        <article className="orgChip orgAnumChip" aria-labelledby="org-anum-name">
          <span className="orgAnumMark" aria-hidden="true"><Sparkles /></span>
          <div className="orgNodeText">
            <h3 id="org-anum-name">ANUM <span className="orgAnumRole">{t('org.anum.role')}</span></h3>
            <p className="muted">{t('org.anum.duty')}</p>
          </div>
        </article>
        <ApprovalStrip chart={chart} onGo={onGo} />
      </div>
      <dl className="orgKpis">
        {tile(t('org.kpi.agents'), kpis.agents, 'neutral')}
        {tile(t('org.kpi.running'), kpis.running, kpis.running ? 'sky' : 'neutral')}
        {tile(t('org.kpi.approvals'), kpis.approvals, kpis.approvals ? 'warn' : 'neutral', kpis.approvals !== null ? () => onGo('approvals') : undefined, t('org.strip.open'))}
        {tile(t('org.kpi.failed'), kpis.failedToday, kpis.failedToday ? 'stop' : 'neutral')}
        {tile(t('org.kpi.tools'), kpis.toolsNotConnected, kpis.toolsNotConnected ? 'warn' : 'neutral', () => onGo('integrations'), t('org.kpi.toolsOpen'))}
        {tile(t('org.kpi.withoutAgent'), kpis.withoutAgent, 'neutral', () => onGo('tasks'), t('org.drawer.openTask'))}
      </dl>
    </section>
    <div className="orgBranches" ref={box}>
      <ul className="orgColumns" style={{ '--org-cols': stacks.length } as CSSProperties} aria-label={t('org.departments')}>
        {stacks.map((stack, i) => <li key={i} className="orgColumn">
          <ul className="orgStack">{stack.map((dept) => <DepartmentCard key={dept.key} dept={dept} onOpen={onOpen} onPeek={onPeek} onGo={onGo} />)}</ul>
        </li>)}
      </ul>
    </div>
  </div>;
}

function ApprovalStrip({ chart, onGo }: { chart: OrgChart; onGo: (view: OrgGo) => void }) {
  const waiting = chart.approvals;
  return <div className={`orgStrip ${waiting ? 'hasWaiting' : ''}`}>
    <ShieldCheck aria-hidden="true" />
    <span><strong>{t('org.strip.title')}</strong> {waiting === null ? t('org.strip.hidden') : waiting ? t('org.strip.waiting', { count: waiting }) : t('org.strip.none')}</span>
    {waiting !== null && <button type="button" className="linkButton" onClick={() => onGo('approvals')}>{t('org.strip.open')}</button>}
  </div>;
}

function DepartmentCard({ dept, onOpen, onPeek, onGo }: { dept: OrgDepartment; onOpen: (m: OrgMember) => void; onPeek: (m: OrgMember) => void; onGo: (view: OrgGo) => void }) {
  const [open, setOpen] = useState(() => typeof window === 'undefined' || !window.matchMedia?.('(max-width: 767px)').matches);
  const [all, setAll] = useState(false);
  const bodyId = useId();
  const agents = dept.members.filter((m) => m.kind === 'agent');
  const others = dept.members.filter((m) => m.kind !== 'agent');
  const { shown, hidden } = visibleMembers({ members: agents }, all);
  const counts = departmentCounts(dept);
  const health = departmentHealth(dept);
  const lead = departmentLead(dept);
  const healthText = health.tone === 'stop' ? t('org.health.attention', { count: health.attention })
    : health.tone === 'warn' ? t('org.count.approvals', { count: health.approvals }) : t('org.health.ok');
  return <li className="orgDept">
    <section className={`surface orgDeptCard health-${health.tone}`} aria-labelledby={`${bodyId}-h`}>
      <h3 id={`${bodyId}-h`} className="orgDeptHead">
        <button type="button" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen(!open)}>
          <span className="orgDeptName" dir="auto">{departmentLabel(dept)}</span>
          <span className={`orgHealth tone-${health.tone}`}><span className="orgDot" aria-hidden="true" />{healthText}</span>
          <span className="orgDeptStats">{[
            t('org.dept.agents', { count: counts.agents }),
            counts.assistants ? t('org.dept.assistants', { count: counts.assistants }) : '',
            counts.people ? t('org.dept.people', { count: counts.people }) : '',
            dept.running ? t('org.count.running', { count: dept.running }) : '',
          ].filter(Boolean).join(' · ')}</span>
          <ChevronDown className="orgChevron" aria-hidden="true" />
        </button>
      </h3>
      <div id={bodyId} hidden={!open} className="orgDeptBody">
        <p className="orgLead"><span className="muted">{t('org.dept.lead')}</span>{' '}
          {lead ? <><button type="button" className="linkButton" onClick={() => onOpen(lead)} dir="auto">{lead.name}</button> <span className="muted">· {t(`org.kind.${lead.kind}` as MessageKey)}</span></>
            : <span className="muted">{t('org.dept.noLead')}</span>}</p>
        {shown.length > 0 && <ul className="orgMembers">{shown.map((m) => <li key={m.id}><MemberNode member={m} onOpen={onOpen} onPeek={onPeek} onGo={onGo} /></li>)}</ul>}
        {(hidden > 0 || all) && agents.length > 4 && <button type="button" className="secondary orgShowAll" aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? t('org.showFewer') : t('org.showAll', { count: agents.length })}</button>}
        {others.length > 0 && <ul className="orgCompact" aria-label={t('org.dept.others')}>{others.map((m) => <li key={`${m.kind}:${m.id}`}>
          {m.kind === 'person' ? <span className="agentAvatar orgAvatar xs orgPerson" aria-hidden="true"><UserRound /></span> : <Avatar icon={m.icon} color={m.color} small />}
          <span className="orgCompactText">
            <button type="button" className="orgOpen" onClick={() => onOpen(m)} dir="auto">{m.name}</button>
            <small className="muted" dir="auto">{roleText(m)}</small>
          </span>
          <StatusPill member={m} />
        </li>)}</ul>}
      </div>
    </section>
  </li>;
}

/** An agent's role line: kind, category and who can use it. */
export function agentRole(member: OrgMember): string {
  if (!member.agent) return roleText(member);
  return [t('org.kind.agent'), t(`agents.category.${member.agent.category}` as MessageKey), t(`agents.sharing.${member.agent.sharing}` as MessageKey)].join(' · ');
}

function MemberNode({ member, onOpen, onPeek, onGo }: { member: OrgMember; onOpen: (m: OrgMember) => void; onPeek: (m: OrgMember) => void; onGo: (view: OrgGo) => void }) {
  const skills = member.skills.slice(0, 2);
  const tools = member.tools.slice(0, 3);
  const meter = (value: number, label: string, tone: string) => <li className={value ? `tone-${tone}` : ''}><strong>{value}</strong> {label}</li>;
  return <article className={`orgMember is-${member.kind} status-${member.status}`}>
    <div className="orgMemberHead">
      <Avatar icon={member.icon} color={member.color} small />
      <h4 dir="auto"><button type="button" className="orgOpen" onClick={() => onOpen(member)}>{member.name}</button></h4>
      <button type="button" className="iconButton orgPeek" onClick={() => onPeek(member)} aria-label={t('org.peek', { name: member.name })} title={t('org.peek', { name: member.name })}><Eye aria-hidden="true" /></button>
    </div>
    <p className="orgRole"><StatusPill member={member} /><span>{agentRole(member)}</span></p>
    <p className="orgMission" dir="auto" title={member.description}>{member.description || t('agents.noDescription')}</p>
    <ul className="orgMeters" aria-label={t('org.col.work')}>
      {meter(member.running, t('org.meter.running'), 'sky')}
      {meter(member.approvals, t('org.meter.approvals'), 'warn')}
      {meter(member.failed, t('org.meter.failed'), 'stop')}
    </ul>
    {member.unavailable > 0 && <p className="orgWarn"><AlertTriangle aria-hidden="true" />{t('org.unavailable', { count: member.unavailable })}</p>}
    <div className="orgChips">
      {skills.map((s) => <span key={s.id} className="orgSkill" dir="auto"><span className="visuallyHidden">{t('org.skills')}: </span>{s.name}</span>)}
      {member.skills.length > 2 && <span className="orgSkill isMore">{t('org.more', { count: member.skills.length - 2 })}</span>}
      {tools.map((chip) => <ToolButton key={chip.key} chip={chip} onGo={onGo} />)}
      {member.tools.length > 3 && <span className="orgSkill isMore">{t('org.more', { count: member.tools.length - 3 })}</span>}
      {skills.length === 0 && tools.length === 0 && <span className="muted orgNone">{t('org.noSkillsTools')}</span>}
    </div>
  </article>;
}

export function ToolButton({ chip, onGo }: { chip: ToolChip; onGo: (view: OrgGo) => void }) {
  const label = chipLabel(chip);
  return <button type="button" className={`orgTool tool-${chip.status}`} onClick={() => onGo('integrations')}
    aria-label={t('org.toolButton', { name: label, status: chipStatus(chip) })} title={`${chip.tools.join(', ')} · ${chipStatus(chip)}`}>
    <span className="orgDot" aria-hidden="true" />{label}
  </button>;
}

// ---------------------------------------------------------------------------- table

function OrgTable({ chart, onSelect, onGo }: { chart: OrgChart; onSelect: (m: OrgMember) => void; onGo: (view: OrgGo) => void }) {
  const [filter, setFilter] = useState<MemberFilter>(NO_FILTER);
  const rows = useMemo(() => filterMembers(chart, filter), [chart, filter]);
  const total = chart.departments.reduce((sum, d) => sum + d.members.length, 0);
  const tools = useMemo(() => toolOptions(chart), [chart]);
  const searchId = useId();
  const set = <K extends keyof MemberFilter>(key: K, value: MemberFilter[K]) => setFilter((old) => ({ ...old, [key]: value }));
  const filtered = JSON.stringify(filter) !== JSON.stringify(NO_FILTER);
  return <section className="orgTableWrap" aria-label={t('org.layout.table')}>
    <div className="agentFilters orgFilters">
      <div className="agentSearch">
        <label className="visuallyHidden" htmlFor={searchId}>{t('org.filter.search')}</label>
        <Search aria-hidden="true" />
        <input id={searchId} type="search" value={filter.q} placeholder={t('org.filter.searchPlaceholder')} onChange={(e) => set('q', e.target.value)} />
      </div>
      <select aria-label={t('org.filter.department')} value={filter.department} onChange={(e) => set('department', e.target.value)}>
        <option value="all">{t('org.filter.allDepartments')}</option>
        {chart.departments.map((d) => <option key={d.key} value={d.key}>{departmentLabel(d)}</option>)}
      </select>
      <select aria-label={t('org.filter.status')} value={filter.status} onChange={(e) => set('status', e.target.value as MemberFilter['status'])}>
        <option value="all">{t('org.filter.allStatuses')}</option>
        {(['approval', 'error', 'running', 'idle', 'paused', 'off'] as const).map((s) => <option key={s} value={s}>{t(`org.status.${s}` as MessageKey)}</option>)}
      </select>
      <select aria-label={t('org.filter.tool')} value={filter.tool} onChange={(e) => set('tool', e.target.value)}>
        <option value="all">{t('org.filter.allTools')}</option>
        {tools.map((chip) => <option key={chip.key} value={chip.key}>{chipLabel(chip)}</option>)}
      </select>
      <select aria-label={t('org.filter.kind')} value={filter.kind} onChange={(e) => set('kind', e.target.value as MemberFilter['kind'])}>
        <option value="all">{t('org.filter.allKinds')}</option>
        {(['agent', 'assistant', 'person'] as const).map((k) => <option key={k} value={k}>{t(`org.kind.${k}` as MessageKey)}</option>)}
      </select>
    </div>
    <p className="muted orgShowing" role="status">{t('org.showing', { shown: rows.length, total })}</p>
    {rows.length === 0 ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('org.noMatch')}</h3>
      {filtered && <button type="button" className="secondary" onClick={() => setFilter(NO_FILTER)}>{t('agents.clearFilters')}</button>}</div>
      : <table className="orgTable">
        <caption className="visuallyHidden">{t('org.tableCaption')}</caption>
        <thead><tr>
          <th scope="col">{t('org.col.name')}</th><th scope="col">{t('org.col.department')}</th><th scope="col">{t('org.col.role')}</th>
          <th scope="col">{t('org.col.status')}</th><th scope="col">{t('org.col.work')}</th><th scope="col">{t('org.col.skills')}</th><th scope="col">{t('org.col.tools')}</th>
        </tr></thead>
        <tbody>{rows.map((m) => <tr key={`${m.kind}:${m.id}`}>
          <th scope="row" data-label={t('org.col.name')}><button type="button" className="orgOpen" onClick={() => onSelect(m)} dir="auto">{m.name}</button>
            <small className="muted">{t(`org.kind.${m.kind}` as MessageKey)}</small></th>
          <td data-label={t('org.col.department')} dir="auto">{departmentLabel(m.department)}</td>
          <td data-label={t('org.col.role')} dir="auto" className="orgCellRole">{roleText(m)}</td>
          <td data-label={t('org.col.status')}><StatusPill member={m} /></td>
          <td data-label={t('org.col.work')}>{countsText(m) || '-'}</td>
          <td data-label={t('org.col.skills')} dir="auto">{m.skills.length ? m.skills.map((s) => s.name).join(t('common.listSeparator')) : '-'}</td>
          <td data-label={t('org.col.tools')}>{m.tools.length ? <ul className="orgTools">{m.tools.map((chip) => <li key={chip.key}><ToolButton chip={chip} onGo={onGo} /></li>)}</ul> : '-'}</td>
        </tr>)}</tbody>
      </table>}
  </section>;
}

// ---------------------------------------------------------------------------- image and print

function memberSkills(m: OrgMember): string {
  return m.skills.length ? `${t('org.skills')}: ${m.skills.map((s) => s.name).join(t('common.listSeparator'))}` : '';
}

function memberTools(m: OrgMember): string {
  return m.tools.length ? `${t('org.tools')}: ${m.tools.map((c) => `${chipLabel(c)} (${chipStatus(c)})`).join(t('common.listSeparator'))}` : '';
}

export function imageModel(chart: OrgChart, owner: Owner & { workspace: string | null }): ImageModel {
  const date = formatDateTime(Date.now(), { dateStyle: 'long' });
  return {
    title: t('org.image.title'),
    subtitle: [owner.workspace, date].filter(Boolean).join(' · '),
    owner: { name: owner.name, role: owner.role, initials: owner.initials },
    anum: { name: 'ANUM', role: t('org.anum.role'), stats: [t('org.anum.members', { count: chart.members }), t('org.count.running', { count: chart.running })].join(' · ') },
    departments: chart.departments.map((d) => ({
      name: departmentLabel(d),
      stats: [t('org.dept.agents', { count: departmentCounts(d).agents }), departmentCounts(d).assistants ? t('org.dept.assistants', { count: departmentCounts(d).assistants }) : '',
        departmentCounts(d).people ? t('org.dept.people', { count: departmentCounts(d).people }) : '', d.running ? t('org.count.running', { count: d.running }) : '', d.approvals ? t('org.count.approvals', { count: d.approvals }) : ''].filter(Boolean).join(' · '),
      members: d.members.map((m) => ({ name: m.name, role: roleText(m), status: m.kind === 'person' ? 'person' : m.status, statusLabel: statusText(m), counts: countsText(m), skills: memberSkills(m), tools: memberTools(m) })),
    })),
    strip: chart.approvals === null ? t('org.strip.title') : `${t('org.strip.title')} · ${chart.approvals ? t('org.strip.waiting', { count: chart.approvals }) : t('org.strip.none')}`,
    footer: t('org.image.footer'),
    rtl: getLocale() === 'ar',
  };
}

/** Every member's full profile as a document; shown only when printing (orgChart.css). */
function OrgPrint({ chart, sources, owner }: { chart: OrgChart; sources: OrgSources; owner: Owner & { workspace: string | null } }) {
  const people = new Map((sources.people ?? []).map((p) => [p.user_id, p.display_name]));
  return <div className="orgPrintRoot" dir={getLocale() === 'ar' ? 'rtl' : 'ltr'} aria-hidden="true">
    <h1>{t('org.image.title')}</h1>
    <p>{[owner.name, owner.workspace, formatDateTime(Date.now())].filter(Boolean).join(' · ')}</p>
    <p>{t('org.anum.role')} · {t('org.anum.members', { count: chart.members })} · {chart.approvals === null ? t('org.strip.hidden') : t('org.strip.waiting', { count: chart.approvals })}</p>
    {chart.departments.map((d) => <section key={d.key} className="orgPrintDept">
      <h2>{departmentLabel(d)}</h2>
      {d.members.map((m) => <article key={`${m.kind}:${m.id}`} className="orgPrintMember">
        <h3>{m.name} · {statusText(m)}</h3>
        <p>{t(`org.kind.${m.kind}` as MessageKey)} · {roleText(m)}</p>
        {countsText(m) && <p>{countsText(m)}</p>}
        {m.agent && <>
          <dl>
            <dt>{t('org.drawer.owner')}</dt><dd>{m.agent.owner_user_id === api.currentUserId() ? t('agents.you') : people.get(m.agent.owner_user_id) ?? m.agent.owner_user_id}</dd>
            <dt>{t('org.col.status')}</dt><dd>{t(`agents.status.${m.agent.status}` as MessageKey)} · {t(`agents.sharing.${m.agent.sharing}` as MessageKey)}</dd>
            <dt>{t('agents.field.instructions')}</dt><dd>{m.agent.instructions || t('agents.noInstructions')}</dd>
            <dt>{t('org.skills')}</dt><dd>{m.skills.map((s) => s.name).join(t('common.listSeparator')) || t('agents.none')}</dd>
            <dt>{t('org.tools')}</dt><dd>{m.tools.map((c) => `${chipLabel(c)} (${chipStatus(c)}): ${c.tools.join(', ')}`).join('; ') || t('agents.none')}</dd>
            <dt>{t('org.drawer.rules')}</dt><dd>{autonomyRules(m.agent, sources.catalog).map((r) => `${t(`agents.toolCategory.${r.category}` as MessageKey)}: ${t(`agents.level.${r.level}` as MessageKey)}`).join('; ')}</dd>
          </dl>
        </>}
        {m.assistant && <dl>
          <dt>{t('agents.field.instructions')}</dt><dd>{m.assistant.instructions || t('agents.noInstructions')}</dd>
          <dt>{t('org.tools')}</dt><dd>{m.assistant.allowed_tools.join(', ')}</dd>
          <dt>{t('org.drawer.keywords')}</dt><dd>{m.assistant.routing_keywords.join(', ') || t('agents.none')}</dd>
        </dl>}
      </article>)}
    </section>)}
  </div>;
}

export function Section({ title, children, id }: { title: string; children: ReactNode; id: string }) {
  return <section className="orgDrawerSection" aria-labelledby={id}><h3 id={id}>{title}</h3>{children}</section>;
}
