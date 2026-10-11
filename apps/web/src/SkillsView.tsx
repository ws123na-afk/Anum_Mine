import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { CircleDot, History, Info, PackageCheck, PauseCircle, PlayCircle, Plus, RefreshCw, Search, ShieldAlert, Trash2, Undo2, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { approvalTone, emptyPublishDraft, filterSkills, groupSkills, historyLabel, pageOf, publishBody, rollbackTargets, type PublishDraft, type SkillDetail, type SkillFilter, type SkillGroup, type SkillVersionLike } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { getGovernanceSummary } from './lib/governanceApi';
import { useIsOwner } from './useIsOwner';
import './features.css';
import './catalog.css';

const PAGE = 12;
const when = (iso: string) => new Date(iso).toLocaleString();
const failure = (e: unknown, who: string) => isPermissionDenied(e) ? `Not allowed: ${describeError(e)} ${who}` : describeError(e);
const OWNER_ONLY = 'Only workspace owners can install, enable, disable or uninstall skills.';
const ADMIN_ONLY = 'Only organization administrators can publish versions or roll an installation back.';

/** Published versions and this workspace's installations, shared by the Skills and Agents views. */
export function useSkills() {
  const [versions, setVersions] = useState<api.SkillVersion[]>([]);
  const [installs, setInstalls] = useState<api.SkillInstallation[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try { const [v, i] = await Promise.all([api.getSkillVersions(), api.getSkillInstallations()]); setVersions(v); setInstalls(i); setError(null); }
    catch (e) { setError(`Could not load skills: ${describeError(e)} Check that the API is reachable, then Refresh.`); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const act = useCallback(async (fn: () => Promise<string>, who = OWNER_ONLY) => {
    setBusy(true); setError(null); setNote(null);
    try { setNote(await fn()); await load(); return true; } catch (e) { setError(failure(e, who)); return false; } finally { setBusy(false); }
  }, [load]);
  const toggle = (skillId: string, enabled: boolean) => act(async () => { await api.setSkillEnabled(skillId, enabled); return enabled ? 'Skill enabled.' : 'Skill disabled.'; });
  return { versions, installs, loading, busy, error, note, load, act, toggle };
}

export function Feedback({ error, note, loading, label }: { error: string | null; note: string | null; loading: boolean; label: string }) {
  return <>
    {error && <p className="notice errorNotice" role="alert"><CircleDot />{error}</p>}
    {note && <p className="notice" role="status"><CircleDot />{note}</p>}
    {loading && <p className="notice" role="status"><CircleDot />Loading {label}...</p>}
  </>;
}

/** Whether the caller is an organization administrator (the API's own answer); null while loading. */
export function useOrgAdmin(): boolean | null {
  const [admin, setAdmin] = useState<boolean | null>(null);
  useEffect(() => { void getGovernanceSummary().then((s) => setAdmin(s.can_manage_organization_policy === true)).catch(() => setAdmin(false)); }, []);
  return admin;
}

/** A modal dialog: focus moves in, Escape and the backdrop close it, focus returns on close. */
export function Dialog({ title, onClose, children, wide }: { title: string; onClose(): void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('[autofocus], input, select, textarea, button')?.focus();
    // Escape closes the dialog wherever focus is (it can drop to the body after a button disappears).
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close.current(); };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, []);
  const id = `dialog-${title.replace(/[^A-Za-z0-9]+/g, '-')}`;
  return <div className="featureBackdrop" onClick={onClose}>
    <section ref={ref} className={`surface featureDialog ${wide ? 'catalogDialogWide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={id} onClick={(e) => e.stopPropagation()}>
      <header><h3 id={id}>{title}</h3><button type="button" className="iconButton" aria-label="Close" onClick={onClose}><X /></button></header>
      {children}
    </section>
  </div>;
}

function InstallDialog({ group, version, busy, onConfirm, onCancel }: { group: SkillGroup; version: SkillVersionLike; busy: boolean; onConfirm(tools: string[]): void; onCancel(): void }) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void catalog.getSkillDetail(group.skillId, version.version).then(setDetail).catch((e) => setError(describeError(e))); }, [group.skillId, version.version]);
  const tools = [...new Set(version.required_tools)].sort();
  const replacing = group.install && group.install.version !== version.version ? group.install.version : null;
  return <Dialog title={`Install ${version.name} ${version.version}?`} onClose={onCancel}>
    <p>{version.description}</p>
    {replacing && <p className="featureMeta">Replaces the installed version {replacing} in this workspace.</p>}
    <p className={`featureChip ${['high', 'blocked'].includes(version.risk_level) ? 'failed' : 'indexed'}`}>{['high', 'blocked'].includes(version.risk_level) && <ShieldAlert />}Skill risk: {version.risk_level}</p>
    {tools.length ? <>
      <h4>Tools granted to agents in this workspace</h4>
      {error && <p className="notice errorNotice" role="alert"><CircleDot />Could not load tool details: {error}</p>}
      <ul className="catalogToolList">{tools.map((name) => {
        const t = detail?.tools.find((x) => x.name === name);
        return <li key={name}><code>{name}</code>{t ? <><span className={`statusBadge ${approvalTone(t.approval)}`}>{t.risk_level ?? 'unavailable'}</span><small>{t.approval_text}</small></> : !error && <small>Loading risk...</small>}</li>;
      })}</ul>
    </> : <p className="featureMeta">This skill requests no tools.</p>}
    <p className="featureMeta">Tool calls stay mediated by the runtime and workspace policy; approvals still apply. The install is recorded in the audit log.</p>
    <div className="actions"><button type="button" className="secondary" onClick={onCancel}>Cancel</button><button type="button" autoFocus disabled={busy} onClick={() => onConfirm(tools)}><PackageCheck />Install and grant</button></div>
  </Dialog>;
}

function DetailDialog({ skillId, owner, onInstall, onChanged, onClose }: { skillId: string; owner: boolean | null; onInstall(version: SkillVersionLike): void; onChanged(): void; onClose(): void }) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => catalog.getSkillDetail(skillId).then((d) => { setDetail(d); setError(null); }).catch((e) => setError(describeError(e))), [skillId]);
  useEffect(() => { void load(); }, [load]);
  const rollback = async (version: string) => {
    setBusy(true); setFlash(null);
    try { await catalog.rollbackSkill(skillId, version); setFlash({ text: `Rolled back to ${version}. Grants not declared by ${version} were removed.` }); setConfirm(null); await load(); onChanged(); }
    catch (e) { setFlash({ text: failure(e, ADMIN_ONLY), error: true }); } finally { setBusy(false); }
  };
  const targets = detail ? rollbackTargets(detail.versions, detail.installation?.version) : [];
  return <Dialog title={detail ? `${detail.name}` : 'Skill details'} onClose={onClose} wide>
    {!detail && !error && <p className="notice" role="status"><CircleDot />Loading skill details...</p>}
    {error && <p className="notice errorNotice" role="alert"><CircleDot />Could not load the skill: {error}</p>}
    {detail && <>
      <p>{detail.description}</p>
      <p className="featureMeta"><code>{detail.skill_id}</code> · latest {detail.latest_version} · {detail.installation ? `installed ${detail.installation.version} (${detail.installation.enabled ? 'enabled' : 'disabled'})` : 'not installed in this workspace'}</p>
      <section aria-labelledby="skill-tools"><h4 id="skill-tools">Tools in {detail.tools_version}</h4>
        {detail.tools.length === 0 ? <p className="featureMeta">This version requests no tools.</p> :
          <ul className="catalogToolList">{detail.tools.map((t) => <li key={t.name}><code>{t.name}</code><span className={`statusBadge ${approvalTone(t.approval)}`}>{t.risk_level ?? 'unavailable'}</span>{detail.installation && <span className={`statusBadge ${t.granted ? 'success' : 'neutral'}`}>{t.granted ? 'granted' : 'not granted'}</span>}<small>{t.description ? `${t.description} ` : ''}{t.approval_text}{t.target ? ` Contacts ${t.target}.` : ''}</small></li>)}</ul>}
      </section>
      <section aria-labelledby="skill-versions"><h4 id="skill-versions">Versions</h4>
        <ul className="catalogVersionList">{detail.versions.map((v) => {
          const installed = detail.installation?.version === v.version;
          return <li key={v.id}><div><strong>{v.version}</strong>{installed && <span className="statusBadge success">installed</span>}<small>Published {when(v.created_at)} by {v.created_by} · risk {v.risk_level} · {v.required_tools.length} tool{v.required_tools.length === 1 ? '' : 's'}</small></div>
            <div className="catalogVersionActions">
              {!installed && owner && (!detail.installation || !targets.includes(v.version)) && <button type="button" className="secondary" onClick={() => onInstall(v)}>{detail.installation ? 'Install this version' : 'Install'}</button>}
              {detail.can_rollback && targets.includes(v.version) && (confirm === v.version
                ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={() => void rollback(v.version)}>Confirm rollback</button><button type="button" className="secondary" onClick={() => setConfirm(null)}>Cancel</button></span>
                : <button type="button" className="secondary" onClick={() => setConfirm(v.version)}><Undo2 size={15} />Roll back to {v.version}</button>)}
            </div></li>;
        })}</ul>
        {detail.installation && targets.length > 0 && !detail.can_rollback && <p className="featureMeta">{ADMIN_ONLY}</p>}
      </section>
      {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot />{flash.text}</p>}
      <section aria-labelledby="skill-history"><h4 id="skill-history"><History size={16} aria-hidden="true" /> History in this workspace</h4>
        {detail.history.length === 0 ? <p className="featureMeta">No changes recorded in this workspace yet.</p> :
          <ol className="catalogHistory">{detail.history.map((h) => <li key={h.id}><span>{historyLabel(h)}</span><small>{h.actor} · {when(h.created_at)}</small></li>)}</ol>}
      </section>
    </>}
  </Dialog>;
}

function PublishDialog({ onClose, onPublished }: { onClose(): void; onPublished(text: string): void }) {
  const [draft, setDraft] = useState<PublishDraft>(emptyPublishDraft());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof PublishDraft) => (e: { target: { value: string } }) => setDraft({ ...draft, [key]: e.target.value });
  const submit = async () => {
    const body = publishBody(draft);
    if (!body.ok) { setError(body.error); return; }
    setBusy(true); setError(null);
    try { const v = await catalog.publishSkillVersion(body.value); onPublished(`${v.name} ${v.version} published to every workspace of the organization.`); }
    catch (e) { setError(failure(e, ADMIN_ONLY)); } finally { setBusy(false); }
  };
  return <Dialog title="Publish a skill version" onClose={onClose} wide>
    <form className="catalogForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <label className="field"><span>Skill id</span><input value={draft.skillId} onChange={set('skillId')} placeholder="acme.release-notes" /></label>
      <label className="field"><span>Version</span><input value={draft.version} onChange={set('version')} /></label>
      <label className="field"><span>Name</span><input value={draft.name} maxLength={160} onChange={set('name')} /></label>
      <label className="field"><span>Risk level</span><select value={draft.risk} onChange={set('risk')}>{['low', 'medium', 'high', 'blocked'].map((r) => <option key={r} value={r}>{r}</option>)}</select></label>
      <label className="field catalogFormWide"><span>Description</span><input value={draft.description} maxLength={2000} onChange={set('description')} /></label>
      <label className="field catalogFormWide"><span>Required tools (comma separated, for example web_search)</span><input value={draft.tools} onChange={set('tools')} /></label>
      <label className="field catalogFormWide"><span>Instructions agents follow</span><textarea rows={5} value={draft.instructions} maxLength={50000} onChange={set('instructions')} /></label>
      <p className="featureMeta catalogFormWide">Versions are immutable: publish a new version to change one. Instructions never grant tools; owners approve tools when they install.</p>
      {error && <p className="notice errorNotice catalogFormWide" role="alert"><CircleDot />{error}</p>}
      <div className="actions catalogFormWide"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button type="submit" disabled={busy}><Plus />Publish</button></div>
    </form>
  </Dialog>;
}

export function SkillsView() {
  const s = useSkills();
  const owner = useIsOwner();
  const admin = useOrgAdmin();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<SkillFilter>('all');
  const [pages, setPages] = useState(1);
  const [installing, setInstalling] = useState<{ group: SkillGroup; version: SkillVersionLike } | null>(null);
  const [details, setDetails] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const groups = useMemo(() => groupSkills(s.versions, s.installs), [s.versions, s.installs]);
  const filtered = filterSkills(groups, query, filter);
  const { shown, remaining } = pageOf(filtered, pages, PAGE);
  useEffect(() => setPages(1), [query, filter]);
  const install = async (group: SkillGroup, version: SkillVersionLike, tools: string[]) => {
    if (await s.act(async () => { await api.installSkill(group.skillId, version.version, tools); return `${version.name} ${version.version} installed.`; })) { setInstalling(null); setDetails(null); }
  };
  const uninstall = (skillId: string) => void s.act(async () => { await api.uninstallSkill(skillId); setConfirmRemove(null); return 'Skill uninstalled.'; });
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Governed capabilities</p><h2>Skills</h2><p>Reviewed instructions for agents, with the tools each one needs, their risk and when they wait for approval.</p></div>
      <div className="actions">{admin && <button type="button" onClick={() => setPublishing(true)}><Plus />Publish version</button>}<button type="button" className="secondary" onClick={() => void s.load()} disabled={s.loading}><RefreshCw />Refresh</button></div></section>
    <Feedback error={s.error} note={s.note} loading={s.loading && !groups.length} label="skills" />
    {owner === false && <p className="notice" role="status"><CircleDot />{OWNER_ONLY} Ask an owner of this workspace.</p>}
    {groups.length > 0 && <div className="surface catalogToolbar" role="search">
      <label className="field"><span>Search skills</span><input type="search" value={query} placeholder="Name, id, description or tool" onChange={(e) => setQuery(e.target.value)} /></label>
      <fieldset className="catalogSegments"><legend>Show</legend>{(['all', 'installed', 'available'] as const).map((f) => <label key={f}><input type="radio" name="skill-filter" checked={filter === f} onChange={() => setFilter(f)} />{f === 'all' ? 'All' : f === 'installed' ? 'Installed' : 'Not installed'}</label>)}</fieldset>
      <p className="featureMeta" aria-live="polite">{filtered.length} of {groups.length} skill{groups.length === 1 ? '' : 's'}</p>
    </div>}
    <div className="integrationGrid">{shown.map((g) => <article className="surface integration catalogCard" key={g.skillId} id={`skill-${g.skillId.replace(/[^A-Za-z0-9_-]/g, '-')}`} aria-labelledby={`skill-title-${g.skillId}`}>
      <div className="integrationIcon"><PackageCheck /></div>
      <div>
        <h3 id={`skill-title-${g.skillId}`}>{g.name} <small>{g.install ? g.install.version : g.latest.version}</small></h3>
        <p>{g.description}</p>
        <div className="tagRow">{(g.install ? g.versions.find((v) => v.version === g.install!.version) ?? g.latest : g.latest).required_tools.map((x) => <span key={x}>{x}</span>)}</div>
        <small>{g.versions.length} version{g.versions.length === 1 ? '' : 's'} · risk {g.latest.risk_level}{g.upgrade ? ` · ${g.upgrade.version} available` : ''}</small>
      </div>
      <div className="featureSkillActions">
        <span className={`statusBadge ${g.state === 'installed' ? 'success' : g.state === 'disabled' ? 'warning' : 'neutral'}`}>{g.state === 'available' ? 'not installed' : g.state}</span>
        <button type="button" className="secondary" onClick={() => setDetails(g.skillId)}><Info size={15} />Details</button>
        {owner && !g.install && <button type="button" disabled={s.busy} onClick={() => setInstalling({ group: g, version: g.latest })}>Install</button>}
        {owner && g.upgrade && <button type="button" className="secondary" disabled={s.busy} onClick={() => setInstalling({ group: g, version: g.upgrade! })}>Upgrade to {g.upgrade.version}</button>}
        {owner && g.install && <>
          <button type="button" className="secondary" disabled={s.busy} onClick={() => void s.toggle(g.skillId, g.state === 'disabled')}>{g.state === 'installed' ? <><PauseCircle />Disable</> : <><PlayCircle />Enable</>}</button>
          {confirmRemove === g.skillId
            ? <span className="featureConfirm"><button type="button" className="danger" disabled={s.busy} onClick={() => uninstall(g.skillId)}>Uninstall</button><button type="button" className="secondary" onClick={() => setConfirmRemove(null)}>Cancel</button></span>
            : <button type="button" className="iconButton" aria-label={`Uninstall ${g.name}`} onClick={() => setConfirmRemove(g.skillId)}><Trash2 /></button>}
        </>}
      </div>
    </article>)}</div>
    {remaining > 0 && <div className="catalogMore"><button type="button" className="secondary" onClick={() => setPages(pages + 1)}>Show {Math.min(remaining, PAGE)} more</button></div>}
    {!s.loading && !s.error && !groups.length && <div className="emptyState"><Search /><h3>No published skills</h3>
      {admin ? <><p>Publish the first skill version; every workspace of the organization can then install it.</p><button type="button" onClick={() => setPublishing(true)}><Plus />Publish version</button></>
        : <p>An organization administrator publishes skills for every workspace. Ask one to publish the skill you need.</p>}</div>}
    {groups.length > 0 && !filtered.length && <div className="emptyState"><Search /><h3>No skills match</h3><p>Nothing matches “{query}” in this view.</p><button type="button" className="secondary" onClick={() => { setQuery(''); setFilter('all'); }}>Clear search</button></div>}
    {installing && <InstallDialog group={installing.group} version={installing.version} busy={s.busy} onCancel={() => setInstalling(null)} onConfirm={(tools) => void install(installing.group, installing.version, tools)} />}
    {details && !installing && <DetailDialog skillId={details} owner={owner} onClose={() => setDetails(null)} onChanged={() => void s.load()} onInstall={(v) => { const g = groups.find((x) => x.skillId === details); if (g) setInstalling({ group: g, version: v }); }} />}
    {publishing && <PublishDialog onClose={() => setPublishing(false)} onPublished={(text) => { setPublishing(false); void s.act(async () => text, ADMIN_ONLY); }} />}
  </>;
}
