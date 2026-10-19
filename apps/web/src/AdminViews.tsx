// Owner screens: workspace members and invitations, monthly model budgets, and the approval policy.
// Everything shown comes from the API. Who may manage is decided by the API (its 403, or the
// caller's persisted membership role), never guessed from the token here.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowRightLeft, Ban, CircleDot, Copy, Gauge, KeyRound, Link2, Lock, MailPlus, RefreshCw, RotateCcw, Save, Scale, Search, Send, ShieldCheck, Ticket, UserCheck, Users, XCircle } from 'lucide-react';
import type { CreatedInvitation, InvitationEmailSetup, MembersSummary, ModelBudgetOverview, ModelBudgetScopeView, WorkspaceApprovalPolicy, WorkspaceInvitation, WorkspaceMember, WorkspaceRole, BudgetScope } from '@anum/contracts';
import * as admin from './lib/adminApi';
import { currentScope, getCurrentMembership, rememberWorkspace } from './lib/api';
import { POLICY_SWITCHES, policyChanged, policyDraft, policyUpdatedLabel, twoPersonWarning, type PolicyDraft } from './lib/policy';
import {
  DEFAULT_TTL_HOURS,
  ROLES,
  budgetMeters,
  expiryLabel,
  formatResetDate,
  formatTokens,
  invitationEmailNotice,
  invitationLink,
  invitationRequest,
  inviteeLabel,
  limitDraft,
  parseInvitationInput,
  parseLimits,
  type LimitDraft,
} from './lib/admin';
import { describeError, isPermissionDenied } from './lib/errors';
import { isOnlyActiveOwner, type InvitationStatusFilter, type MemberStatusFilter } from './lib/audit';
import { ConfirmDialog } from './Dialog';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { AccessNotice } from './PageParts';
import type { TenantWriteAccess } from './lib/governance';
import { tenantWriteAccess } from './lib/governance';
import './admin.css';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;

const when = (value: string) => formatDateTime(value);
/** "Owner", "Member", "Viewer" in the page language (members.roleOption.*). */
const roleLabel = (role: WorkspaceRole) => t(`members.roleOption.${role}`);
const notAllowed = (error: unknown) => isPermissionDenied(error) ? t('admin.notAllowed', { message: describeError(error) }) : describeError(error);

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function Forbidden({ title, detail, children }: { title: string; detail: string | null; children: ReactNode }) {
  return <section className="surface adminForbidden" aria-label={title}>
    <span className="adminIcon" aria-hidden="true"><Lock /></span>
    <div><h3>{title}</h3><p>{children}</p>{detail && <p className="muted">{t('admin.apiAnswered', { detail })}</p>}</div>
  </section>;
}

async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

// ------------------------------------------------------------------------- members

/** Pending invitation from a link: read once, then removed from the address bar and history. */
function takeInvitationFromLocation(): { token: string; workspaceId: string | null; tenantId?: string | null } | null {
  if (!location.hash.startsWith('#invitation=')) return null;
  const parsed = parseInvitationInput(location.hash);
  history.replaceState(null, '', '#members');
  return parsed;
}

type PendingChange =
  | { kind: 'role'; member: WorkspaceMember; role: WorkspaceRole }
  | { kind: 'deactivate'; member: WorkspaceMember }
  | { kind: 'revoke'; invitation: WorkspaceInvitation }
  | null;

const INVITATION_FILTERS: InvitationStatusFilter[] = ['pending', 'expired', 'accepted', 'revoked', 'all'];
const roleName = (role: WorkspaceRole) => t(`members.role.${role}` as MessageKey);

export function MembersView({ workspaceId, onSwitchWorkspace, onSwitchOrganisation }: { workspaceId: string; onSwitchWorkspace?: (workspaceId: string) => Promise<void>; onSwitchOrganisation?: (tenantId: string, workspaceId: string) => Promise<void> }) {
  const [summary, setSummary] = useState<MembersSummary | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [invitationFilter, setInvitationFilter] = useState<InvitationStatusFilter>('pending');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<MemberStatusFilter>('all');
  const [roleFilter, setRoleFilter] = useState<WorkspaceRole | 'all'>('all');
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [listState, setListState] = useState<'idle' | 'loading' | 'more' | { error: string }>('idle');
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  const [pendingChange, setPendingChange] = useState<PendingChange>(null);
  const [linked] = useState(takeInvitationFromLocation);
  const listRequest = useRef(0);

  // Debounced search: the API filters by user id and email.
  useEffect(() => { const timer = setTimeout(() => setSearch(query), 300); return () => clearTimeout(timer); }, [query]);

  const loadMembers = useCallback(async (append: string | null) => {
    const id = ++listRequest.current;
    setListState(append ? 'more' : 'loading');
    try {
      const page = await admin.getMembersPage({ q: search, status: statusFilter, role: roleFilter, cursor: append });
      if (id !== listRequest.current) return;
      setMembers((current) => append ? [...current, ...page.items] : page.items);
      setCursor(page.nextCursor);
      setListState('idle');
      setLoad({ kind: 'ready' });
    } catch (error) {
      if (id !== listRequest.current) return;
      if (isPermissionDenied(error)) setLoad({ kind: 'forbidden', detail: describeError(error) });
      else if (append) setListState({ error: describeError(error) });
      else setLoad({ kind: 'error', detail: describeError(error) });
    }
  }, [search, statusFilter, roleFilter]);

  const loadSide = useCallback(async () => {
    try {
      const [nextSummary, nextInvitations] = await Promise.all([admin.getMembersSummary(), admin.getInvitations(invitationFilter)]);
      setSummary(nextSummary);
      setInvitations(nextInvitations);
    } catch (error) {
      if (isPermissionDenied(error)) setLoad({ kind: 'forbidden', detail: describeError(error) });
      else setFlash({ text: t('members.sideError', { message: describeError(error) }), error: true });
    }
  }, [invitationFilter]);

  useEffect(() => { void loadMembers(null); }, [loadMembers]);
  useEffect(() => { void loadSide(); }, [loadSide]);
  const refresh = useCallback(async () => { await Promise.all([loadMembers(null), loadSide()]); }, [loadMembers, loadSide]);

  async function act(key: string, action: () => Promise<string>): Promise<boolean> {
    setBusy(key);
    setFlash(null);
    try {
      setFlash({ text: await action() });
      await refresh();
      return true;
    } catch (error) {
      // 403 from a mutation also means "not an owner": show the API's reason in place.
      setFlash({ text: notAllowed(error), error: true });
      return false;
    } finally {
      setBusy(null);
    }
  }

  function confirmChange() {
    const change = pendingChange;
    if (!change) return;
    const done = () => setPendingChange(null);
    if (change.kind === 'role') {
      void act(`member:${change.member.userId}`, async () => { await admin.changeMemberRole(change.member.userId, change.role); return t('members.roleChanged', { user: change.member.userId, role: roleName(change.role) }); }).then(done);
    } else if (change.kind === 'deactivate') {
      void act(`member:${change.member.userId}`, async () => { await admin.setMemberActive(change.member.userId, false); return t('members.deactivated', { user: change.member.userId }); }).then(done);
    } else {
      void act(`invitation:${change.invitation.id}`, async () => { await admin.revokeInvitation(change.invitation.id); return t('members.revoked', { invitee: inviteeLabel(change.invitation) }); }).then(done);
    }
  }

  const filtersActive = search.trim() !== '' || statusFilter !== 'all' || roleFilter !== 'all';
  const clearFilters = () => { setQuery(''); setSearch(''); setStatusFilter('all'); setRoleFilter('all'); };

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('memberPage.eyebrow')}</p><h2>{t('memberPage.title')}</h2><p>{t('memberPage.intro', { workspace: workspaceId })}</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true" />{t('admin.refresh')}</button>
    </section>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {load.kind === 'loading' && <Notice>{t('memberPage.loading')}</Notice>}
    {load.kind === 'error' && <section className="surface adminForbidden" aria-label={t('members.unavailable')}>
      <span className="adminIcon"><Users aria-hidden="true" /></span>
      <div><h3>{t('members.errorTitle')}</h3><p>{load.detail}</p><p className="muted">{t('audit.errorHelp')}</p>
        <div className="actions"><button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>
    </section>}
    {load.kind === 'forbidden' && <Forbidden title={t('admin.ownerRequired')} detail={load.detail}>
      {t('memberPage.forbidden')}
    </Forbidden>}
    {load.kind === 'ready' && <>
      <section className="metrics">
        <AdminMetric icon={<Users />} label={t('memberPage.metric.active')} value={summary?.active ?? 0} detail={t('memberPage.metric.activeDetail')} />
        <AdminMetric icon={<ShieldCheck />} label={t('memberPage.metric.owners')} value={summary?.activeOwners ?? 0} detail={t('memberPage.metric.ownersDetail')} />
        <AdminMetric icon={<Ticket />} label={t('memberPage.metric.pending')} value={summary?.pendingInvitations ?? 0} detail={t('members.pendingDetail')} />
        <AdminMetric icon={<Ban />} label={t('memberPage.metric.deactivated')} value={summary?.deactivated ?? 0} detail={t('memberPage.metric.deactivatedDetail')} />
      </section>
      <section className="surface adminPanel" aria-label={t('memberPage.region')}>
        <div className="sectionHeader"><div><p className="eyebrow">{t('memberPage.listEyebrow')}</p><h2>{summary ? t('memberPage.count', { count: summary.total }) : t('memberPage.region')}</h2></div></div>
        <div className="memberFilters" role="search" aria-label={t('members.find')}>
          <label className="field memberSearch"><span>{t('common.search')}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('members.searchPlaceholder')} autoComplete="off" spellCheck={false} /></label>
          <label className="field"><span>{t('members.status')}</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as MemberStatusFilter)}><option value="all">{t('members.status.all')}</option><option value="active">{t('members.status.active')}</option><option value="deactivated">{t('members.status.deactivated')}</option></select></label>
          <label className="field"><span>{t('members.roleFilter')}</span><select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value as WorkspaceRole | 'all')}><option value="all">{t('members.allRoles')}</option>{ROLES.map((r) => <option key={r} value={r}>{t(`members.roleOption.${r}` as MessageKey)}</option>)}</select></label>
        </div>
        {listState === 'loading' && <p className="muted" role="status">{t('members.searching')}</p>}
        {!members.length && listState !== 'loading' && (filtersActive
          ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('members.noMatchTitle')}</h3><p>{t('members.noMatchText')}</p><button type="button" className="secondary" onClick={clearFilters}>{t('members.clearFilters')}</button></div>
          : <Empty title={t('memberPage.emptyTitle')} text={t('memberPage.emptyText')} />)}
        <div className="memberList">
          {members.map((member) => {
            const last = isOnlyActiveOwner(summary, member);
            const key = `member:${member.userId}`;
            const hintId = `last-owner-${member.userId}`;
            return <article className={`memberRow ${member.active ? '' : 'inactive'}`} key={member.userId} aria-label={t('memberPage.rowLabel', { user: member.userId }, { isolate: false })}>
              <span className="adminAvatar" aria-hidden="true">{member.userId.slice(0, 2).toUpperCase()}</span>
              <div className="memberMain">
                <strong dir="ltr">{member.userId}</strong>
                {member.email && <small dir="ltr">{member.email}</small>}
                <small>{t('memberPage.joined', { joined: when(member.createdAt), updated: when(member.updatedAt) })}</small>
                {last && <small className="memberHint" id={hintId}>{t('members.lastOwner')}</small>}
              </div>
              <span className={`statusBadge ${member.active ? 'success' : 'neutral'}`}>{member.active ? t('memberPage.badge.active') : t('memberPage.badge.deactivated')}</span>
              <label className="memberRole"><span className="visuallyHidden">{t('memberPage.roleFor', { user: member.userId })}</span>
                <select aria-label={t('memberPage.roleFor', { user: member.userId }, { isolate: false })} aria-describedby={last ? hintId : undefined} value={member.role} disabled={busy !== null} onChange={(event) => {
                  const next = event.target.value as WorkspaceRole;
                  if (next !== member.role) setPendingChange({ kind: 'role', member, role: next });
                }}>
                  {ROLES.map((role) => <option key={role} value={role} disabled={last && role !== 'owner'}>{roleLabel(role)}</option>)}
                </select>
              </label>
              {member.active
                ? <button type="button" className="secondary" disabled={busy !== null || last} aria-describedby={last ? hintId : undefined} onClick={() => setPendingChange({ kind: 'deactivate', member })}><Ban aria-hidden="true" />{t('members.deactivate')}</button>
                : <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(key, async () => { await admin.setMemberActive(member.userId, true); return t('memberPage.reactivated', { user: member.userId }); })}><RotateCcw aria-hidden="true" />{t('memberPage.reactivate')}</button>}
            </article>;
          })}
        </div>
        {cursor && <div className="actions memberMore"><button type="button" className="secondary" disabled={listState === 'more'} onClick={() => void loadMembers(cursor)}>{listState === 'more' ? t('common.loading') : t('members.loadMore')}</button></div>}
        {typeof listState === 'object' && <Notice error>{t('members.moreError', { message: listState.error })}</Notice>}
      </section>
      <div className="adminGrid">
        <InviteForm busy={busy !== null} onCreate={(request) => act('invite', async () => {
          const result = await admin.createInvitation(request);
          setCreated(result);
          return t(result.email.status === 'sent' ? 'memberPage.invitationEmailed' : 'memberPage.invitationCreated', { to: result.email.to ?? '' });
        })} />
        {created && <TokenReveal created={created} onDone={() => setCreated(null)} />}
      </div>
      <section className="surface adminPanel" aria-label={t('memberPage.invitations')}>
        <div className="sectionHeader"><div><p className="eyebrow">{t('memberPage.invitations')}</p><h2>{t('memberPage.invitations')}</h2></div>
          <div className="segmented" role="group" aria-label={t('members.showInvitations')}>
            {INVITATION_FILTERS.map((option) => <button type="button" key={option} aria-pressed={invitationFilter === option} onClick={() => setInvitationFilter(option)}>{t(`members.invitations.${option}` as MessageKey)}</button>)}
          </div>
        </div>
        {!invitations.length && <Empty title={t(`memberPage.noInvitations.${invitationFilter}`)} text={invitationFilter === 'pending' || invitationFilter === 'all' ? t('memberPage.noInvitationsHint') : t('memberPage.otherFilter')} />}
        <div className="memberList">
          {invitations.map((invitation) => {
            const open = invitation.status === 'pending' || invitation.status === 'expired';
            return <article className="memberRow invitationRow" key={invitation.id} aria-label={t('memberPage.invitationFor', { invitee: inviteeLabel(invitation) }, { isolate: false })}>
              <span className="adminIcon"><MailPlus /></span>
              <div className="memberMain">
                <strong dir="auto">{inviteeLabel(invitation)}</strong>
                <small>{t('memberPage.invitationMeta', { role: roleLabel(invitation.role), creator: invitation.createdByUserId, state: invitationState(invitation) })}</small>
              </div>
              <span className={`statusBadge ${invitation.status === 'pending' ? 'warning' : invitation.status === 'accepted' ? 'success' : 'neutral'}`}>{t(`memberPage.invStatus.${invitation.status}`)}</span>
              {open
                ? <div className="invitationActions">
                    <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(`invitation:${invitation.id}`, async () => {
                      const result = await admin.resendInvitation(invitation.id);
                      setCreated(result);
                      return t(result.email.status === 'sent' ? 'members.resentEmailed' : 'members.resent', { invitee: inviteeLabel(invitation) });
                    })}><Send aria-hidden="true" />{invitation.status === 'expired' ? t('members.renew') : t('members.resend')}</button>
                    {invitation.status === 'pending' && <button type="button" className="danger" disabled={busy !== null} onClick={() => setPendingChange({ kind: 'revoke', invitation })}><XCircle aria-hidden="true" />{t('memberPage.revoke')}</button>}
                  </div>
                : <span />}
            </article>;
          })}
        </div>
      </section>
    </>}
    <AcceptInvitation initial={linked} currentWorkspaceId={workspaceId} onAccepted={() => void refresh()} onSwitchWorkspace={onSwitchWorkspace} onSwitchOrganisation={onSwitchOrganisation} />
    {pendingChange && <ConfirmDialog
      title={pendingChange.kind === 'role' ? t('members.confirmRoleTitle', { user: pendingChange.member.userId, role: roleName(pendingChange.role) }, { isolate: false }) : pendingChange.kind === 'deactivate' ? t('members.confirmDeactivateTitle', { user: pendingChange.member.userId }, { isolate: false }) : t('members.confirmRevokeTitle', { invitee: inviteeLabel(pendingChange.invitation) }, { isolate: false })}
      confirmLabel={pendingChange.kind === 'role' ? t('members.changeRole') : pendingChange.kind === 'deactivate' ? t('members.deactivate') : t('members.revokeInvitation')}
      danger={pendingChange.kind !== 'role'}
      busy={busy !== null}
      onCancel={() => setPendingChange(null)}
      onConfirm={confirmChange}>
      {pendingChange.kind === 'role' && <p>{t('members.confirmRoleText', { from: roleName(pendingChange.member.role), to: roleName(pendingChange.role), effect: t(`members.effect.${pendingChange.role}` as MessageKey) })}</p>}
      {pendingChange.kind === 'deactivate' && <p>{t('members.confirmDeactivateText')}</p>}
      {pendingChange.kind === 'revoke' && <p>{t('members.confirmRevokeText')}</p>}
    </ConfirmDialog>}
  </>;
}

/** When an invitation was accepted, revoked or expired, for its row. */
function invitationState(invitation: WorkspaceInvitation): string {
  if (invitation.status === 'pending') return expiryLabel(invitation.expiresAt);
  if (invitation.status === 'accepted' && invitation.acceptedAt) {
    return invitation.acceptedByUserId
      ? t('memberPage.acceptedAtBy', { when: when(invitation.acceptedAt), user: invitation.acceptedByUserId })
      : t('memberPage.acceptedAt', { when: when(invitation.acceptedAt) });
  }
  if (invitation.status === 'revoked' && invitation.revokedAt) return t('memberPage.revokedAt', { when: when(invitation.revokedAt) });
  return t('memberPage.expiredAt', { when: when(invitation.expiresAt) });
}

function InviteForm({ busy, onCreate }: { busy: boolean; onCreate: (request: Parameters<typeof admin.createInvitation>[0]) => Promise<boolean> }) {
  const [role, setRole] = useState<WorkspaceRole>('member');
  const [userId, setUserId] = useState('');
  const [email, setEmail] = useState('');
  const [ttl, setTtl] = useState(String(DEFAULT_TTL_HOURS));
  const [error, setError] = useState<string | null>(null);
  const [sendEmail, setSendEmail] = useState(true);
  // Whether this server can email invitations; unknown (null) until asked or when asking failed.
  const [setup, setSetup] = useState<InvitationEmailSetup | null>(null);
  useEffect(() => { void admin.getInvitationEmailSetup().then(setSetup).catch(() => setSetup(null)); }, []);
  const hasEmail = email.trim().length > 0;
  return <form className="surface adminPanel" aria-label={t('invite.title')} noValidate onSubmit={(event) => {
    event.preventDefault();
    const result = invitationRequest({ role, userId, email, ttlHours: ttl });
    if (!result.ok) { setError(result.error); return; }
    setError(null);
    void onCreate(sendEmail ? result.value : { ...result.value, sendEmail: false }).then((ok) => { if (ok) { setUserId(''); setEmail(''); } });
  }}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('invite.eyebrow')}</p><h2>{t('invite.title')}</h2></div></div>
    <p className="muted">{t('invite.help')}</p>
    <div className="adminFields">
      <label className="field"><span>{t('invite.userId')}</span><input dir="ltr" value={userId} onChange={(event) => setUserId(event.target.value)} placeholder={t('invite.userIdPlaceholder')} autoComplete="off" /></label>
      <label className="field"><span>{t('invite.email')}</span><input dir="ltr" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" autoComplete="off" /></label>
      <label className="field"><span>{t('invite.role')}</span><select value={role} onChange={(event) => setRole(event.target.value as WorkspaceRole)}>{ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}</select></label>
      <label className="field"><span>{t('invite.ttl')}</span><input inputMode="numeric" value={ttl} onChange={(event) => setTtl(event.target.value)} /></label>
    </div>
    {hasEmail && setup?.configured && <div className="settingToggle inviteEmailToggle">
      <div><label htmlFor="invite-send-email"><strong>{t('invite.email.toggle')}</strong></label><p id="invite-send-email-hint">{setup.linkBase ? t('invite.email.toggleHint') : t('invite.email.toggleHintToken')}</p></div>
      <input id="invite-send-email" type="checkbox" checked={sendEmail} aria-describedby="invite-send-email-hint" onChange={(event) => setSendEmail(event.target.checked)} />
    </div>}
    {hasEmail && setup && !setup.configured && <Notice>{t('invite.email.serverOff')}</Notice>}
    {!hasEmail && <p className="muted">{t('invite.email.needsAddress')}</p>}
    {error && <Notice error>{error}</Notice>}
    <div className="actions"><button type="submit" disabled={busy}><MailPlus aria-hidden="true" />{t('invite.create')}</button></div>
  </form>;
}

function TokenReveal({ created, onDone }: { created: CreatedInvitation; onDone: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const link = invitationLink(`${location.origin}${location.pathname}`, created.token, created.invitation.workspaceId);
  const emailNotice = invitationEmailNotice(created.email);
  const copy = async (done: string, text: string) => setCopied(await copyText(text) ? done : t('invite.copyBlocked'));
  return <section className="surface adminPanel tokenReveal" aria-label={t('invite.tokenRegion')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('invite.shownOnce')}</p><h2>{t('invite.sendTo', { invitee: inviteeLabel(created.invitation) })}</h2></div><span className="statusBadge warning">{expiryLabel(created.invitation.expiresAt)}</span></div>
    <Notice error={emailNotice.tone === 'warning'}>{emailNotice.text}</Notice>
    <p className="tokenWarning"><KeyRound aria-hidden="true" />{t('invite.tokenWarning')}</p>
    <label className="field"><span>{t('invite.token')}</span><code className="tokenValue" dir="ltr" data-testid="invitation-token">{created.token}</code></label>
    <label className="field"><span>{t('invite.link')}</span><code className="tokenValue" dir="ltr">{link}</code></label>
    <p className="muted">{t('invite.access', { role: roleLabel(created.invitation.role), workspace: created.invitation.workspaceId, until: when(created.invitation.expiresAt) })}</p>
    {copied && <Notice>{copied}</Notice>}
    <div className="actions">
      <button type="button" onClick={() => void copy(t('invite.tokenCopied'), created.token)}><Copy aria-hidden="true" />{t('invite.copyToken')}</button>
      <button type="button" className="secondary" onClick={() => void copy(t('invite.linkCopied'), link)}><Link2 aria-hidden="true" />{t('invite.copyLink')}</button>
      <button type="button" className="secondary" onClick={onDone}>{t('invite.done')}</button>
    </div>
  </section>;
}

function AcceptInvitation({ initial, currentWorkspaceId, onAccepted, onSwitchWorkspace, onSwitchOrganisation }: { initial: { token: string; workspaceId: string | null; tenantId?: string | null } | null; currentWorkspaceId: string; onAccepted: () => void; onSwitchWorkspace?: (workspaceId: string) => Promise<void>; onSwitchOrganisation?: (tenantId: string, workspaceId: string) => Promise<void> }) {
  // Another organisation's invitation (a customer organisation's first admin): accepted with x-tenant-id.
  const [tenant, setTenant] = useState<string | null>(initial?.tenantId ?? null);
  const [joinedTenant, setJoinedTenant] = useState<{ tenantId: string; workspaceId: string } | null>(null);
  const [input, setInput] = useState(initial?.token ?? '');
  const [workspace, setWorkspace] = useState(initial?.workspaceId ?? '');
  const [busy, setBusy] = useState(false);
  const [joined, setJoined] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(initial ? { text: t('accept.opened') } : null);
  const switchTo = (target: string) => {
    if (!onSwitchWorkspace) return;
    setBusy(true);
    // On success the app reloads into the new workspace; only a refusal comes back here.
    void onSwitchWorkspace(target).catch((error: unknown) => { setFlash({ text: describeError(error), error: true }); setBusy(false); });
  };
  return <form className="surface adminPanel" aria-label={t('accept.title')} onSubmit={(event) => {
    event.preventDefault();
    const parsed = parseInvitationInput(input);
    if (!parsed) { setFlash({ text: t('accept.invalid'), error: true }); return; }
    const target = (parsed.workspaceId ?? workspace.trim()) || null;
    const targetTenant = parsed.tenantId ?? tenant;
    const otherTenant = targetTenant && targetTenant !== currentScope().tenantId ? targetTenant : null;
    setBusy(true);
    setFlash(null);
    setJoined(null);
    setJoinedTenant(null);
    void admin.acceptInvitation(parsed.token, target, otherTenant).then((result) => {
      const joinedId = result.membership.workspaceId;
      if (otherTenant) {
        setInput('');
        setJoinedTenant({ tenantId: otherTenant, workspaceId: joinedId });
        setFlash({ text: t('orgs.joinedOrganisation', { tenant: otherTenant, workspace: joinedId }) });
        return;
      }
      rememberWorkspace(joinedId);
      setInput('');
      setJoined(joinedId !== currentWorkspaceId ? joinedId : null);
      const joinedRole = t(`members.role.${result.membership.role}`);
      setFlash({ text: t(joinedId !== currentWorkspaceId ? 'accept.joinedElsewhere' : 'accept.joined', { workspace: joinedId, role: joinedRole }) });
      onAccepted();
    }).catch((error: unknown) => setFlash({ text: describeError(error), error: true })).finally(() => setBusy(false));
  }}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('accept.eyebrow')}</p><h2>{t('accept.title')}</h2></div><UserCheck className="sectionIcon" aria-hidden="true" /></div>
    <p className="muted">{t('accept.help')}</p>
    <div className="adminFields">
      <label className="field wide"><span>{t('accept.input')}</span><input dir="ltr" value={input} onChange={(event) => {
        setInput(event.target.value);
        const parsed = parseInvitationInput(event.target.value);
        if (parsed?.workspaceId) setWorkspace(parsed.workspaceId);
        setTenant(parsed?.tenantId ?? null);
      }} placeholder="anum_inv_…" autoComplete="off" spellCheck={false} /></label>
      <label className="field"><span>{t('accept.workspace')}</span><input dir="ltr" value={workspace} onChange={(event) => setWorkspace(event.target.value)} placeholder={currentWorkspaceId} autoComplete="off" /></label>
    </div>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <div className="actions">
      <button type="submit" disabled={busy || !input.trim()}><UserCheck aria-hidden="true" />{busy ? t('accept.working') : t('accept.submit')}</button>
      {joined && onSwitchWorkspace && <button type="button" className="secondary" disabled={busy} onClick={() => switchTo(joined)}><ArrowRightLeft aria-hidden="true" />{t('accept.switch', { workspace: joined })}</button>}
      {joinedTenant && onSwitchOrganisation && <button type="button" className="secondary" disabled={busy} onClick={() => { setBusy(true); void onSwitchOrganisation(joinedTenant.tenantId, joinedTenant.workspaceId).catch((error: unknown) => { setFlash({ text: describeError(error), error: true }); setBusy(false); }); }}><ArrowRightLeft aria-hidden="true" />{t('orgs.switchTo', { tenant: joinedTenant.tenantId })}</button>}
    </div>
  </form>;
}

// ------------------------------------------------------------------------- budgets

export function BudgetsView() {
  const [overview, setOverview] = useState<ModelBudgetOverview | null>(null);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const refresh = useCallback(async () => {
    try {
      setOverview(await admin.getModelBudgets());
      setLoad({ kind: 'ready' });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const exceeded = overview && (overview.tenant.exceeded || overview.workspace.exceeded);
  // The organization budget caps every workspace: only an organization administrator may
  // change it (the API's `can_manage_tenant_budget`, never guessed from the workspace role).
  const tenantAccess = tenantWriteAccess(overview ? overview.canManageTenantBudget : null, t('budgets.subject'));
  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('budgets.eyebrow')}</p><h2>{t('budgets.title')}</h2><p>{overview ? t('budgets.period', { start: formatResetDate(overview.periodStart, false), reset: formatResetDate(overview.resetsOn) }) : t('budgets.intro')}</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true" />{t('admin.refresh')}</button>
    </section>
    {load.kind === 'loading' && <Notice>{t('budgets.loading')}</Notice>}
    {load.kind === 'error' && <Notice error>{t('budgets.error', { message: load.detail })}</Notice>}
    {load.kind === 'forbidden' && <Forbidden title={t('admin.ownerRequired')} detail={load.detail}>
      {t('budgets.forbidden')}
    </Forbidden>}
    {overview && load.kind !== 'forbidden' && <>
      {exceeded && <Notice error>{t('budgets.exceeded', { reset: formatResetDate(overview.resetsOn) })}</Notice>}
      <div className="budgetGrid">
        <BudgetCard scope="tenant" title={t('budgets.tenant.title')} subtitle={t('budgets.tenant.subtitle')} view={overview.tenant} onSaved={setOverview} access={tenantAccess} />
        <BudgetCard scope="workspace" title={t('budgets.workspace.title')} subtitle={t('budgets.workspace.subtitle')} view={overview.workspace} onSaved={setOverview} />
      </div>
    </>}
  </>;
}

function BudgetCard({ scope, title, subtitle, view, onSaved, access = null }: { scope: BudgetScope; title: string; subtitle: string; view: ModelBudgetScopeView; onSaved: (next: ModelBudgetOverview) => void; access?: TenantWriteAccess | null }) {
  const [draft, setDraft] = useState<LimitDraft>(() => limitDraft(view.budget));
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  useEffect(() => { setDraft(limitDraft(view.budget)); }, [view.budget]);
  const meters = budgetMeters(view);
  const save = (limits: LimitDraft) => {
    const parsed = parseLimits(limits);
    if (!parsed.ok) { setFlash({ text: parsed.error, error: true }); return; }
    setBusy(true);
    setFlash(null);
    void admin.setModelBudget(scope, parsed.value).then((next) => { onSaved(next); setFlash({ text: t('budgets.saved') }); })
      .catch((error: unknown) => setFlash({ text: notAllowed(error), error: true }))
      .finally(() => setBusy(false));
  };
  return <section className={`surface budgetCard ${view.exceeded ? 'exceeded' : ''}`} aria-label={t('budgets.cardLabel', { title })}>
    <div className="sectionHeader"><div><p className="eyebrow">{scope === 'tenant' ? t('budgets.tenant.scope') : t('budgets.workspace.scope')}</p><h2>{title}</h2><p className="muted">{subtitle}</p></div>
      <span className={`statusBadge ${view.exceeded ? 'dangerTone' : view.budget ? 'success' : 'neutral'}`}>{view.exceeded ? t('budgets.badge.exceeded') : view.budget ? t('budgets.badge.limited') : t('budgets.badge.none')}</span></div>
    {meters.map((meter) => <div className="budgetMeter" key={meter.kind}>
      <div className="budgetMeterTop"><span>{meter.label}</span><strong dir="ltr">{meter.usedText}<small> / {meter.limitText}</small></strong></div>
      <div className={`meterTrack ${meter.tone}`} role="progressbar" aria-label={t('budgets.meterUsed', { title, meter: meter.label.toLowerCase() }, { isolate: false })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={meter.percent === null ? undefined : Math.round(meter.percent)} aria-valuetext={meter.rawPercent === null ? t('budgets.noLimit') : t('budgets.percent', { percent: Math.round(meter.rawPercent) })}>
        <span style={{ width: `${meter.percent ?? 0}%` }} />
      </div>
      <small className="muted">{meter.rawPercent === null ? t('budgets.noLimitSet') : t('budgets.percentUsed', { percent: Math.round(meter.rawPercent) })}</small>
    </div>)}
    <dl className="budgetStats">
      <div><dt>{t('budgets.stat.calls')}</dt><dd>{formatTokens(view.usage.calls)}</dd></div>
      <div><dt>{t('budgets.stat.input')}</dt><dd>{formatTokens(view.usage.inputTokens)}</dd></div>
      <div><dt>{t('budgets.stat.output')}</dt><dd>{formatTokens(view.usage.outputTokens)}</dd></div>
      <div><dt>{t('budgets.stat.unpriced')}</dt><dd>{formatTokens(view.usage.unpricedCalls)}</dd></div>
    </dl>
    {view.usage.unpricedCalls > 0 && <p className="muted">{t('budgets.unpricedHint')}</p>}
    {view.budget && <p className="muted">{t('budgets.lastChanged', { when: when(view.budget.updatedAt), user: view.budget.updatedBy })}</p>}
    {access?.notice ? <AccessNotice notice={access.notice} howTo={access.howTo} /> : <form className="budgetForm" aria-label={t('budgets.editLimits', { title: title.toLowerCase() }, { isolate: false })} onSubmit={(event) => { event.preventDefault(); save(draft); }}>
      <label className="field"><span>{t('budgets.costLimit')}</span><input dir="ltr" inputMode="decimal" value={draft.cost} onChange={(event) => setDraft({ ...draft, cost: event.target.value })} placeholder={t('budgets.noLimit')} /></label>
      <label className="field"><span>{t('budgets.tokenLimit')}</span><input dir="ltr" inputMode="numeric" value={draft.tokens} onChange={(event) => setDraft({ ...draft, tokens: event.target.value })} placeholder={t('budgets.noLimit')} /></label>
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      <p className="muted">{t('budgets.limitHint')}</p>
      <div className="actions">
        <button type="submit" disabled={busy}><Save aria-hidden="true" />{busy ? t('admin.saving') : t('budgets.save')}</button>
        {view.budget && <button type="button" className="secondary" disabled={busy} onClick={() => save({ cost: '', tokens: '' })}><XCircle aria-hidden="true" />{t('budgets.remove')}</button>}
      </div>
    </form>}
  </section>;
}

// ------------------------------------------------------------------------- approval policy

/**
 * The workspace approval policy (docs/approvals-and-risk.md, Workspace Approval Policy). Any member
 * reads it; only owners change it. The caller's role comes from their persisted membership, and a
 * 403 on saving is shown the same way the other owner screens show it.
 */
export function ApprovalPolicyView({ workspaceId }: { workspaceId: string }) {
  const [policy, setPolicy] = useState<WorkspaceApprovalPolicy | null>(null);
  const [draft, setDraft] = useState<PolicyDraft>({ twoPersonRule: false, mediumRiskRequiresApproval: false });
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [role, setRole] = useState<string | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);

  const refresh = useCallback(async () => {
    try {
      const [next, membership] = await Promise.all([admin.getApprovalPolicy(), getCurrentMembership().catch(() => null)]);
      setPolicy(next);
      setDraft(policyDraft(next));
      const ownRole = membership?.active ? membership.role : null;
      setRole(ownRole);
      // Owners can list members: used only to warn before the two-person rule locks everyone out.
      setMembers(ownRole === 'owner' ? await admin.getMembers().catch(() => null) : null);
      setLoad({ kind: 'ready' });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const notOwner = role !== null && role !== 'owner';
  const readOnly = notOwner || denied !== null;
  const changed = policy !== null && policyChanged(policy, draft);
  const warning = twoPersonWarning(draft, members);

  const save = () => {
    setBusy(true);
    setFlash(null);
    void admin.setApprovalPolicy(draft).then((next) => {
      setPolicy(next);
      setDraft(policyDraft(next));
      setFlash({ text: t('policy.saved') });
    }).catch((error: unknown) => {
      if (isPermissionDenied(error)) {
        setDenied(describeError(error));
        if (policy) setDraft(policyDraft(policy));
      } else {
        setFlash({ text: describeError(error), error: true });
      }
    }).finally(() => setBusy(false));
  };

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('policy.eyebrow')}</p><h2>{t('policy.title')}</h2><p>{t('policy.intro', { workspace: workspaceId })}</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); setDenied(null); setFlash(null); void refresh(); }}><RefreshCw aria-hidden="true" />{t('admin.refresh')}</button>
    </section>
    {load.kind === 'loading' && <Notice>{t('policy.loading')}</Notice>}
    {load.kind === 'error' && <Notice error>{t('policy.error', { message: load.detail })}</Notice>}
    {load.kind === 'forbidden' && <Forbidden title={t('policy.accessRequired')} detail={load.detail}>
      {t('policy.forbidden')}
    </Forbidden>}
    {load.kind === 'ready' && policy && <>
      {readOnly && <Forbidden title={t('admin.ownerRequired')} detail={denied}>
        {role && role !== 'owner' ? t('policy.readOnlyRole', { role: (ROLES as readonly string[]).includes(role) ? t(`members.role.${role as WorkspaceRole}`) : role }) : t('policy.readOnly')}
      </Forbidden>}
      <section className="surface adminPanel policyPanel" aria-label={t('policy.region')}>
        <div className="sectionHeader"><div><p className="eyebrow">{t('policy.inForce')}</p><h2>{t('policy.rules')}</h2></div><Scale className="sectionIcon" aria-hidden="true" /></div>
        <p className="muted">{t('policy.help')}</p>
        {POLICY_SWITCHES.map((item) => {
          const on = draft[item.key];
          return <div className={`policySwitch ${on ? 'on' : ''}`} key={item.key}>
            <label>
              <span className="policySwitchTitle"><strong>{item.title}</strong><span className={`statusBadge ${on ? 'success' : 'neutral'}`}>{on ? t('policy.on') : t('policy.off')}</span></span>
              <input type="checkbox" role="switch" aria-label={item.title} aria-describedby={`policy-${item.key}`} checked={on} disabled={readOnly || busy} onChange={(event) => setDraft({ ...draft, [item.key]: event.target.checked })} />
            </label>
            <ul id={`policy-${item.key}`} className="policyExplain">
              <li className={on ? 'active' : ''}><span>{t('policy.whenOn')}</span>{item.on}</li>
              <li className={on ? '' : 'active'}><span>{t('policy.whenOff')}</span>{item.off}</li>
            </ul>
          </div>;
        })}
        {warning && <Notice error>{warning}</Notice>}
        <p className="muted">{policyUpdatedLabel(policy, when)}</p>
        {flash && <Notice error={flash.error}>{flash.text}</Notice>}
        {!readOnly && <div className="actions">
          <button type="button" disabled={busy || !changed} onClick={save}><Save aria-hidden="true" />{busy ? t('admin.saving') : t('policy.save')}</button>
          {changed && <button type="button" className="secondary" disabled={busy} onClick={() => setDraft(policyDraft(policy))}><RotateCcw aria-hidden="true" />{t('policy.undo')}</button>}
        </div>}
      </section>
    </>}
  </>;
}

// ------------------------------------------------------------------------- shared

function AdminMetric({ icon, label, value, detail }: { icon: ReactNode; label: string; value: number; detail: string }) {
  return <article className="metric"><div className="metricTop"><span>{label}</span><div className="metricIcon" aria-hidden="true">{icon}</div></div><strong>{value}</strong><small>{detail}</small></article>;
}

function Empty({ title, text }: { title: string; text: string }) {
  return <div className="emptyState"><Search aria-hidden="true" /><h3>{title}</h3><p>{text}</p></div>;
}

/** Shown where a task run is refused with 402: the API's sentence names the reset date. */
export function BudgetExceededNotice({ message, onOpenBudgets, onDismiss }: { message: string; onOpenBudgets: () => void; onDismiss: () => void }) {
  return <section className="surface budgetAlert" role="alert" aria-label={t('budgets.reachedLabel')}>
    <span className="adminIcon" aria-hidden="true"><Gauge /></span>
    <div><h3>{t('budgets.reachedTitle')}</h3><p dir="auto">{message}</p></div>
    <div className="budgetAlertActions">
      <button type="button" onClick={onOpenBudgets}><Gauge aria-hidden="true" />{t('budgets.open')}</button>
      <button type="button" className="secondary" onClick={onDismiss}>{t('admin.dismiss')}</button>
    </div>
  </section>;
}
