// The Team page's people tabs (docs/team-work.md#team-page), from the owner's mockups 32, 38, 39, 46
// and 47: Directory (people with their profile, role and open work, a member panel beside the
// table), Departments (people by department with their work), Roles and access (what each role may
// do, from GET /workspaces/current/roles) and Invitations (the workspace invitations, invite,
// resend and revoke for owners). Everything comes from the live API; a 403 is explained in place.
import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import { Check, ChevronRight, CircleDot, ExternalLink, Mail, MailPlus, Minus, Pencil, RefreshCw, Search, ShieldCheck, UserRound, Users, X } from 'lucide-react';
import type { CreatedInvitation, TeamPerson, WorkspaceInvitation, WorkspacePermissions, Workload } from '@anum/contracts';
import * as admin from './lib/adminApi';
import { inviteeLabel } from './lib/admin';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { type Load } from './lib/pageSummary';
import { accessMatrix, departmentRows, filterPeople, initialsOf, NO_DIRECTORY_FILTER, type AccessLevel, type DirectoryFilter } from './lib/teamPage';
import { currentUserId } from './lib/api';
import { InviteForm, invitationState, TokenReveal } from './AdminViews';
import { CallButtons } from './AppCalls';
import { ConfirmDialog, Modal } from './Dialog';
import { FinCard, StatusChip } from './FinanceShell';
import { loadFailure, PaneDenied, PaneProblem, SPLIT_QUERY, useMediaQuery } from './PageParts';
import { ProfileForm } from './TeamView';

export const deptLabel = (department: string | null) => (department ? t(`team.dept.${department}` as MessageKey) : t('teamPage.dept.none'));
export const roleName = (role: string) => t(`team.role.${role}` as MessageKey);
const areaLabel = (area: string) => t(`teamPage.area.${area}` as MessageKey);
const LEVEL_TONE: Record<AccessLevel, 'ok' | 'info' | 'muted' | 'bad'> = { full: 'ok', edit: 'info', read: 'muted', none: 'bad' };

export function Avatar({ name }: { name: string }) { return <span className="tpAvatar" aria-hidden="true">{initialsOf(name)}</span>; }

function Denied({ load, title }: { load: Load<unknown>; title: string }) {
  if (load.state === 'denied') return <PaneDenied title={title} text={load.message} />;
  return null;
}

// ------------------------------------------------------------------------- directory

export function DirectoryTab({ people, peopleLoad, workload, roles, onReload, onOpenOrg }: {
  people: TeamPerson[]; peopleLoad: Load<unknown>; workload: Workload | null; roles: Load<admin.RolePermissions[]>; onReload(): void; onOpenOrg(): void;
}) {
  const [filter, setFilter] = useState<DirectoryFilter>(NO_DIRECTORY_FILTER);
  const [selected, setSelected] = useState<string | null>(null);
  const wide = useMediaQuery(SPLIT_QUERY);
  const ids = useId();
  const shown = useMemo(() => filterPeople(people, filter), [people, filter]);
  const departments = useMemo(() => [...new Set(people.map((p) => p.department).filter((d): d is NonNullable<TeamPerson['department']> => Boolean(d)))].sort(), [people]);
  const person = people.find((p) => p.user_id === selected) ?? null;
  const filtered = filter.q !== '' || filter.department !== '' || filter.role !== '';
  const showTable = wide || !person;
  return <div className={`tpSplit ${person ? 'withDetail' : ''}`}>
    {showTable && <FinCard title={t('teamPage.directory.title')} subtitle={peopleLoad.state === 'ready' ? t('teamPage.directory.count', { count: shown.length }) : undefined} icon={<Users />}
      actions={<button type="button" className="secondary" onClick={onOpenOrg}>{t('teamPage.viewOrg')}</button>}>
      <div className="tpFilters" role="search">
        <label className="visuallyHidden" htmlFor={`${ids}-q`}>{t('teamPage.directory.search')}</label>
        <span className="tpSearch"><Search aria-hidden="true" /><input id={`${ids}-q`} type="search" value={filter.q} maxLength={120} placeholder={t('teamPage.directory.search')} onChange={(e) => setFilter({ ...filter, q: e.target.value })} /></span>
        <label className="tpSelect"><span>{t('teamPage.col.department')}</span><select value={filter.department} onChange={(e) => setFilter({ ...filter, department: e.target.value })}>
          <option value="">{t('teamPage.allDepartments')}</option>{departments.map((d) => <option key={d} value={d}>{deptLabel(d)}</option>)}<option value="none">{t('teamPage.dept.none')}</option></select></label>
        <label className="tpSelect"><span>{t('teamPage.col.role')}</span><select value={filter.role} onChange={(e) => setFilter({ ...filter, role: e.target.value })}>
          <option value="">{t('teamPage.allRoles')}</option>{['owner', 'member', 'viewer'].map((r) => <option key={r} value={r}>{roleName(r)}</option>)}</select></label>
        {filtered && <button type="button" className="secondary" onClick={() => setFilter(NO_DIRECTORY_FILTER)}>{t('teamPage.clear')}</button>}
      </div>
      {peopleLoad.state === 'loading' && <p className="notice" role="status">{t('team.loading')}</p>}
      {peopleLoad.state === 'error' && <PaneProblem text={t('team.directoryFailed', { message: peopleLoad.message })} onRetry={onReload} />}
      <Denied load={peopleLoad} title={t('teamPage.denied')} />
      {peopleLoad.state === 'ready' && !people.length && <div className="paneEmpty"><Users aria-hidden="true" /><h3>{t('team.empty.people')}</h3><p>{t('team.empty.peopleText')}</p></div>}
      {peopleLoad.state === 'ready' && people.length > 0 && !shown.length && <div className="paneEmpty"><Search aria-hidden="true" /><h3>{t('team.empty.filtered')}</h3><p>{t('teamPage.directory.noMatch')}</p>
        <div className="actions"><button type="button" className="secondary" onClick={() => setFilter(NO_DIRECTORY_FILTER)}>{t('teamPage.clear')}</button></div></div>}
      {shown.length > 0 && <div className="finTableWrap" tabIndex={0} aria-label={t('teamPage.tableScroll')}>
        <table className="finGrid tpTable">
          <caption className="visuallyHidden">{t('teamPage.directory.title')}</caption>
          <thead><tr><th scope="col">{t('teamPage.col.name')}</th><th scope="col">{t('teamPage.col.role')}</th><th scope="col">{t('teamPage.col.department')}</th><th scope="col">{t('teamPage.col.title')}</th><th scope="col">{t('teamPage.col.zone')}</th><th scope="col" className="num">{t('teamPage.col.open')}</th><th scope="col">{t('teamPage.col.status')}</th></tr></thead>
          <tbody>{shown.map((p) => {
            const load = workload?.rows.find((r) => r.assignee_type === 'user' && r.assignee_id === p.user_id);
            return <tr key={p.user_id} className={selected === p.user_id ? 'selected' : undefined}>
              <th scope="row"><button type="button" className="tpPerson" aria-current={selected === p.user_id ? 'true' : undefined} onClick={() => setSelected(p.user_id)}>
                <Avatar name={p.display_name} /><span><strong dir="auto">{p.display_name}</strong><small dir="ltr">{p.email ?? p.user_id}</small></span></button></th>
              <td><StatusChip tone={p.role === 'owner' ? 'ok' : p.role === 'member' ? 'info' : 'muted'}>{roleName(p.role)}</StatusChip></td>
              <td>{deptLabel(p.department)}</td>
              <td dir="auto">{p.job_title ?? <span className="muted">-</span>}</td>
              <td dir="ltr" className="tpZone">{p.timezone}</td>
              <td className="num">{load ? formatNumber(load.open) : '0'}{load?.overdue ? <small className="tpLate"> {t('teamPage.overdueShort', { count: load.overdue })}</small> : null}</td>
              <td>{p.has_profile ? <StatusChip tone={p.assignable ? 'ok' : 'muted'}>{p.assignable ? t('teamPage.status.assignable') : t('teamPage.status.readOnly')}</StatusChip> : <StatusChip tone="warn">{t('teamPage.status.noProfile')}</StatusChip>}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
    </FinCard>}
    {person && <MemberPanel key={person.user_id} person={person} workload={workload} roles={roles} onClose={() => setSelected(null)} onChanged={onReload} />}
  </div>;
}

function MemberPanel({ person, workload, roles, onClose, onChanged }: { person: TeamPerson; workload: Workload | null; roles: Load<admin.RolePermissions[]>; onClose(): void; onChanged(): void }) {
  const [editing, setEditing] = useState(false);
  const me = currentUserId();
  const load = workload?.rows.find((r) => r.assignee_type === 'user' && r.assignee_id === person.user_id);
  const matrix = roles.state === 'ready' ? accessMatrix(roles.value) : null;
  const levels = matrix?.levels[person.role];
  const strong = matrix ? matrix.areas.filter((a) => levels?.[a] === 'full' || levels?.[a] === 'edit') : [];
  return <aside className="finCard tpPanel" aria-labelledby="tp-member-title">
    <div className="tpPanelHead">
      <Avatar name={person.display_name} />
      <div><h2 id="tp-member-title" dir="auto">{person.display_name}</h2><p className="muted" dir="auto">{person.job_title ?? roleName(person.role)}</p></div>
      <button type="button" className="secondary iconOnly tpClose" onClick={onClose} aria-label={t('teamPage.member.close')} title={t('teamPage.member.close')}><X aria-hidden="true" /></button>
    </div>
    <div className="tpChips"><StatusChip tone="info">{roleName(person.role)}</StatusChip><StatusChip tone={person.assignable ? 'ok' : 'muted'}>{person.assignable ? t('teamPage.status.assignable') : t('teamPage.status.readOnly')}</StatusChip></div>
    <dl className="tpFacts">
      <div><dt><Mail aria-hidden="true" /><span className="visuallyHidden">{t('teamPage.member.email')}</span></dt><dd dir="ltr">{person.email ?? t('teamPage.member.noEmail')}</dd></div>
      <div><dt><Users aria-hidden="true" /><span className="visuallyHidden">{t('teamPage.col.department')}</span></dt><dd>{deptLabel(person.department)}</dd></div>
      <div><dt><UserRound aria-hidden="true" /><span className="visuallyHidden">{t('teamPage.col.zone')}</span></dt><dd dir="ltr">{person.timezone}</dd></div>
    </dl>
    <section className="tpSection" aria-labelledby="tp-member-work">
      <h3 id="tp-member-work">{t('teamPage.member.work')}</h3>
      {load ? <ul className="tpNumbers">
        <li><strong>{formatNumber(load.open)}</strong><span>{t('team.load.open')}</span></li>
        <li><strong>{formatNumber(load.in_progress)}</strong><span>{t('team.status.in_progress')}</span></li>
        <li className={load.overdue ? 'bad' : ''}><strong>{formatNumber(load.overdue)}</strong><span>{t('team.load.overdue')}</span></li>
        <li><strong>{load.load_percent === null ? '-' : `${formatNumber(load.load_percent)}%`}</strong><span>{t('teamPage.member.capacity')}</span></li>
      </ul> : <p className="muted">{t('teamPage.member.noWork')}</p>}
    </section>
    <section className="tpSection" aria-labelledby="tp-member-access">
      <h3 id="tp-member-access"><ShieldCheck aria-hidden="true" />{t('teamPage.member.access', { role: roleName(person.role) })}</h3>
      {roles.state === 'ready' ? <p className="muted">{strong.length ? t('teamPage.member.accessText', { areas: strong.map(areaLabel).join(', ') }) : t('teamPage.member.readOnlyText')}</p>
        : roles.state === 'loading' ? <p className="muted">{t('team.loading')}</p> : <p className="muted">{t('teamPage.roles.failed', { reason: roles.message })}</p>}
    </section>
    <div className="tpPanelActions">
      <button type="button" onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{person.user_id === me ? t('team.editMine') : t('team.edit')}</button>
      {person.user_id !== me && <CallButtons person={person} />}
      <a className="secondaryLink" href="#members"><ShieldCheck aria-hidden="true" />{t('teamPage.member.manageAccess')}</a>
    </div>
    {editing && <Modal title={t('teamPage.member.editTitle', { name: person.display_name })} className="formDialog" onClose={() => setEditing(false)}>
      <ProfileForm person={person} onDone={() => { setEditing(false); onChanged(); }} />
    </Modal>}
  </aside>;
}

// ------------------------------------------------------------------------- departments

export function DepartmentsTab({ people, peopleLoad, workload, onReload, onPickPerson }: { people: TeamPerson[]; peopleLoad: Load<unknown>; workload: Workload | null; onReload(): void; onPickPerson(): void }) {
  const rows = useMemo(() => departmentRows(people, workload), [people, workload]);
  const [open, setOpen] = useState<string | null>(null);
  const current = rows.find((r) => (r.department ?? 'none') === open) ?? null;
  return <div className={`tpSplit ${current ? 'withDetail' : ''}`}>
    <FinCard title={t('teamPage.departments.title')} subtitle={t('teamPage.departments.count', { count: rows.filter((r) => r.department).length })} icon={<Users />}>
      <p className="muted">{t('teamPage.departments.intro')}</p>
      {peopleLoad.state === 'loading' && <p className="notice" role="status">{t('team.loading')}</p>}
      {peopleLoad.state === 'error' && <PaneProblem text={t('team.directoryFailed', { message: peopleLoad.message })} onRetry={onReload} />}
      <Denied load={peopleLoad} title={t('teamPage.denied')} />
      {peopleLoad.state === 'ready' && !rows.length && <div className="paneEmpty"><Users aria-hidden="true" /><h3>{t('team.empty.people')}</h3><p>{t('team.empty.peopleText')}</p></div>}
      {rows.length > 0 && <div className="finTableWrap" tabIndex={0} aria-label={t('teamPage.tableScroll')}>
        <table className="finGrid tpTable">
          <caption className="visuallyHidden">{t('teamPage.departments.title')}</caption>
          <thead><tr><th scope="col">{t('teamPage.col.department')}</th><th scope="col" className="num">{t('teamPage.col.headcount')}</th><th scope="col">{t('teamPage.col.people')}</th><th scope="col" className="num">{t('team.load.open')}</th><th scope="col" className="num">{t('team.load.overdue')}</th><th scope="col" className="num">{t('team.load.week')}</th></tr></thead>
          <tbody>{rows.map((r) => {
            const key = r.department ?? 'none';
            return <tr key={key} className={open === key ? 'selected' : undefined}>
              <th scope="row"><button type="button" className="tpPerson" aria-current={open === key ? 'true' : undefined} onClick={() => setOpen(key)}><strong>{deptLabel(r.department)}</strong><ChevronRight aria-hidden="true" className="flipRtl" /></button></th>
              <td className="num">{formatNumber(r.people.length)}</td>
              <td dir="auto">{r.people.slice(0, 3).map((p) => p.display_name).join(', ')}{r.people.length > 3 ? ` ${t('teamPage.morePeople', { count: r.people.length - 3 })}` : ''}</td>
              <td className="num">{formatNumber(r.open)}</td>
              <td className={`num ${r.overdue ? 'tpLate' : ''}`}>{formatNumber(r.overdue)}</td>
              <td className="num">{formatNumber(r.dueThisWeek)}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
    </FinCard>
    {current && <aside className="finCard tpPanel" aria-labelledby="tp-dept-title">
      <div className="tpPanelHead"><span className="tpAvatar dept" aria-hidden="true"><Users /></span>
        <div><h2 id="tp-dept-title">{deptLabel(current.department)}</h2><p className="muted">{t('teamPage.directory.count', { count: current.people.length })}</p></div>
        <button type="button" className="secondary iconOnly tpClose" onClick={() => setOpen(null)} aria-label={t('teamPage.member.close')} title={t('teamPage.member.close')}><X aria-hidden="true" /></button></div>
      <ul className="tpNumbers">
        <li><strong>{formatNumber(current.open)}</strong><span>{t('team.load.open')}</span></li>
        <li><strong>{formatNumber(current.inProgress)}</strong><span>{t('team.status.in_progress')}</span></li>
        <li className={current.overdue ? 'bad' : ''}><strong>{formatNumber(current.overdue)}</strong><span>{t('team.load.overdue')}</span></li>
        <li><strong>{formatNumber(current.dueThisWeek)}</strong><span>{t('team.load.week')}</span></li>
      </ul>
      <section className="tpSection" aria-labelledby="tp-dept-people"><h3 id="tp-dept-people">{t('teamPage.col.people')}</h3>
        <ul className="tpPeopleList">{current.people.map((p) => <li key={p.user_id}><Avatar name={p.display_name} /><span><strong dir="auto">{p.display_name}</strong><small dir="auto">{p.job_title ?? roleName(p.role)}</small></span></li>)}</ul>
      </section>
      <p className="muted">{t('teamPage.departments.howTo')} <button type="button" className="linkButton" onClick={onPickPerson}>{t('teamPage.tab.directory')}</button></p>
    </aside>}
  </div>;
}

// ------------------------------------------------------------------------- roles and access

export function RolesTab({ roles, people, onRetry, permissions }: { roles: Load<admin.RolePermissions[]>; people: TeamPerson[]; onRetry(): void; permissions: WorkspacePermissions | null }) {
  const matrix = roles.state === 'ready' ? accessMatrix(roles.value) : null;
  const order = ['owner', 'member', 'viewer'];
  const count = (role: string) => people.filter((p) => p.role === role).length;
  return <div className="tpStack">
    <FinCard title={t('teamPage.roles.title')} icon={<ShieldCheck />} subtitle={t('teamPage.roles.subtitle')}
      actions={permissions?.canManageMembers ? <a className="secondaryLink" href="#members"><ExternalLink aria-hidden="true" />{t('teamPage.roles.manage')}</a> : undefined}>
      {roles.state === 'loading' && <p className="notice" role="status">{t('team.loading')}</p>}
      {roles.state === 'error' && <PaneProblem text={t('teamPage.roles.failed', { reason: roles.message })} onRetry={onRetry} />}
      <Denied load={roles} title={t('teamPage.denied')} />
      {matrix && <>
        <ul className="tpRoleCards">{order.map((role) => <li key={role}>
          <strong>{roleName(role)}</strong><span className="muted">{t(`teamPage.roles.about.${role}` as MessageKey)}</span>
          <small>{t('teamPage.roles.people', { count: count(role) })}</small>
        </li>)}</ul>
        <div className="finTableWrap" tabIndex={0} aria-label={t('teamPage.tableScroll')}>
          <table className="finGrid tpMatrix">
            <caption className="visuallyHidden">{t('teamPage.roles.matrix')}</caption>
            <thead><tr><th scope="col">{t('teamPage.col.module')}</th>{order.map((role) => <th key={role} scope="col">{roleName(role)}</th>)}</tr></thead>
            <tbody>{matrix.areas.map((area) => <tr key={area}><th scope="row">{areaLabel(area)}</th>
              {order.map((role) => { const level = matrix.levels[role]?.[area] ?? 'none'; return <td key={role}><StatusChip tone={LEVEL_TONE[level]}>{level === 'none' ? <Minus aria-hidden="true" /> : <Check aria-hidden="true" />}{t(`teamPage.level.${level}` as MessageKey)}</StatusChip></td>; })}
            </tr>)}</tbody>
          </table>
        </div>
        <p className="muted tpNote">{t('teamPage.roles.note')}</p>
      </>}
    </FinCard>
  </div>;
}

// ------------------------------------------------------------------------- invitations

export function InvitationsTab({ permissions, onChanged }: { permissions: WorkspacePermissions | null; onChanged(): void }) {
  const [list, setList] = useState<Load<WorkspaceInvitation[]>>({ state: 'loading' });
  const [inviting, setInviting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  const [revoke, setRevoke] = useState<WorkspaceInvitation | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const manage = permissions?.canManageMembers ?? false;
  const load = useCallback(() => { admin.getInvitations('all').then((value) => setList({ state: 'ready', value }), (e: unknown) => setList(loadFailure(e))); }, []);
  useEffect(load, [load]);
  const act = async (key: string, run: () => Promise<string>) => {
    setBusy(key); setFlash(null);
    try { setFlash({ text: await run() }); load(); onChanged(); } catch (e) { setFlash({ text: isPermissionDenied(e) ? t('team.denied', { message: describeError(e) }) : describeError(e), error: true }); } finally { setBusy(null); }
  };
  const rows = list.state === 'ready' ? list.value : [];
  const groups: { key: string; title: string; items: WorkspaceInvitation[] }[] = [
    { key: 'pending', title: t('teamPage.inv.pending'), items: rows.filter((i) => i.status === 'pending') },
    { key: 'accepted', title: t('teamPage.inv.accepted'), items: rows.filter((i) => i.status === 'accepted') },
    { key: 'closed', title: t('teamPage.inv.closed'), items: rows.filter((i) => i.status !== 'pending' && i.status !== 'accepted') },
  ];
  return <div className="tpStack">
    {created && <TokenReveal created={created} onDone={() => setCreated(null)} />}
    <FinCard title={t('teamPage.inv.title')} icon={<Mail />} subtitle={list.state === 'ready' ? t('teamPage.inv.count', { count: groups[0].items.length }) : undefined}
      actions={manage ? <button type="button" onClick={() => setInviting(true)} aria-haspopup="dialog"><MailPlus aria-hidden="true" />{t('teamPage.invite')}</button> : undefined}>
      {!manage && permissions && <p className="notice" role="note">{t('teamPage.inv.ownersOnly')}</p>}
      {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
      {list.state === 'loading' && <p className="notice" role="status">{t('team.loading')}</p>}
      {list.state === 'error' && <PaneProblem text={t('teamPage.inv.failed', { reason: list.message })} onRetry={load} />}
      <Denied load={list} title={t('teamPage.inv.denied')} />
      {list.state === 'ready' && !rows.length && <div className="paneEmpty"><Mail aria-hidden="true" /><h3>{t('teamPage.inv.empty')}</h3><p>{manage ? t('teamPage.inv.emptyText') : t('teamPage.inv.ownersOnly')}</p>
        {manage && <div className="actions"><button type="button" onClick={() => setInviting(true)}><MailPlus aria-hidden="true" />{t('teamPage.invite')}</button></div>}</div>}
      {rows.length > 0 && <div className="finTableWrap" tabIndex={0} aria-label={t('teamPage.tableScroll')}>
        <table className="finGrid tpTable tpInv">
          <caption className="visuallyHidden">{t('teamPage.inv.title')}</caption>
          <thead><tr><th scope="col">{t('teamPage.inv.who')}</th><th scope="col">{t('teamPage.col.status')}</th><th scope="col">{t('teamPage.col.role')}</th><th scope="col" className="tpWideOnly">{t('teamPage.inv.sent')}</th><th scope="col" className="tpWideOnly">{t('teamPage.inv.state')}</th>{manage && <th scope="col"><span className="visuallyHidden">{t('teamPage.inv.actions')}</span></th>}</tr></thead>
          {groups.filter((g) => g.items.length).map((g) => <tbody key={g.key}>
            <tr className="tpGroupRow"><th scope="colgroup" colSpan={manage ? 6 : 5}>{g.title} <span>({formatNumber(g.items.length)})</span></th></tr>
            {g.items.map((inv) => <tr key={inv.id}>
              <th scope="row"><span className="tpPerson static"><Avatar name={inviteeLabel(inv)} /><span><strong dir="ltr">{inviteeLabel(inv)}</strong></span></span></th>
              <td><StatusChip tone={inv.status === 'pending' ? 'warn' : inv.status === 'accepted' ? 'ok' : 'muted'}>{t(`teamPage.inv.status.${inv.status}` as MessageKey)}</StatusChip></td>
              <td>{roleName(inv.role)}</td>
              <td className="tpWideOnly">{formatDateTime(inv.createdAt, { day: 'numeric', month: 'short', year: 'numeric' })}</td>
              <td className="tpState tpWideOnly">{invitationState(inv)}</td>
              {manage && <td><div className="tpRowActions">{inv.status === 'pending' || inv.status === 'expired' ? <>
                <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(`resend:${inv.id}`, async () => { const result = await admin.resendInvitation(inv.id); setCreated(result); return t('teamPage.inv.resent', { who: inviteeLabel(inv) }); })}><RefreshCw aria-hidden="true" />{t('teamPage.inv.resend')}</button>
                {inv.status === 'pending' && <button type="button" className="secondary dangerText" disabled={busy !== null} onClick={() => setRevoke(inv)}>{t('teamPage.inv.revoke')}</button>}
              </> : null}</div></td>}
            </tr>)}
          </tbody>)}
        </table>
      </div>}
    </FinCard>
    {inviting && <Modal title={t('teamPage.invite')} className="formDialog" onClose={() => setInviting(false)}>
      <InviteForm busy={busy === 'create'} onCreate={async (request) => {
        setBusy('create'); setFlash(null);
        try { const result = await admin.createInvitation(request); setCreated(result); setInviting(false); load(); onChanged(); return true; }
        catch (e) { setFlash({ text: describeError(e), error: true }); setInviting(false); return false; } finally { setBusy(null); }
      }} />
    </Modal>}
    {revoke && <ConfirmDialog title={t('teamPage.inv.revokeTitle')} confirmLabel={t('teamPage.inv.revoke')} danger busy={busy === 'revoke'} onCancel={() => setRevoke(null)}
      onConfirm={() => { const inv = revoke; setRevoke(null); void act('revoke', async () => { await admin.revokeInvitation(inv.id); return t('teamPage.inv.revoked', { who: inviteeLabel(inv) }); }); }}>
      <p>{t('teamPage.inv.revokeText', { who: inviteeLabel(revoke) })}</p>
    </ConfirmDialog>}
  </div>;
}

export function TabNote({ children }: { children: ReactNode }) { return <p className="muted tpNote">{children}</p>; }
