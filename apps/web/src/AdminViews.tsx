// Owner screens: workspace members and invitations, monthly model budgets, and the approval policy.
// Everything shown comes from the API. Who may manage is decided by the API (its 403, or the
// caller's persisted membership role), never guessed from the token here.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowRightLeft, Ban, CircleDot, Copy, Gauge, KeyRound, Link2, Lock, MailPlus, RefreshCw, RotateCcw, Save, Scale, Search, Send, ShieldCheck, Ticket, UserCheck, Users, XCircle } from 'lucide-react';
import type { CreatedInvitation, MembersSummary, ModelBudgetOverview, ModelBudgetScopeView, WorkspaceApprovalPolicy, WorkspaceInvitation, WorkspaceMember, WorkspaceRole, BudgetScope } from '@anum/contracts';
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
import { t, type MessageKey } from './lib/i18n';
import { tenantWriteAccess } from './lib/governance';
import './admin.css';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;

const when = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
const roleLabel: Record<WorkspaceRole, string> = { owner: 'Owner', member: 'Member', viewer: 'Viewer' };

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot />{children}</p>;
}

function Forbidden({ title, detail, children }: { title: string; detail: string | null; children: ReactNode }) {
  return <section className="surface adminForbidden" aria-label={title}>
    <span className="adminIcon"><Lock /></span>
    <div><h3>{title}</h3><p>{children}</p>{detail && <p className="muted">The API answered: {detail}</p>}</div>
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
      setFlash({ text: isPermissionDenied(error) ? `Not allowed: ${describeError(error)}` : describeError(error), error: true });
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
      <div><p className="eyebrow">Workspace access</p><h2>Members and invitations</h2><p>Who can work in {workspaceId}, with which role. Owners invite people with a single-use link and manage roles.</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw />Refresh</button>
    </section>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {load.kind === 'loading' && <Notice>Loading members and invitations...</Notice>}
    {load.kind === 'error' && <section className="surface adminForbidden" aria-label={t('members.unavailable')}>
      <span className="adminIcon"><Users aria-hidden="true" /></span>
      <div><h3>{t('members.errorTitle')}</h3><p>{load.detail}</p><p className="muted">{t('audit.errorHelp')}</p>
        <div className="actions"><button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>
    </section>}
    {load.kind === 'forbidden' && <Forbidden title="Owner access required" detail={load.detail}>
      Listing members, changing roles and inviting people need the owner role in this workspace. Ask a workspace owner to change your role, or accept an invitation below.
    </Forbidden>}
    {load.kind === 'ready' && <>
      <section className="metrics">
        <AdminMetric icon={<Users />} label="Active members" value={summary?.active ?? 0} detail="Can use this workspace" />
        <AdminMetric icon={<ShieldCheck />} label="Owners" value={summary?.activeOwners ?? 0} detail="Active owners" />
        <AdminMetric icon={<Ticket />} label="Pending invitations" value={summary?.pendingInvitations ?? 0} detail={t('members.pendingDetail')} />
        <AdminMetric icon={<Ban />} label="Deactivated" value={summary?.deactivated ?? 0} detail="Access refused (403)" />
      </section>
      <section className="surface adminPanel" aria-label="Members">
        <div className="sectionHeader"><div><p className="eyebrow">Memberships</p><h2>{summary ? (summary.total === 1 ? '1 member' : `${summary.total} members`) : 'Members'}</h2></div></div>
        <div className="memberFilters" role="search" aria-label={t('members.find')}>
          <label className="field memberSearch"><span>{t('common.search')}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('members.searchPlaceholder')} autoComplete="off" spellCheck={false} /></label>
          <label className="field"><span>{t('members.status')}</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as MemberStatusFilter)}><option value="all">{t('members.status.all')}</option><option value="active">{t('members.status.active')}</option><option value="deactivated">{t('members.status.deactivated')}</option></select></label>
          <label className="field"><span>{t('members.roleFilter')}</span><select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value as WorkspaceRole | 'all')}><option value="all">{t('members.allRoles')}</option>{ROLES.map((r) => <option key={r} value={r}>{t(`members.roleOption.${r}` as MessageKey)}</option>)}</select></label>
        </div>
        {listState === 'loading' && <p className="muted" role="status">{t('members.searching')}</p>}
        {!members.length && listState !== 'loading' && (filtersActive
          ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('members.noMatchTitle')}</h3><p>{t('members.noMatchText')}</p><button type="button" className="secondary" onClick={clearFilters}>{t('members.clearFilters')}</button></div>
          : <Empty title="No members" text="This workspace has no memberships yet. Invite someone below." />)}
        <div className="memberList">
          {members.map((member) => {
            const last = isOnlyActiveOwner(summary, member);
            const key = `member:${member.userId}`;
            const hintId = `last-owner-${member.userId}`;
            return <article className={`memberRow ${member.active ? '' : 'inactive'}`} key={member.userId} aria-label={`Member ${member.userId}`}>
              <span className="adminAvatar" aria-hidden="true">{member.userId.slice(0, 2).toUpperCase()}</span>
              <div className="memberMain">
                <strong>{member.userId}</strong>
                {member.email && <small>{member.email}</small>}
                <small>Joined {when(member.createdAt)} · updated {when(member.updatedAt)}</small>
                {last && <small className="memberHint" id={hintId}>{t('members.lastOwner')}</small>}
              </div>
              <span className={`statusBadge ${member.active ? 'success' : 'neutral'}`}>{member.active ? 'active' : 'deactivated'}</span>
              <label className="memberRole"><span className="visuallyHidden">Role for {member.userId}</span>
                <select aria-label={`Role for ${member.userId}`} aria-describedby={last ? hintId : undefined} value={member.role} disabled={busy !== null} onChange={(event) => {
                  const next = event.target.value as WorkspaceRole;
                  if (next !== member.role) setPendingChange({ kind: 'role', member, role: next });
                }}>
                  {ROLES.map((role) => <option key={role} value={role} disabled={last && role !== 'owner'}>{roleLabel[role]}</option>)}
                </select>
              </label>
              {member.active
                ? <button type="button" className="secondary" disabled={busy !== null || last} aria-describedby={last ? hintId : undefined} onClick={() => setPendingChange({ kind: 'deactivate', member })}><Ban />Deactivate</button>
                : <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(key, async () => { await admin.setMemberActive(member.userId, true); return `${member.userId} was reactivated.`; })}><RotateCcw />Reactivate</button>}
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
          return 'Invitation created. Copy the token now: it is shown only once.';
        })} />
        {created && <TokenReveal created={created} onDone={() => setCreated(null)} />}
      </div>
      <section className="surface adminPanel" aria-label="Invitations">
        <div className="sectionHeader"><div><p className="eyebrow">Invitations</p><h2>Invitations</h2></div>
          <div className="segmented" role="group" aria-label={t('members.showInvitations')}>
            {INVITATION_FILTERS.map((option) => <button type="button" key={option} aria-pressed={invitationFilter === option} onClick={() => setInvitationFilter(option)}>{t(`members.invitations.${option}` as MessageKey)}</button>)}
          </div>
        </div>
        {!invitations.length && <Empty title={invitationFilter === 'all' ? 'No invitations' : `No ${invitationFilter} invitations`} text={invitationFilter === 'pending' || invitationFilter === 'all' ? 'Invite someone above. Their invitation appears here until it is accepted, revoked or expires.' : 'Choose another filter to see the rest.'} />}
        <div className="memberList">
          {invitations.map((invitation) => {
            const open = invitation.status === 'pending' || invitation.status === 'expired';
            return <article className="memberRow invitationRow" key={invitation.id} aria-label={`Invitation for ${inviteeLabel(invitation)}`}>
              <span className="adminIcon"><MailPlus /></span>
              <div className="memberMain">
                <strong>{inviteeLabel(invitation)}</strong>
                <small>{roleLabel[invitation.role]} · created by {invitation.createdByUserId} · {invitation.status === 'pending' ? expiryLabel(invitation.expiresAt) : invitation.status === 'accepted' && invitation.acceptedAt ? `accepted ${when(invitation.acceptedAt)}${invitation.acceptedByUserId ? ` by ${invitation.acceptedByUserId}` : ''}` : invitation.status === 'revoked' && invitation.revokedAt ? `revoked ${when(invitation.revokedAt)}` : `expired ${when(invitation.expiresAt)}`}</small>
              </div>
              <span className={`statusBadge ${invitation.status === 'pending' ? 'warning' : invitation.status === 'accepted' ? 'success' : 'neutral'}`}>{invitation.status}</span>
              {open
                ? <div className="invitationActions">
                    <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(`invitation:${invitation.id}`, async () => {
                      const result = await admin.resendInvitation(invitation.id);
                      setCreated(result);
                      return t('members.resent', { invitee: inviteeLabel(invitation) });
                    })}><Send aria-hidden="true" />{invitation.status === 'expired' ? t('members.renew') : t('members.resend')}</button>
                    {invitation.status === 'pending' && <button type="button" className="danger" disabled={busy !== null} onClick={() => setPendingChange({ kind: 'revoke', invitation })}><XCircle />Revoke</button>}
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


function InviteForm({ busy, onCreate }: { busy: boolean; onCreate: (request: Parameters<typeof admin.createInvitation>[0]) => Promise<boolean> }) {
  const [role, setRole] = useState<WorkspaceRole>('member');
  const [userId, setUserId] = useState('');
  const [email, setEmail] = useState('');
  const [ttl, setTtl] = useState(String(DEFAULT_TTL_HOURS));
  const [error, setError] = useState<string | null>(null);
  return <form className="surface adminPanel" aria-label="Invite someone" noValidate onSubmit={(event) => {
    event.preventDefault();
    const result = invitationRequest({ role, userId, email, ttlHours: ttl });
    if (!result.ok) { setError(result.error); return; }
    setError(null);
    void onCreate(result.value).then((ok) => { if (ok) { setUserId(''); setEmail(''); } });
  }}>
    <div className="sectionHeader"><div><p className="eyebrow">New invitation</p><h2>Invite someone</h2></div></div>
    <p className="muted">Bind it to their user id (the sign-in subject), their verified email, or both. Only that person can accept it.</p>
    <div className="adminFields">
      <label className="field"><span>User id</span><input value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="e.g. 3f2c…-sub" autoComplete="off" /></label>
      <label className="field"><span>Email</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" autoComplete="off" /></label>
      <label className="field"><span>Role</span><select value={role} onChange={(event) => setRole(event.target.value as WorkspaceRole)}>{ROLES.map((r) => <option key={r} value={r}>{roleLabel[r]}</option>)}</select></label>
      <label className="field"><span>Expires after (hours)</span><input inputMode="numeric" value={ttl} onChange={(event) => setTtl(event.target.value)} /></label>
    </div>
    {error && <Notice error>{error}</Notice>}
    <div className="actions"><button type="submit" disabled={busy}><MailPlus />Create invitation</button></div>
  </form>;
}

function TokenReveal({ created, onDone }: { created: CreatedInvitation; onDone: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const link = invitationLink(`${location.origin}${location.pathname}`, created.token, created.invitation.workspaceId);
  const copy = async (label: string, text: string) => setCopied(await copyText(text) ? `${label} copied.` : 'Copying is blocked here. Select the text and copy it yourself.');
  return <section className="surface adminPanel tokenReveal" aria-label="New invitation token">
    <div className="sectionHeader"><div><p className="eyebrow">Shown once</p><h2>Send this to {inviteeLabel(created.invitation)}</h2></div><span className="statusBadge warning">{expiryLabel(created.invitation.expiresAt)}</span></div>
    <p className="tokenWarning"><KeyRound />ANUM keeps only a hash of this token. Copy it now: it cannot be shown again. Anyone you forward it to still has to sign in as the invitee.</p>
    <label className="field"><span>Token</span><code className="tokenValue" data-testid="invitation-token">{created.token}</code></label>
    <label className="field"><span>Link</span><code className="tokenValue">{link}</code></label>
    <p className="muted">{roleLabel[created.invitation.role]} access to {created.invitation.workspaceId}, valid until {when(created.invitation.expiresAt)}.</p>
    {copied && <Notice>{copied}</Notice>}
    <div className="actions">
      <button type="button" onClick={() => void copy('Token', created.token)}><Copy />Copy token</button>
      <button type="button" className="secondary" onClick={() => void copy('Link', link)}><Link2 />Copy link</button>
      <button type="button" className="secondary" onClick={onDone}>Done</button>
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
  const [flash, setFlash] = useState<Flash>(initial ? { text: 'Invitation link opened. Check the workspace, then accept.' } : null);
  const switchTo = (target: string) => {
    if (!onSwitchWorkspace) return;
    setBusy(true);
    // On success the app reloads into the new workspace; only a refusal comes back here.
    void onSwitchWorkspace(target).catch((error: unknown) => { setFlash({ text: describeError(error), error: true }); setBusy(false); });
  };
  return <form className="surface adminPanel" aria-label="Accept an invitation" onSubmit={(event) => {
    event.preventDefault();
    const parsed = parseInvitationInput(input);
    if (!parsed) { setFlash({ text: 'Paste an invitation token (it starts with anum_inv_) or the whole invitation link.', error: true }); return; }
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
      setFlash({ text: `You joined ${joinedId} as ${roleLabel[result.membership.role].toLowerCase()}.${joinedId !== currentWorkspaceId ? ' Switch to that workspace to work in it.' : ''}` });
      onAccepted();
    }).catch((error: unknown) => setFlash({ text: describeError(error), error: true })).finally(() => setBusy(false));
  }}>
    <div className="sectionHeader"><div><p className="eyebrow">For invitees</p><h2>Accept an invitation</h2></div><UserCheck className="sectionIcon" /></div>
    <p className="muted">Paste the token or link you received. You must be signed in as the person it was issued to.</p>
    <div className="adminFields">
      <label className="field wide"><span>Invitation token or link</span><input value={input} onChange={(event) => {
        setInput(event.target.value);
        const parsed = parseInvitationInput(event.target.value);
        if (parsed?.workspaceId) setWorkspace(parsed.workspaceId);
        setTenant(parsed?.tenantId ?? null);
      }} placeholder="anum_inv_…" autoComplete="off" spellCheck={false} /></label>
      <label className="field"><span>Workspace</span><input value={workspace} onChange={(event) => setWorkspace(event.target.value)} placeholder={currentWorkspaceId} autoComplete="off" /></label>
    </div>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <div className="actions">
      <button type="submit" disabled={busy || !input.trim()}><UserCheck />{busy ? 'Working...' : 'Accept invitation'}</button>
      {joined && onSwitchWorkspace && <button type="button" className="secondary" disabled={busy} onClick={() => switchTo(joined)}><ArrowRightLeft />Switch to {joined}</button>}
      {joinedTenant && onSwitchOrganisation && <button type="button" className="secondary" disabled={busy} onClick={() => { setBusy(true); void onSwitchOrganisation(joinedTenant.tenantId, joinedTenant.workspaceId).catch((error: unknown) => { setFlash({ text: describeError(error), error: true }); setBusy(false); }); }}><ArrowRightLeft />{t('orgs.switchTo', { tenant: joinedTenant.tenantId })}</button>}
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
  const tenantAccess = tenantWriteAccess(overview ? overview.canManageTenantBudget : null, 'organization model budgets');
  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">Model spend</p><h2>Monthly model budgets</h2><p>{overview ? `Usage this UTC month, since ${formatResetDate(overview.periodStart).replace(' (UTC)', '')}. It resets on ${formatResetDate(overview.resetsOn)}.` : 'Estimated cost and token limits per UTC calendar month, for the organization and this workspace.'}</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw />Refresh</button>
    </section>
    {load.kind === 'loading' && <Notice>Loading budgets and usage...</Notice>}
    {load.kind === 'error' && <Notice error>Budgets could not be loaded. {load.detail}</Notice>}
    {load.kind === 'forbidden' && <Forbidden title="Owner access required" detail={load.detail}>
      Model budgets and usage are visible to organization owners only. If tasks stop with a budget message, ask an owner to raise the limit.
    </Forbidden>}
    {overview && load.kind !== 'forbidden' && <>
      {exceeded && <Notice error>A monthly budget is used up: task runs are refused (402) and voice answers say so until {formatResetDate(overview.resetsOn)} or until an owner raises the limit.</Notice>}
      <div className="budgetGrid">
        <BudgetCard scope="tenant" title="Organization" subtitle="All workspaces in this organization together" view={overview.tenant} onSaved={setOverview} readOnlyNotice={tenantAccess.notice} />
        <BudgetCard scope="workspace" title="This workspace" subtitle="Task planning and voice answers here" view={overview.workspace} onSaved={setOverview} />
      </div>
    </>}
  </>;
}

function BudgetCard({ scope, title, subtitle, view, onSaved, readOnlyNotice = null }: { scope: BudgetScope; title: string; subtitle: string; view: ModelBudgetScopeView; onSaved: (next: ModelBudgetOverview) => void; readOnlyNotice?: string | null }) {
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
    void admin.setModelBudget(scope, parsed.value).then((next) => { onSaved(next); setFlash({ text: 'Budget saved.' }); })
      .catch((error: unknown) => setFlash({ text: isPermissionDenied(error) ? `Not allowed: ${describeError(error)}` : describeError(error), error: true }))
      .finally(() => setBusy(false));
  };
  return <section className={`surface budgetCard ${view.exceeded ? 'exceeded' : ''}`} aria-label={`${title} budget`}>
    <div className="sectionHeader"><div><p className="eyebrow">{scope === 'tenant' ? 'Tenant scope' : 'Workspace scope'}</p><h2>{title}</h2><p className="muted">{subtitle}</p></div>
      <span className={`statusBadge ${view.exceeded ? 'dangerTone' : view.budget ? 'success' : 'neutral'}`}>{view.exceeded ? 'budget used up' : view.budget ? 'limited' : 'no budget'}</span></div>
    {meters.map((meter) => <div className="budgetMeter" key={meter.kind}>
      <div className="budgetMeterTop"><span>{meter.label}</span><strong>{meter.usedText}<small> / {meter.limitText}</small></strong></div>
      <div className={`meterTrack ${meter.tone}`} role="progressbar" aria-label={`${title} ${meter.label.toLowerCase()} used`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={meter.percent === null ? undefined : Math.round(meter.percent)} aria-valuetext={meter.rawPercent === null ? 'No limit' : `${Math.round(meter.rawPercent)}%`}>
        <span style={{ width: `${meter.percent ?? 0}%` }} />
      </div>
      <small className="muted">{meter.rawPercent === null ? 'No limit set' : `${Math.round(meter.rawPercent)}% used`}</small>
    </div>)}
    <dl className="budgetStats">
      <div><dt>Model calls</dt><dd>{formatTokens(view.usage.calls)}</dd></div>
      <div><dt>Input tokens</dt><dd>{formatTokens(view.usage.inputTokens)}</dd></div>
      <div><dt>Output tokens</dt><dd>{formatTokens(view.usage.outputTokens)}</dd></div>
      <div><dt>Unpriced calls</dt><dd>{formatTokens(view.usage.unpricedCalls)}</dd></div>
    </dl>
    {view.usage.unpricedCalls > 0 && <p className="muted">Unpriced calls add tokens but no cost: set a token limit too.</p>}
    {view.budget && <p className="muted">Last changed {when(view.budget.updatedAt)} by {view.budget.updatedBy}.</p>}
    {readOnlyNotice ? <Notice>{readOnlyNotice}</Notice> : <form className="budgetForm" aria-label={`Edit ${title.toLowerCase()} limits`} onSubmit={(event) => { event.preventDefault(); save(draft); }}>
      <label className="field"><span>Monthly cost limit (USD)</span><input inputMode="decimal" value={draft.cost} onChange={(event) => setDraft({ ...draft, cost: event.target.value })} placeholder="No limit" /></label>
      <label className="field"><span>Monthly token limit</span><input inputMode="numeric" value={draft.tokens} onChange={(event) => setDraft({ ...draft, tokens: event.target.value })} placeholder="No limit" /></label>
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      <p className="muted">Leave a field empty for no limit. 0 blocks every model call.</p>
      <div className="actions">
        <button type="submit" disabled={busy}><Save />{busy ? 'Saving...' : 'Save limits'}</button>
        {view.budget && <button type="button" className="secondary" disabled={busy} onClick={() => save({ cost: '', tokens: '' })}><XCircle />Remove limits</button>}
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
      setFlash({ text: 'Approval policy saved. It applies from the next approval decision and is in the audit log.' });
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
      <div><p className="eyebrow">Workspace safety</p><h2>Who must approve risky actions</h2><p>The approval rules for agent actions in {workspaceId}. Every change is recorded in the audit log.</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); setDenied(null); setFlash(null); void refresh(); }}><RefreshCw />Refresh</button>
    </section>
    {load.kind === 'loading' && <Notice>Loading the approval policy...</Notice>}
    {load.kind === 'error' && <Notice error>The approval policy could not be loaded. {load.detail}</Notice>}
    {load.kind === 'forbidden' && <Forbidden title="Access required" detail={load.detail}>
      Reading the approval policy needs an active membership in this workspace.
    </Forbidden>}
    {load.kind === 'ready' && policy && <>
      {readOnly && <Forbidden title="Owner access required" detail={denied}>
        Only workspace owners can change the approval policy.{role && role !== 'owner' ? ` Your role here is ${roleLabel[role as WorkspaceRole]?.toLowerCase() ?? role}.` : ''} What is in force is shown below; ask an owner to change it.
      </Forbidden>}
      <section className="surface adminPanel policyPanel" aria-label="Approval policy settings">
        <div className="sectionHeader"><div><p className="eyebrow">In force</p><h2>Approval rules</h2></div><Scale className="sectionIcon" /></div>
        <p className="muted">High-risk tools always pause for an owner's approval. These two switches add to that.</p>
        {POLICY_SWITCHES.map((item) => {
          const on = draft[item.key];
          return <div className={`policySwitch ${on ? 'on' : ''}`} key={item.key}>
            <label>
              <span className="policySwitchTitle"><strong>{item.title}</strong><span className={`statusBadge ${on ? 'success' : 'neutral'}`}>{on ? 'on' : 'off'}</span></span>
              <input type="checkbox" role="switch" aria-label={item.title} aria-describedby={`policy-${item.key}`} checked={on} disabled={readOnly || busy} onChange={(event) => setDraft({ ...draft, [item.key]: event.target.checked })} />
            </label>
            <ul id={`policy-${item.key}`} className="policyExplain">
              <li className={on ? 'active' : ''}><span>When on</span>{item.on}</li>
              <li className={on ? '' : 'active'}><span>When off</span>{item.off}</li>
            </ul>
          </div>;
        })}
        {warning && <Notice error>{warning}</Notice>}
        <p className="muted">{policyUpdatedLabel(policy, when)}</p>
        {flash && <Notice error={flash.error}>{flash.text}</Notice>}
        {!readOnly && <div className="actions">
          <button type="button" disabled={busy || !changed} onClick={save}><Save />{busy ? 'Saving...' : 'Save policy'}</button>
          {changed && <button type="button" className="secondary" disabled={busy} onClick={() => setDraft(policyDraft(policy))}><RotateCcw />Undo changes</button>}
        </div>}
      </section>
    </>}
  </>;
}

// ------------------------------------------------------------------------- shared

function AdminMetric({ icon, label, value, detail }: { icon: ReactNode; label: string; value: number; detail: string }) {
  return <article className="metric"><div className="metricTop"><span>{label}</span><div className="metricIcon">{icon}</div></div><strong>{value}</strong><small>{detail}</small></article>;
}

function Empty({ title, text }: { title: string; text: string }) {
  return <div className="emptyState"><Search /><h3>{title}</h3><p>{text}</p></div>;
}

/** Shown where a task run is refused with 402: the API's sentence names the reset date. */
export function BudgetExceededNotice({ message, onOpenBudgets, onDismiss }: { message: string; onOpenBudgets: () => void; onDismiss: () => void }) {
  return <section className="surface budgetAlert" role="alert" aria-label="Model budget reached">
    <span className="adminIcon"><Gauge /></span>
    <div><h3>Monthly model budget reached</h3><p>{message}</p></div>
    <div className="budgetAlertActions">
      <button type="button" onClick={onOpenBudgets}><Gauge />Model budgets</button>
      <button type="button" className="secondary" onClick={onDismiss}>Dismiss</button>
    </div>
  </section>;
}
