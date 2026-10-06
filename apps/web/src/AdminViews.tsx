// Owner screens: workspace members and invitations, and monthly model budgets.
// Everything shown comes from the API. Who may manage is decided by the API's 403, never guessed here.
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Ban, CircleDot, Copy, Gauge, KeyRound, Link2, Lock, MailPlus, RefreshCw, RotateCcw, Save, Search, ShieldCheck, Ticket, UserCheck, Users, XCircle } from 'lucide-react';
import type { CreatedInvitation, ModelBudgetOverview, ModelBudgetScopeView, WorkspaceInvitation, WorkspaceMember, WorkspaceRole, BudgetScope } from '@anum/contracts';
import * as admin from './lib/adminApi';
import {
  DEFAULT_TTL_HOURS,
  ROLES,
  activeOwnerCount,
  budgetMeters,
  expiryLabel,
  formatResetDate,
  formatTokens,
  invitationLink,
  invitationRequest,
  inviteeLabel,
  isLastActiveOwner,
  limitDraft,
  parseInvitationInput,
  parseLimits,
  sortMembers,
  type LimitDraft,
} from './lib/admin';
import { describeError, isPermissionDenied } from './lib/errors';
import './admin.css';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;

const when = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
const roleLabel: Record<WorkspaceRole, string> = { owner: 'Owner', member: 'Member', viewer: 'Viewer' };

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot />{children}</p>;
}

function Forbidden({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return <section className="surface adminForbidden" aria-label={title}>
    <span className="adminIcon"><Lock /></span>
    <div><h3>{title}</h3><p>{children}</p><p className="muted">The API answered: {detail}</p></div>
  </section>;
}

async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

// ------------------------------------------------------------------------- members

/** Pending invitation from a link: read once, then removed from the address bar and history. */
function takeInvitationFromLocation(): { token: string; workspaceId: string | null } | null {
  if (!location.hash.startsWith('#invitation=')) return null;
  const parsed = parseInvitationInput(location.hash);
  history.replaceState(null, '', '#members');
  return parsed;
}

export function MembersView({ workspaceId }: { workspaceId: string }) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  const [linked] = useState(takeInvitationFromLocation);

  const refresh = useCallback(async () => {
    try {
      const [nextMembers, nextInvitations] = await Promise.all([admin.getMembers(), admin.getInvitations()]);
      setMembers(nextMembers);
      setInvitations(nextInvitations);
      setLoad({ kind: 'ready' });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

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

  const sorted = sortMembers(members);
  const pending = invitations.filter((x) => x.status === 'pending');
  const others = invitations.filter((x) => x.status !== 'pending');

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">Workspace access</p><h2>Members and invitations</h2><p>Who can work in {workspaceId}, with which role. Owners invite people with a single-use link and manage roles.</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw />Refresh</button>
    </section>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {load.kind === 'loading' && <Notice>Loading members and invitations...</Notice>}
    {load.kind === 'error' && <Notice error>Members could not be loaded. {load.detail}</Notice>}
    {load.kind === 'forbidden' && <Forbidden title="Owner access required" detail={load.detail}>
      Listing members, changing roles and inviting people need the owner role in this workspace. Ask a workspace owner to change your role, or accept an invitation below.
    </Forbidden>}
    {load.kind === 'ready' && <>
      <section className="metrics">
        <AdminMetric icon={<Users />} label="Active members" value={members.filter((m) => m.active).length} detail="Can use this workspace" />
        <AdminMetric icon={<ShieldCheck />} label="Owners" value={activeOwnerCount(members)} detail="Active owners" />
        <AdminMetric icon={<Ticket />} label="Pending invitations" value={pending.length} detail="Not yet accepted" />
        <AdminMetric icon={<Ban />} label="Deactivated" value={members.filter((m) => !m.active).length} detail="Access refused (403)" />
      </section>
      <section className="surface adminPanel" aria-label="Members">
        <div className="sectionHeader"><div><p className="eyebrow">Memberships</p><h2>{members.length === 1 ? '1 member' : `${members.length} members`}</h2></div></div>
        {!members.length && <Empty title="No members" text="This workspace has no memberships yet." />}
        <div className="memberList">
          {sorted.map((member) => {
            const last = isLastActiveOwner(members, member);
            const key = `member:${member.userId}`;
            return <article className={`memberRow ${member.active ? '' : 'inactive'}`} key={member.userId} aria-label={`Member ${member.userId}`}>
              <span className="adminAvatar" aria-hidden="true">{member.userId.slice(0, 2).toUpperCase()}</span>
              <div className="memberMain">
                <strong>{member.userId}</strong>
                <small>Joined {when(member.createdAt)} · updated {when(member.updatedAt)}</small>
                {last && <small className="memberHint">Last active owner: the API refuses to demote or deactivate them.</small>}
              </div>
              <span className={`statusBadge ${member.active ? 'success' : 'neutral'}`}>{member.active ? 'active' : 'deactivated'}</span>
              <label className="memberRole"><span className="visuallyHidden">Role for {member.userId}</span>
                <select aria-label={`Role for ${member.userId}`} value={member.role} disabled={busy !== null} onChange={(event) => {
                  const next = event.target.value as WorkspaceRole;
                  void act(key, async () => { await admin.changeMemberRole(member.userId, next); return `${member.userId} is now ${roleLabel[next].toLowerCase()}.`; });
                }}>
                  {ROLES.map((role) => <option key={role} value={role}>{roleLabel[role]}</option>)}
                </select>
              </label>
              {member.active
                ? <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(key, async () => { await admin.setMemberActive(member.userId, false); return `${member.userId} was deactivated.`; })}><Ban />Deactivate</button>
                : <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(key, async () => { await admin.setMemberActive(member.userId, true); return `${member.userId} was reactivated.`; })}><RotateCcw />Reactivate</button>}
            </article>;
          })}
        </div>
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
        <div className="sectionHeader"><div><p className="eyebrow">Invitations</p><h2>{pending.length === 1 ? '1 pending' : `${pending.length} pending`}</h2></div></div>
        {!invitations.length && <Empty title="No invitations" text="Invite someone above. Their invitation appears here until it is accepted, revoked or expires." />}
        <div className="memberList">
          {[...pending, ...others].map((invitation) => <article className="memberRow invitationRow" key={invitation.id} aria-label={`Invitation for ${inviteeLabel(invitation)}`}>
            <span className="adminIcon"><MailPlus /></span>
            <div className="memberMain">
              <strong>{inviteeLabel(invitation)}</strong>
              <small>{roleLabel[invitation.role]} · created by {invitation.createdByUserId} · {invitation.status === 'pending' ? expiryLabel(invitation.expiresAt) : invitation.status === 'accepted' && invitation.acceptedAt ? `accepted ${when(invitation.acceptedAt)}${invitation.acceptedByUserId ? ` by ${invitation.acceptedByUserId}` : ''}` : invitation.status === 'revoked' && invitation.revokedAt ? `revoked ${when(invitation.revokedAt)}` : `expired ${when(invitation.expiresAt)}`}</small>
            </div>
            <span className={`statusBadge ${invitation.status === 'pending' ? 'warning' : invitation.status === 'accepted' ? 'success' : 'neutral'}`}>{invitation.status}</span>
            {invitation.status === 'pending'
              ? <button type="button" className="danger" disabled={busy !== null} onClick={() => void act(`invitation:${invitation.id}`, async () => { await admin.revokeInvitation(invitation.id); return `Invitation for ${inviteeLabel(invitation)} was revoked.`; })}><XCircle />Revoke</button>
              : <span />}
          </article>)}
        </div>
      </section>
    </>}
    <AcceptInvitation initial={linked} currentWorkspaceId={workspaceId} onAccepted={() => void refresh()} />
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

function AcceptInvitation({ initial, currentWorkspaceId, onAccepted }: { initial: { token: string; workspaceId: string | null } | null; currentWorkspaceId: string; onAccepted: () => void }) {
  const [input, setInput] = useState(initial?.token ?? '');
  const [workspace, setWorkspace] = useState(initial?.workspaceId ?? '');
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(initial ? { text: 'Invitation link opened. Check the workspace, then accept.' } : null);
  return <form className="surface adminPanel" aria-label="Accept an invitation" onSubmit={(event) => {
    event.preventDefault();
    const parsed = parseInvitationInput(input);
    if (!parsed) { setFlash({ text: 'Paste an invitation token (it starts with anum_inv_) or the whole invitation link.', error: true }); return; }
    const target = (parsed.workspaceId ?? workspace.trim()) || null;
    setBusy(true);
    setFlash(null);
    void admin.acceptInvitation(parsed.token, target).then((result) => {
      setInput('');
      setFlash({ text: `You joined ${result.membership.workspaceId} as ${roleLabel[result.membership.role].toLowerCase()}.${result.membership.workspaceId !== currentWorkspaceId ? ' Switch to that workspace to work in it.' : ''}` });
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
      }} placeholder="anum_inv_…" autoComplete="off" spellCheck={false} /></label>
      <label className="field"><span>Workspace</span><input value={workspace} onChange={(event) => setWorkspace(event.target.value)} placeholder={currentWorkspaceId} autoComplete="off" /></label>
    </div>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <div className="actions"><button type="submit" disabled={busy || !input.trim()}><UserCheck />{busy ? 'Accepting...' : 'Accept invitation'}</button></div>
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
        <BudgetCard scope="tenant" title="Organization" subtitle="All workspaces in this organization together" view={overview.tenant} onSaved={setOverview} />
        <BudgetCard scope="workspace" title="This workspace" subtitle="Task planning and voice answers here" view={overview.workspace} onSaved={setOverview} />
      </div>
    </>}
  </>;
}

function BudgetCard({ scope, title, subtitle, view, onSaved }: { scope: BudgetScope; title: string; subtitle: string; view: ModelBudgetScopeView; onSaved: (next: ModelBudgetOverview) => void }) {
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
      .catch((error: unknown) => setFlash({ text: isPermissionDenied(error) ? `Only owners can change budgets. ${describeError(error)}` : describeError(error), error: true }))
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
    <form className="budgetForm" aria-label={`Edit ${title.toLowerCase()} limits`} onSubmit={(event) => { event.preventDefault(); save(draft); }}>
      <label className="field"><span>Monthly cost limit (USD)</span><input inputMode="decimal" value={draft.cost} onChange={(event) => setDraft({ ...draft, cost: event.target.value })} placeholder="No limit" /></label>
      <label className="field"><span>Monthly token limit</span><input inputMode="numeric" value={draft.tokens} onChange={(event) => setDraft({ ...draft, tokens: event.target.value })} placeholder="No limit" /></label>
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      <p className="muted">Leave a field empty for no limit. 0 blocks every model call.</p>
      <div className="actions">
        <button type="submit" disabled={busy}><Save />{busy ? 'Saving...' : 'Save limits'}</button>
        {view.budget && <button type="button" className="secondary" disabled={busy} onClick={() => save({ cost: '', tokens: '' })}><XCircle />Remove limits</button>}
      </div>
    </form>
  </section>;
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
