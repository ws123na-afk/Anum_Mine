import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { CircleDot, History, Info, PackageCheck, PauseCircle, PlayCircle, Plus, RefreshCw, Search, ShieldAlert, Trash2, Undo2, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { approvalTone, emptyPublishDraft, filterSkills, groupSkills, historyLabel, pageOf, publishBody, rollbackTargets, type PublishDraft, type SkillDetail, type SkillFilter, type SkillGroup, type SkillVersionLike } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { getGovernanceSummary } from './lib/governanceApi';
import { formatDateTime, t } from './lib/i18n';
import { riskLabel } from './lib/labels';
import { useIsOwner } from './useIsOwner';
import './features.css';
import './catalog.css';

const PAGE = 12;
const when = (iso: string) => formatDateTime(iso);
const failure = (e: unknown, who: string) => isPermissionDenied(e) ? t('skills.notAllowed', { error: describeError(e), who }) : describeError(e);
const ownersOnly = () => t('skills.ownersOnly');
const adminsOnly = () => t('skills.adminsOnly');

/** Published versions and this workspace's installations, shared by the Skills and Agents views. */
export function useSkills() {
  const [versions, setVersions] = useState<api.SkillVersion[]>([]);
  const [installs, setInstalls] = useState<api.SkillInstallation[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try { const [v, i] = await Promise.all([api.getSkillVersions(), api.getSkillInstallations()]); setVersions(v); setInstalls(i); setError(null); }
    catch (e) { setError(t('skills.loadFailed', { error: describeError(e) })); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const act = useCallback(async (fn: () => Promise<string>, who: () => string = ownersOnly) => {
    setBusy(true); setError(null); setNote(null);
    try { setNote(await fn()); await load(); return true; } catch (e) { setError(failure(e, who())); return false; } finally { setBusy(false); }
  }, [load]);
  const toggle = (skillId: string, enabled: boolean) => act(async () => { await api.setSkillEnabled(skillId, enabled); return enabled ? t('skills.enabled') : t('skills.disabled'); });
  return { versions, installs, loading, busy, error, note, load, act, toggle };
}

export function Feedback({ error, note, loading, label }: { error: string | null; note: string | null; loading: boolean; label: string }) {
  return <>
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {note && <p className="notice" role="status"><CircleDot aria-hidden="true" />{note}</p>}
    {loading && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('skills.loading', { label })}</p>}
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
  const id = useRef(`dialog-${Math.random().toString(36).slice(2)}`).current;
  return <div className="featureBackdrop" onClick={onClose}>
    <section ref={ref} className={`surface featureDialog ${wide ? 'catalogDialogWide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={id} onClick={(e) => e.stopPropagation()}>
      <header><h3 id={id}>{title}</h3><button type="button" className="iconButton" aria-label={t('skills.close')} onClick={onClose}><X aria-hidden="true" /></button></header>
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
  const high = ['high', 'blocked'].includes(version.risk_level);
  return <Dialog title={t('skills.installTitle', { name: version.name, version: version.version })} onClose={onCancel}>
    <p dir="auto">{version.description}</p>
    {replacing && <p className="featureMeta">{t('skills.replaces', { version: replacing })}</p>}
    <p className={`featureChip ${high ? 'failed' : 'indexed'}`}>{high && <ShieldAlert aria-hidden="true" />}{t('skills.skillRisk', { risk: riskLabel(version.risk_level) })}</p>
    {tools.length ? <>
      <h4>{t('skills.toolsGranted')}</h4>
      {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('skills.toolsFailed', { error })}</p>}
      <ul className="catalogToolList">{tools.map((name) => {
        const tool = detail?.tools.find((x) => x.name === name);
        return <li key={name}><code dir="ltr">{name}</code>{tool ? <><span className={`statusBadge ${approvalTone(tool.approval)}`}>{tool.risk_level ? riskLabel(tool.risk_level) : t('skills.unavailable')}</span><small dir="auto">{tool.approval_text}</small></> : !error && <small>{t('skills.loadingRisk')}</small>}</li>;
      })}</ul>
    </> : <p className="featureMeta">{t('skills.noTools')}</p>}
    <p className="featureMeta">{t('skills.installNote')}</p>
    <div className="actions"><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button><button type="button" autoFocus disabled={busy} onClick={() => onConfirm(tools)}><PackageCheck aria-hidden="true" />{t('skills.installGrant')}</button></div>
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
    try { await catalog.rollbackSkill(skillId, version); setFlash({ text: t('skills.rolledBack', { version }) }); setConfirm(null); await load(); onChanged(); }
    catch (e) { setFlash({ text: failure(e, adminsOnly()), error: true }); } finally { setBusy(false); }
  };
  const targets = detail ? rollbackTargets(detail.versions, detail.installation?.version) : [];
  return <Dialog title={detail ? detail.name : t('skills.detailsTitle')} onClose={onClose} wide>
    {!detail && !error && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('skills.detailsLoading')}</p>}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('skills.detailsFailed', { error })}</p>}
    {detail && <>
      <p dir="auto">{detail.description}</p>
      <p className="featureMeta"><code dir="ltr">{detail.skill_id}</code> · {detail.installation
        ? t('skills.metaInstalled', { latest: detail.latest_version, version: detail.installation.version, state: detail.installation.enabled ? t('skills.state.installed') : t('skills.state.disabled') })
        : t('skills.metaNotInstalled', { latest: detail.latest_version })}</p>
      <section aria-labelledby="skill-tools"><h4 id="skill-tools">{t('skills.toolsIn', { version: detail.tools_version })}</h4>
        {detail.tools.length === 0 ? <p className="featureMeta">{t('skills.versionNoTools')}</p> :
          <ul className="catalogToolList">{detail.tools.map((tool) => <li key={tool.name}><code dir="ltr">{tool.name}</code><span className={`statusBadge ${approvalTone(tool.approval)}`}>{tool.risk_level ? riskLabel(tool.risk_level) : t('skills.unavailable')}</span>{detail.installation && <span className={`statusBadge ${tool.granted ? 'success' : 'neutral'}`}>{tool.granted ? t('skills.granted') : t('skills.notGranted')}</span>}<small dir="auto">{tool.description ? `${tool.description} ` : ''}{tool.approval_text}{tool.target ? ` ${t('skills.contacts', { host: tool.target })}` : ''}</small></li>)}</ul>}
      </section>
      <section aria-labelledby="skill-versions"><h4 id="skill-versions">{t('skills.versionsTitle')}</h4>
        <ul className="catalogVersionList">{detail.versions.map((v) => {
          const installed = detail.installation?.version === v.version;
          return <li key={v.id}><div><strong>{v.version}</strong>{installed && <span className="statusBadge success">{t('skills.state.installed')}</span>}<small>{t('skills.publishedBy', { time: when(v.created_at), user: v.created_by, risk: riskLabel(v.risk_level), tools: t('skills.tools', { count: v.required_tools.length }) })}</small></div>
            <div className="catalogVersionActions">
              {!installed && owner && (!detail.installation || !targets.includes(v.version)) && <button type="button" className="secondary" onClick={() => onInstall(v)}>{detail.installation ? t('skills.installVersion') : t('skills.install')}</button>}
              {detail.can_rollback && targets.includes(v.version) && (confirm === v.version
                ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={() => void rollback(v.version)}>{t('skills.confirmRollback')}</button><button type="button" className="secondary" onClick={() => setConfirm(null)}>{t('common.cancel')}</button></span>
                : <button type="button" className="secondary" onClick={() => setConfirm(v.version)}><Undo2 size={15} aria-hidden="true" />{t('skills.rollbackTo', { version: v.version })}</button>)}
            </div></li>;
        })}</ul>
        {detail.installation && targets.length > 0 && !detail.can_rollback && <p className="featureMeta">{adminsOnly()}</p>}
      </section>
      {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
      <section aria-labelledby="skill-history"><h4 id="skill-history"><History size={16} aria-hidden="true" /> {t('skills.historyTitle')}</h4>
        {detail.history.length === 0 ? <p className="featureMeta">{t('skills.historyEmpty')}</p> :
          <ol className="catalogHistory">{detail.history.map((h) => <li key={h.id}><span>{historyLabel(h)}</span><small>{t('skills.historyBy', { user: h.actor, time: when(h.created_at) })}</small></li>)}</ol>}
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
    try { const v = await catalog.publishSkillVersion(body.value); onPublished(t('skills.published', { name: v.name, version: v.version })); }
    catch (e) { setError(failure(e, adminsOnly())); } finally { setBusy(false); }
  };
  return <Dialog title={t('skills.publishTitle')} onClose={onClose} wide>
    <form className="catalogForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <label className="field"><span>{t('skills.field.id')}</span><input dir="ltr" value={draft.skillId} onChange={set('skillId')} placeholder="acme.release-notes" /></label>
      <label className="field"><span>{t('skills.field.version')}</span><input dir="ltr" value={draft.version} onChange={set('version')} /></label>
      <label className="field"><span>{t('skills.field.name')}</span><input dir="auto" value={draft.name} maxLength={160} onChange={set('name')} /></label>
      <label className="field"><span>{t('skills.field.risk')}</span><select value={draft.risk} onChange={set('risk')}>{['low', 'medium', 'high', 'blocked'].map((r) => <option key={r} value={r}>{riskLabel(r)}</option>)}</select></label>
      <label className="field catalogFormWide"><span>{t('skills.field.description')}</span><input dir="auto" value={draft.description} maxLength={2000} onChange={set('description')} /></label>
      <label className="field catalogFormWide"><span>{t('skills.field.tools')}</span><input dir="ltr" value={draft.tools} onChange={set('tools')} /></label>
      <label className="field catalogFormWide"><span>{t('skills.field.instructions')}</span><textarea dir="auto" rows={5} value={draft.instructions} maxLength={50000} onChange={set('instructions')} /></label>
      <p className="featureMeta catalogFormWide">{t('skills.publishNote')}</p>
      {error && <p className="notice errorNotice catalogFormWide" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
      <div className="actions catalogFormWide"><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button><button type="submit" disabled={busy}><Plus aria-hidden="true" />{t('skills.publishButton')}</button></div>
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
    if (await s.act(async () => { await api.installSkill(group.skillId, version.version, tools); return t('skills.installed', { name: version.name, version: version.version }); })) { setInstalling(null); setDetails(null); }
  };
  const uninstall = (skillId: string) => void s.act(async () => { await api.uninstallSkill(skillId); setConfirmRemove(null); return t('skills.uninstalled'); });
  const stateLabel = (g: SkillGroup) => g.state === 'available' ? t('skills.state.available') : g.state === 'installed' ? t('skills.state.installed') : t('skills.state.disabled');
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">{t('skills.eyebrow')}</p><h2>{t('skills.title')}</h2><p>{t('skills.intro')}</p></div>
      <div className="actions">{admin && <button type="button" onClick={() => setPublishing(true)}><Plus aria-hidden="true" />{t('skills.publish')}</button>}<button type="button" className="secondary" onClick={() => void s.load()} disabled={s.loading}><RefreshCw aria-hidden="true" />{t('skills.refresh')}</button></div></section>
    <Feedback error={s.error} note={s.note} loading={s.loading && !groups.length} label={t('skills.loadingLabel')} />
    {owner === false && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('skills.ownersOnlyAsk')}</p>}
    {groups.length > 0 && <div className="surface catalogToolbar" role="search">
      <label className="field"><span>{t('skills.search')}</span><input type="search" value={query} placeholder={t('skills.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} /></label>
      <fieldset className="catalogSegments"><legend>{t('skills.show')}</legend>{(['all', 'installed', 'available'] as const).map((f) => <label key={f}><input type="radio" name="skill-filter" checked={filter === f} onChange={() => setFilter(f)} />{t(`skills.filter.${f}`)}</label>)}</fieldset>
      <p className="featureMeta" aria-live="polite">{t('skills.count', { shown: filtered.length, count: groups.length })}</p>
    </div>}
    <div className="integrationGrid">{shown.map((g) => {
      const toolsOf = (g.install ? g.versions.find((v) => v.version === g.install!.version) ?? g.latest : g.latest).required_tools;
      const versions = t('skills.versions', { count: g.versions.length });
      return <article className="surface integration catalogCard" key={g.skillId} id={`skill-${g.skillId.replace(/[^A-Za-z0-9_-]/g, '-')}`} aria-labelledby={`skill-title-${g.skillId}`}>
        <div className="integrationIcon" aria-hidden="true"><PackageCheck /></div>
        <div>
          <h3 id={`skill-title-${g.skillId}`}>{g.name} <small>{g.install ? g.install.version : g.latest.version}</small></h3>
          <p dir="auto">{g.description}</p>
          <div className="tagRow">{toolsOf.map((x) => <span key={x} dir="ltr">{x}</span>)}</div>
          <small>{g.upgrade ? t('skills.cardMetaUpgrade', { versions, risk: riskLabel(g.latest.risk_level), version: g.upgrade.version }) : t('skills.cardMeta', { versions, risk: riskLabel(g.latest.risk_level) })}</small>
        </div>
        <div className="featureSkillActions">
          <span className={`statusBadge ${g.state === 'installed' ? 'success' : g.state === 'disabled' ? 'warning' : 'neutral'}`}>{stateLabel(g)}</span>
          <button type="button" className="secondary" onClick={() => setDetails(g.skillId)}><Info size={15} aria-hidden="true" />{t('skills.details')}</button>
          {owner && !g.install && <button type="button" disabled={s.busy} onClick={() => setInstalling({ group: g, version: g.latest })}>{t('skills.install')}</button>}
          {owner && g.upgrade && <button type="button" className="secondary" disabled={s.busy} onClick={() => setInstalling({ group: g, version: g.upgrade! })}>{t('skills.upgrade', { version: g.upgrade.version })}</button>}
          {owner && g.install && <>
            <button type="button" className="secondary" disabled={s.busy} onClick={() => void s.toggle(g.skillId, g.state === 'disabled')}>{g.state === 'installed' ? <><PauseCircle aria-hidden="true" />{t('skills.disable')}</> : <><PlayCircle aria-hidden="true" />{t('skills.enable')}</>}</button>
            {confirmRemove === g.skillId
              ? <span className="featureConfirm"><button type="button" className="danger" disabled={s.busy} onClick={() => uninstall(g.skillId)}>{t('skills.uninstall')}</button><button type="button" className="secondary" onClick={() => setConfirmRemove(null)}>{t('common.cancel')}</button></span>
              : <button type="button" className="iconButton" aria-label={t('skills.uninstallNamed', { name: g.name })} onClick={() => setConfirmRemove(g.skillId)}><Trash2 aria-hidden="true" /></button>}
          </>}
        </div>
      </article>;
    })}</div>
    {remaining > 0 && <div className="catalogMore"><button type="button" className="secondary" onClick={() => setPages(pages + 1)}>{t('skills.showMore', { count: Math.min(remaining, PAGE) })}</button></div>}
    {!s.loading && !s.error && !groups.length && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('skills.emptyTitle')}</h3>
      {admin ? <><p>{t('skills.emptyAdmin')}</p><button type="button" onClick={() => setPublishing(true)}><Plus aria-hidden="true" />{t('skills.publish')}</button></>
        : <p>{t('skills.emptyOther')}</p>}</div>}
    {groups.length > 0 && !filtered.length && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('skills.noMatchTitle')}</h3><p>{t('skills.noMatchText', { query })}</p><button type="button" className="secondary" onClick={() => { setQuery(''); setFilter('all'); }}>{t('skills.clearSearch')}</button></div>}
    {installing && <InstallDialog group={installing.group} version={installing.version} busy={s.busy} onCancel={() => setInstalling(null)} onConfirm={(tools) => void install(installing.group, installing.version, tools)} />}
    {details && !installing && <DetailDialog skillId={details} owner={owner} onClose={() => setDetails(null)} onChanged={() => void s.load()} onInstall={(v) => { const g = groups.find((x) => x.skillId === details); if (g) setInstalling({ group: g, version: v }); }} />}
    {publishing && <PublishDialog onClose={() => setPublishing(false)} onPublished={(text) => { setPublishing(false); void s.act(async () => text, adminsOnly); }} />}
  </>;
}
