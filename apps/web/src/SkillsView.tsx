import { useCallback, useEffect, useState } from 'react';
import { CircleDot, PackageCheck, PauseCircle, PlayCircle, RefreshCw, Search, ShieldAlert, Trash2, X } from 'lucide-react';
import * as api from './lib/api';
import { installReview, skillAnchor, skillInstallState, type InstallReview } from './lib/capabilities';
import { describeError } from './lib/errors';
import './features.css';

/** Published versions and this workspace's installations, shared by the Skills and Agents views. */
export function useSkills() {
  const [versions, setVersions] = useState<api.SkillVersion[]>([]);
  const [installs, setInstalls] = useState<api.SkillInstallation[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try { const [v, i] = await Promise.all([api.getSkillVersions(), api.getSkillInstallations()]); setVersions(v); setInstalls(i); setError(null); }
    catch (e) { setError(describeError(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const act = useCallback(async (fn: () => Promise<string>) => {
    setBusy(true); setError(null); setNote(null);
    try { setNote(await fn()); await load(); return true; } catch (e) { setError(describeError(e)); return false; } finally { setBusy(false); }
  }, [load]);
  const toggle = (skillId: string, enabled: boolean) => act(async () => { await api.setSkillEnabled(skillId, enabled); return enabled ? 'Skill enabled.' : 'Skill disabled.'; });
  return { versions, installs, loading, busy, error, note, load, act, toggle };
}

export function Feedback({ error, note, loading, label }: { error: string | null; note: string | null; loading: boolean; label: string }) {
  return <>
    {error && <p className="notice errorNotice" role="alert"><CircleDot />{error}</p>}
    {note && <p className="notice" role="status"><CircleDot />{note}</p>}
    {loading && <p className="notice"><CircleDot />Loading {label}...</p>}
  </>;
}

function ReviewDialog({ review, busy, onConfirm, onCancel }: { review: InstallReview; busy: boolean; onConfirm(): void; onCancel(): void }) {
  return <div className="featureBackdrop" onClick={onCancel}>
    <section className="surface featureDialog" role="dialog" aria-modal="true" aria-labelledby="skill-review-title" onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Escape') onCancel(); }}>
      <header><h3 id="skill-review-title">Install {review.name} {review.version}?</h3><button className="iconButton" aria-label="Close" onClick={onCancel}><X /></button></header>
      <p>{review.summary}</p>
      <p className={`featureChip ${review.highRisk ? 'failed' : 'indexed'}`}>{review.highRisk && <ShieldAlert />}Risk: {review.risk}</p>
      {review.tools.length > 0 && <><h4>Tools and permissions granted</h4><ul className="featureToolList">{review.tools.map(t => <li key={t}><code>{t}</code></li>)}</ul></>}
      <p className="featureMeta">Tool calls stay mediated by the runtime and workspace policy; approvals still apply.</p>
      <div className="actions"><button className="secondary" onClick={onCancel}>Cancel</button><button autoFocus disabled={busy} onClick={onConfirm}><PackageCheck />Install and grant</button></div>
    </section>
  </div>;
}

export function SkillsView() {
  const s = useSkills();
  const [review, setReview] = useState<InstallReview | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const install = async (r: InstallReview) => {
    // Grant exactly the tools shown in the dialog.
    if (await s.act(async () => { await api.installSkill(r.skillId, r.version, r.tools); return `${r.name} installed.`; })) setReview(null);
  };
  const uninstall = (skillId: string) => void s.act(async () => { await api.uninstallSkill(skillId); setConfirmRemove(null); return 'Skill uninstalled.'; });
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Governed capabilities</p><h2>Skills</h2><p>Published versions and approved tool grants.</p></div><button onClick={() => void s.load()}><RefreshCw />Refresh</button></section>
    <Feedback error={s.error} note={s.note} loading={s.loading} label="skills" />
    <div className="integrationGrid">{s.versions.map(v => {
      const state = skillInstallState(v, s.installs);
      return <article className="surface integration" key={v.id} id={skillAnchor(v.skill_id)}>
        <div className="integrationIcon"><PackageCheck /></div>
        <div><h3>{v.name} <small>{v.version}</small></h3><p>{v.description}</p><div className="tagRow">{v.required_tools.map(x => <span key={x}>{x}</span>)}</div></div>
        <div className="featureSkillActions">
          {state === 'available' && <button disabled={s.busy} onClick={() => setReview(installReview(v))}>Install</button>}
          {state === 'other_version' && <span className="statusBadge warning">other version installed</span>}
          {(state === 'installed' || state === 'disabled') && <>
            <span className={`statusBadge ${state === 'installed' ? 'success' : 'warning'}`}>{state === 'installed' ? 'installed' : 'disabled'}</span>
            <button className="secondary" disabled={s.busy} onClick={() => void s.toggle(v.skill_id, state === 'disabled')}>{state === 'installed' ? <><PauseCircle />Disable</> : <><PlayCircle />Enable</>}</button>
            {confirmRemove === v.skill_id
              ? <span className="featureConfirm"><button className="danger" disabled={s.busy} onClick={() => uninstall(v.skill_id)}>Uninstall</button><button className="secondary" onClick={() => setConfirmRemove(null)}>Cancel</button></span>
              : <button className="iconButton" aria-label={`Uninstall ${v.name}`} onClick={() => setConfirmRemove(v.skill_id)}><Trash2 /></button>}
          </>}
        </div>
      </article>;
    })}</div>
    {!s.loading && !s.error && !s.versions.length && <div className="emptyState"><Search /><h3>No published skills</h3><p>No skill versions are available.</p></div>}
    {review && <ReviewDialog review={review} busy={s.busy} onCancel={() => setReview(null)} onConfirm={() => void install(review)} />}
  </>;
}
