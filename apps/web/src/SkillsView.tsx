import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Bot, CircleDot, Copy, History, Info, KeyRound, PackageCheck, PauseCircle, PenLine, PlayCircle, Plus, RefreshCw, Search, ShieldAlert, ShieldCheck, Sparkles, Trash2, Undo2, X } from 'lucide-react';
import * as api from './lib/api';
import { agentsApi, usageById, type AgentCatalogSkill, type SkillUsage, type SkillUsagePage } from './lib/agents';
import { describeError, isPermissionDenied } from './lib/errors';
import { approvalTone, emptyPublishDraft, filterBuiltins, filterSkills, groupSkills, historyLabel, pageOf, usageNames, publishBody, rollbackTargets, type PublishDraft, type SkillDetail, type SkillFilter, type SkillGroup, type SkillVersionLike } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { getGovernanceSummary } from './lib/governanceApi';
import { formatDateTime, t } from './lib/i18n';
import { riskLabel } from './lib/labels';
import { useIsOwner } from './useIsOwner';
import { figure } from './lib/pageSummary';
import { skillCounts } from './lib/catalog';
import { viewLabel } from './lib/shell';
import { PageHeader, PageSummary } from './PageParts';
import { PublisherKeysPanel } from './PublisherKeysPanel';
import { copyText } from './lib/clipboard';
import { flagged, installable, signatureBody, verificationLabel, verificationTone, type SkillVerification } from './lib/skillSigning';
import './features.css';
import './catalog.css';
import './adminPages.css';

const PAGE = 12;
const agentsCalls = agentsApi(api.apiRequest, api.apiRequestVoid);

/** Built-in skills and which agents load each skill (the agent catalog and usage, any member). */
function useSkillAgents() {
  const [builtins, setBuiltins] = useState<AgentCatalogSkill[]>([]);
  const [usage, setUsage] = useState<SkillUsagePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [catalog, page] = await Promise.all([agentsCalls.catalog(), agentsCalls.skillUsage()]);
      setBuiltins(catalog.skills.filter((skill) => skill.source === 'builtin')); setUsage(page); setError(null);
    } catch (e) { setError(t('skills.usageFailed', { error: describeError(e) })); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return { builtins, usage: useMemo(() => usageById(usage), [usage]), error, load };
}

/** "Loaded by Fleet desk and 2 more", or that no agent loads it yet. */
function UsedBy({ usage }: { usage: SkillUsage | undefined }) {
  if (!usage || usage.count === 0) return <p className="skillUsedBy none"><Bot aria-hidden="true" />{t('skills.usedByNone')}</p>;
  const { shown, more } = usageNames(usage.agents.map((agent) => agent.name));
  const names = shown.join(t('common.listSeparator'));
  return <p className="skillUsedBy"><span className="skillUsedAvatars" aria-hidden="true">{usage.agents.slice(0, 3).map((agent) => <span key={agent.id} style={{ background: agent.color }} />)}</span>
    {more ? t('skills.usedByMore', { names, count: more }) : t('skills.usedBy', { names })}</p>;
}

function AgentsUsing({ usage, onOpenAgents }: { usage: SkillUsage | undefined; onOpenAgents?: () => void }) {
  return <section aria-labelledby="skill-agents"><h4 id="skill-agents"><Bot size={16} aria-hidden="true" /> {t('skills.agentsTitle')}</h4>
    {!usage || usage.count === 0 ? <p className="featureMeta">{t('skills.agentsNone')}</p>
      : <ul className="skillAgentList">{usage.agents.map((agent) => <li key={agent.id}>
        <span className="skillAgentDot" style={{ background: agent.color }} aria-hidden="true" /><span dir="auto">{agent.name}</span>
        {agent.status === 'paused' && <span className="statusBadge warning">{t('skills.agentPaused')}</span>}
      </li>)}</ul>}
    {onOpenAgents && <button type="button" className="linkButton" onClick={onOpenAgents}>{t('skills.openAgents')}</button>}
  </section>;
}
/** Who signed a version and whether it verifies (docs/skills.md#signing). */
export function SignatureBadge({ v }: { v: SkillVerification | null | undefined }) {
  if (!v) return null;
  return <span className={`statusBadge ${verificationTone(v.status)}`} title={v.detail}>{v.status === 'verified' ? <ShieldCheck size={13} aria-hidden="true" /> : <ShieldAlert size={13} aria-hidden="true" />}{verificationLabel(v)}</span>;
}

/** Digest, signer and fingerprint of one version; organization administrators can attach a signature. */
export function SignaturePanel({ version, name, v, admin, onSign, onSigned }: { version: string; name: string; v: SkillVerification; admin: boolean; onSign(body: { key_id: string; signature: string }): Promise<unknown>; onSigned(text: string): void }) {
  const [open, setOpen] = useState(false);
  const [keyId, setKeyId] = useState(''), [signature, setSignature] = useState('');
  const [error, setError] = useState<string | null>(null), [copied, setCopied] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const body = signatureBody(keyId, signature);
    if (!body.ok) { setError(body.error); return; }
    setBusy(true); setError(null);
    try { await onSign(body.value); setOpen(false); onSigned(t('signing.signedNote', { version })); }
    catch (e) { setError(isPermissionDenied(e) ? t('signing.notAllowed', { error: describeError(e) }) : describeError(e)); } finally { setBusy(false); }
  };
  return <div className="skillSignature">
    <small dir="auto">{v.detail}</small>
    {v.key_id && <small className="keyMono" dir="ltr">{t('signing.signer', { signer: v.signer ?? '-', key: v.key_id, fingerprint: v.fingerprint ?? '-' })}</small>}
    <small className="keyMono skillDigest" dir="ltr"><span>{t('signing.digest')}: </span><code>{v.digest}</code>
      <button type="button" className="iconButton" aria-label={t('signing.copyDigest')} onClick={() => void copyText(v.digest).then((ok) => setCopied(ok ? t('signing.copied') : t('signing.copyFailed')))}><Copy size={14} aria-hidden="true" /></button></small>
    {copied && <small role="status">{copied}</small>}
    {admin && !v.installable && !open && <button type="button" className="secondary" onClick={() => setOpen(true)}><PenLine size={15} aria-hidden="true" />{t('signing.sign')}</button>}
    {open && <form className="catalogForm skillSignForm" aria-label={t('signing.signTitle', { name, version })} onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <p className="featureMeta catalogFormWide">{t('signing.signHelp')}</p>
      <label className="field"><span>{t('signing.field.keyId')}</span><input dir="ltr" value={keyId} onChange={(e) => setKeyId(e.target.value)} autoComplete="off" /></label>
      <label className="field catalogFormWide"><span>{t('signing.field.signature')}</span><textarea dir="ltr" className="keyMono" rows={2} value={signature} spellCheck={false} onChange={(e) => setSignature(e.target.value)} /></label>
      {error && <p className="notice errorNotice catalogFormWide" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
      <div className="actions catalogFormWide"><button type="button" className="secondary" onClick={() => setOpen(false)}>{t('common.cancel')}</button><button type="submit" disabled={busy}><KeyRound aria-hidden="true" />{t('signing.signButton')}</button></div>
    </form>}
  </div>;
}

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
  const toggle = (skillId: string, enabled: boolean, acknowledgeFlagged = false) => act(async () => { await api.setSkillEnabled(skillId, enabled, acknowledgeFlagged); return enabled ? t('skills.enabled') : t('skills.disabled'); });
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
  const signed = installable(version);
  return <Dialog title={t('skills.installTitle', { name: version.name, version: version.version })} onClose={onCancel}>
    <p dir="auto">{version.description}</p>
    {version.verification && <p className="skillSignatureLine"><SignatureBadge v={version.verification} />{version.verification.fingerprint && <small className="keyMono" dir="ltr">{version.verification.fingerprint}</small>}</p>}
    {!signed && version.verification && <p className="notice errorNotice" role="alert"><ShieldAlert aria-hidden="true" />{t('signing.installBlocked', { detail: version.verification.detail })}</p>}
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
    <div className="actions"><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button><button type="button" autoFocus disabled={busy || !signed} onClick={() => onConfirm(tools)}><PackageCheck aria-hidden="true" />{t('skills.installGrant')}</button></div>
  </Dialog>;
}

function DetailDialog({ skillId, owner, usage, onOpenAgents, onInstall, onChanged, onClose }: { skillId: string; owner: boolean | null; usage: SkillUsage | undefined; onOpenAgents?: () => void; onInstall(version: SkillVersionLike): void; onChanged(): void; onClose(): void }) {
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
  const unsignedOlder = detail ? rollbackTargets(detail.versions.map((v) => ({ version: v.version })), detail.installation?.version).length > targets.length : false;
  const signedNote = (text: string) => { setFlash({ text }); void load(); onChanged(); };
  return <Dialog title={detail ? detail.name : t('skills.detailsTitle')} onClose={onClose} wide>
    {!detail && !error && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('skills.detailsLoading')}</p>}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('skills.detailsFailed', { error })}</p>}
    {detail && <>
      <p dir="auto">{detail.description}</p>
      <p className="featureMeta"><code dir="ltr">{detail.skill_id}</code> · {detail.installation
        ? t('skills.metaInstalled', { latest: detail.latest_version, version: detail.installation.version, state: detail.installation.enabled ? t('skills.state.installed') : t('skills.state.disabled') })
        : t('skills.metaNotInstalled', { latest: detail.latest_version })}</p>
      {detail.installation && flagged(detail.installation) && <p className="notice errorNotice" role="alert"><ShieldAlert aria-hidden="true" />{t('signing.flagged', { detail: detail.installation.verification!.detail })}</p>}
      <section aria-labelledby="skill-tools"><h4 id="skill-tools">{t('skills.toolsIn', { version: detail.tools_version })}</h4>
        {detail.tools.length === 0 ? <p className="featureMeta">{t('skills.versionNoTools')}</p> :
          <ul className="catalogToolList">{detail.tools.map((tool) => <li key={tool.name}><code dir="ltr">{tool.name}</code><span className={`statusBadge ${approvalTone(tool.approval)}`}>{tool.risk_level ? riskLabel(tool.risk_level) : t('skills.unavailable')}</span>{detail.installation && <span className={`statusBadge ${tool.granted ? 'success' : 'neutral'}`}>{tool.granted ? t('skills.granted') : t('skills.notGranted')}</span>}<small dir="auto">{tool.description ? `${tool.description} ` : ''}{tool.approval_text}{tool.target ? ` ${t('skills.contacts', { host: tool.target })}` : ''}</small></li>)}</ul>}
      </section>
      <AgentsUsing usage={usage} onOpenAgents={onOpenAgents} />
      <section aria-labelledby="skill-versions"><h4 id="skill-versions">{t('skills.versionsTitle')}</h4>
        <ul className="catalogVersionList">{detail.versions.map((v) => {
          const installed = detail.installation?.version === v.version;
          return <li key={v.id}><div><strong>{v.version}</strong>{installed && <span className="statusBadge success">{t('skills.state.installed')}</span>}<SignatureBadge v={v.verification} /><small>{t('skills.publishedBy', { time: when(v.created_at), user: v.created_by, risk: riskLabel(v.risk_level), tools: t('skills.tools', { count: v.required_tools.length }) })}</small>
            {v.verification && <SignaturePanel version={v.version} name={detail.name} v={v.verification} admin={detail.can_rollback} onSign={(body) => catalog.signSkillVersion(detail.skill_id, v.version, body)} onSigned={signedNote} />}</div>
            <div className="catalogVersionActions">
              {!installed && owner && installable(v) && (!detail.installation || !targets.includes(v.version)) && <button type="button" className="secondary" onClick={() => onInstall(v)}>{detail.installation ? t('skills.installVersion') : t('skills.install')}</button>}
              {detail.can_rollback && targets.includes(v.version) && (confirm === v.version
                ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={() => void rollback(v.version)}>{t('skills.confirmRollback')}</button><button type="button" className="secondary" onClick={() => setConfirm(null)}>{t('common.cancel')}</button></span>
                : <button type="button" className="secondary" onClick={() => setConfirm(v.version)}><Undo2 size={15} aria-hidden="true" />{t('skills.rollbackTo', { version: v.version })}</button>)}
            </div></li>;
        })}</ul>
        {detail.installation && targets.length > 0 && !detail.can_rollback && <p className="featureMeta">{adminsOnly()}</p>}
        {detail.can_rollback && unsignedOlder && <p className="featureMeta">{t('signing.rollbackSignedOnly')}</p>}
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

export function SkillsView({ onOpenAgents }: { onOpenAgents?: () => void } = {}) {
  const s = useSkills();
  const agentsOf = useSkillAgents();
  const [confirmDisable, setConfirmDisable] = useState<string | null>(null);
  const owner = useIsOwner();
  const admin = useOrgAdmin();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<SkillFilter>('all');
  const [pages, setPages] = useState(1);
  const [installing, setInstalling] = useState<{ group: SkillGroup; version: SkillVersionLike } | null>(null);
  const [details, setDetails] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [tab, setTab] = useState<'skills' | 'keys'>('skills');
  const groups = useMemo(() => groupSkills(s.versions, s.installs), [s.versions, s.installs]);
  const filtered = filterSkills(groups, query, filter);
  const { shown, remaining } = pageOf(filtered, pages, PAGE);
  const builtins = filterBuiltins(agentsOf.builtins, query, filter);
  const affects = (skillId: string) => agentsOf.usage.get(skillId)?.count ?? 0;
  const toggle = (g: SkillGroup, acknowledgeFlagged = false) => { setConfirmDisable(null); void s.toggle(g.skillId, g.state === 'disabled', acknowledgeFlagged).then(() => agentsOf.load()); };
  useEffect(() => setPages(1), [query, filter]);
  const install = async (group: SkillGroup, version: SkillVersionLike, tools: string[]) => {
    if (await s.act(async () => { await api.installSkill(group.skillId, version.version, tools); return t('skills.installed', { name: version.name, version: version.version }); })) { setInstalling(null); setDetails(null); }
  };
  const uninstall = (skillId: string) => void s.act(async () => { await api.uninstallSkill(skillId); setConfirmRemove(null); return t('skills.uninstalled'); }).then(() => agentsOf.load());
  const stateLabel = (g: SkillGroup) => g.state === 'available' ? t('skills.state.available') : g.state === 'installed' ? t('skills.state.installed') : t('skills.state.disabled');
  const counts = skillCounts(groups, agentsOf.builtins.length);
  const skillsLoad = { state: s.error && !groups.length ? 'error' as const : s.loading && !groups.length ? 'loading' as const : 'ready' as const };
  const figures = [
    figure('skills', t('pages2.skills.fig.skills'), skillsLoad, () => ({ value: String(counts.total), hint: t('pages2.skills.fig.builtin', { count: counts.builtin }) })),
    figure('installed', t('pages2.skills.fig.installed'), skillsLoad, () => ({ value: String(counts.installed), hint: counts.disabled ? t('pages2.skills.fig.disabled', { count: counts.disabled }) : t('pages2.skills.fig.noneDisabled'), tone: counts.installed ? 'ok' : '' })),
    figure('upgrades', t('pages2.skills.fig.upgrades'), skillsLoad, () => ({ value: String(counts.upgrades), hint: counts.upgrades ? t('pages2.skills.fig.upgradesHint') : t('pages2.skills.fig.upToDate'), tone: counts.upgrades ? 'warn' : '' })),
    figure('available', t('pages2.skills.fig.available'), skillsLoad, () => ({ value: String(counts.available), hint: t('pages2.skills.fig.availableHint') })),
  ];
  return <>
    <PageHeader title={viewLabel('skills')} subtitle={t('skills.intro')} actions={<>{admin && <button type="button" onClick={() => setPublishing(true)}><Plus aria-hidden="true" />{t('skills.publish')}</button>}<button type="button" className="secondary" onClick={() => { void s.load(); void agentsOf.load(); }} disabled={s.loading}><RefreshCw aria-hidden="true" />{t('skills.refresh')}</button></>} />
    <div className="catalogSegments skillTabs" role="tablist" aria-label={t('signing.tabs.label')}>
      {(['skills', 'keys'] as const).map((id) => <button key={id} type="button" role="tab" id={`skills-tab-${id}`} aria-controls={`skills-panel-${id}`} aria-selected={tab === id} className={tab === id ? 'active' : 'secondary'}
        onClick={() => setTab(id)} onKeyDown={(e) => { if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { const next = id === 'skills' ? 'keys' : 'skills'; setTab(next); document.getElementById(`skills-tab-${next}`)?.focus(); } }}
        tabIndex={tab === id ? 0 : -1}>{id === 'keys' && <KeyRound size={15} aria-hidden="true" />}{t(`signing.tabs.${id}`)}</button>)}
    </div>
    {tab === 'keys' && <div role="tabpanel" id="skills-panel-keys" aria-labelledby="skills-tab-keys"><PublisherKeysPanel onChanged={() => void s.load()} /></div>}
    {tab === 'skills' && <div role="tabpanel" id="skills-panel-skills" aria-labelledby="skills-tab-skills" className="skillPanel">
    <PageSummary label={t('pages2.skills.summary')} figures={figures} onRetry={() => { void s.load(); void agentsOf.load(); }} />
    <Feedback error={s.error} note={s.note} loading={s.loading && !groups.length} label={t('skills.loadingLabel')} />
    {agentsOf.error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{agentsOf.error}</p>}
    {owner === false && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('skills.ownersOnlyAsk')}</p>}
    {(groups.length > 0 || agentsOf.builtins.length > 0) && <div className="surface catalogToolbar" role="search">
      <label className="field"><span>{t('skills.search')}</span><input type="search" value={query} placeholder={t('skills.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} /></label>
      <fieldset className="catalogSegments"><legend>{t('skills.show')}</legend>{(['all', 'installed', 'available', 'builtin'] as const).map((f) => <label key={f}><input type="radio" name="skill-filter" checked={filter === f} onChange={() => setFilter(f)} />{t(`skills.filter.${f}`)}</label>)}</fieldset>
      <p className="featureMeta" aria-live="polite">{t('skills.count', { shown: filtered.length + builtins.length, count: groups.length + agentsOf.builtins.length })}</p>
    </div>}
    {shown.length > 0 && <div className="skillRows">{shown.map((g) => {
      const toolsOf = (g.install ? g.versions.find((v) => v.version === g.install!.version) ?? g.latest : g.latest).required_tools;
      const versions = t('skills.versions', { count: g.versions.length });
      const shownVersion = g.install ? g.versions.find((v) => v.version === g.install!.version) ?? g.latest : g.latest;
      const next = !g.install ? g.latest : g.upgrade;
      return <article className="skillRow catalogCard" key={g.skillId} id={`skill-${g.skillId.replace(/[^A-Za-z0-9_-]/g, '-')}`} aria-labelledby={`skill-title-${g.skillId}`}>
        <div className="integrationIcon" aria-hidden="true"><PackageCheck /></div>
        <div>
          <h3 id={`skill-title-${g.skillId}`}>{g.name} <small>{g.install ? g.install.version : g.latest.version}</small></h3>
          <p dir="auto">{g.description}</p>
          <div className="tagRow">{toolsOf.map((x) => <span key={x} dir="ltr">{x}</span>)}</div>
          <small>{g.upgrade ? t('skills.cardMetaUpgrade', { versions, risk: riskLabel(g.latest.risk_level), version: g.upgrade.version }) : t('skills.cardMeta', { versions, risk: riskLabel(g.latest.risk_level) })}</small>
          <UsedBy usage={agentsOf.usage.get(g.skillId)} />
          {g.install?.disabled_reason && !g.install.enabled && <p className="featureMeta skillFlag" dir="auto">{g.install.disabled_reason}</p>}
          {flagged(g.install) && <p className="notice errorNotice skillFlag" role="alert"><ShieldAlert aria-hidden="true" />{t('signing.flagged', { detail: g.install!.verification!.detail })}</p>}
          {owner && next && !installable(next) && next.verification && <p className="featureMeta skillFlag">{t('signing.cannotInstall', { version: next.version, detail: next.verification.detail })}</p>}
        </div>
        <div className="featureSkillActions">
          <SignatureBadge v={g.install?.verification ?? shownVersion.verification} />
          <span className={`statusBadge ${g.state === 'installed' ? 'success' : g.state === 'disabled' ? 'warning' : 'neutral'}`}>{stateLabel(g)}</span>
          <button type="button" className="secondary" onClick={() => setDetails(g.skillId)}><Info size={15} aria-hidden="true" />{t('skills.details')}</button>
          {owner && !g.install && <button type="button" disabled={s.busy || !installable(g.latest)} onClick={() => setInstalling({ group: g, version: g.latest })}>{t('skills.install')}</button>}
          {owner && g.upgrade && <button type="button" className="secondary" disabled={s.busy || !installable(g.upgrade)} onClick={() => setInstalling({ group: g, version: g.upgrade! })}>{t('skills.upgrade', { version: g.upgrade.version })}</button>}
          {owner && g.install && <>
            {confirmDisable === `enable-${g.skillId}` && <span className="featureConfirm"><span className="skillAffects">{t('signing.enableFlaggedConfirm')}</span><button type="button" className="danger" disabled={s.busy} onClick={() => toggle(g, true)}>{t('signing.enableAnyway')}</button><button type="button" className="secondary" onClick={() => setConfirmDisable(null)}>{t('common.cancel')}</button></span>}
            {confirmDisable === `enable-${g.skillId}` ? null : confirmDisable === g.skillId
              ? <span className="featureConfirm"><span className="skillAffects">{t('skills.affectsAgents', { count: affects(g.skillId) })}</span><button type="button" className="danger" disabled={s.busy} onClick={() => toggle(g)}>{t('skills.confirmDisable')}</button><button type="button" className="secondary" onClick={() => setConfirmDisable(null)}>{t('common.cancel')}</button></span>
              : <button type="button" className="secondary" disabled={s.busy} onClick={() => g.state === 'installed' && affects(g.skillId) > 0 ? setConfirmDisable(g.skillId) : g.state === 'disabled' && flagged(g.install) ? setConfirmDisable(`enable-${g.skillId}`) : toggle(g)}>{g.state === 'installed' ? <><PauseCircle aria-hidden="true" />{t('skills.disable')}</> : <><PlayCircle aria-hidden="true" />{t('skills.enable')}</>}</button>}
            {confirmRemove === g.skillId
              ? <span className="featureConfirm">{affects(g.skillId) > 0 && <span className="skillAffects">{t('skills.affectsAgents', { count: affects(g.skillId) })}</span>}<button type="button" className="danger" disabled={s.busy} onClick={() => uninstall(g.skillId)}>{t('skills.uninstall')}</button><button type="button" className="secondary" onClick={() => setConfirmRemove(null)}>{t('common.cancel')}</button></span>
              : <button type="button" className="iconButton" aria-label={t('skills.uninstallNamed', { name: g.name })} onClick={() => setConfirmRemove(g.skillId)}><Trash2 aria-hidden="true" /></button>}
          </>}
        </div>
      </article>;
    })}</div>}
    {builtins.length > 0 && <section className="skillBuiltins" aria-labelledby="skills-builtin-h">
      <div className="skillBuiltinsHead"><h3 id="skills-builtin-h">{t('skills.builtinTitle')}</h3><p className="featureMeta">{t('skills.builtinNote')}</p></div>
      <ul className="skillBuiltinGrid">{builtins.map((b) => <li key={b.id} className="surface skillBuiltin">
        <span className="skillBuiltinIcon" aria-hidden="true"><Sparkles /></span>
        <div><h4 dir="auto">{b.name} <span className="statusBadge neutral">{t('skills.state.builtin')}</span></h4>
          <p dir="auto">{b.description}</p>
          <div className="tagRow">{b.required_tools.map((x) => <span key={x} dir="ltr">{x}</span>)}</div>
          <UsedBy usage={agentsOf.usage.get(b.id)} /></div>
      </li>)}</ul>
    </section>}
    {remaining > 0 && <div className="catalogMore"><button type="button" className="secondary" onClick={() => setPages(pages + 1)}>{t('skills.showMore', { count: Math.min(remaining, PAGE) })}</button></div>}
    {!s.loading && !s.error && !groups.length && filter !== 'builtin' && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('skills.emptyTitle')}</h3>
      {admin ? <><p>{t('skills.emptyAdmin')}</p><button type="button" onClick={() => setPublishing(true)}><Plus aria-hidden="true" />{t('skills.publish')}</button></>
        : <p>{t('skills.emptyOther')}</p>}</div>}
    {(groups.length > 0 || filter === 'builtin') && !filtered.length && !builtins.length && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('skills.noMatchTitle')}</h3><p>{t('skills.noMatchText', { query })}</p><button type="button" className="secondary" onClick={() => { setQuery(''); setFilter('all'); }}>{t('skills.clearSearch')}</button></div>}
    {installing && <InstallDialog group={installing.group} version={installing.version} busy={s.busy} onCancel={() => setInstalling(null)} onConfirm={(tools) => void install(installing.group, installing.version, tools)} />}
    {details && !installing && <DetailDialog skillId={details} owner={owner} usage={agentsOf.usage.get(details)} onOpenAgents={onOpenAgents} onClose={() => setDetails(null)} onChanged={() => void s.load()} onInstall={(v) => { const g = groups.find((x) => x.skillId === details); if (g) setInstalling({ group: g, version: v }); }} />}
    </div>}
    {publishing && <PublishDialog onClose={() => setPublishing(false)} onPublished={(text) => { setPublishing(false); void s.act(async () => text, adminsOnly); }} />}
  </>;
}
