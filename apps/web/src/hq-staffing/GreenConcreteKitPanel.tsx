/** Green Concrete agents kit in the office drawer (docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md): role skills and the data each
 * needs, read tools, data gaps, the shared knowledge collection, routines (turned off until the owner turns them on) and
 * each agent's working memory. Only a workspace owner sets things up or switches a routine, always with a click. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { GcKit, GcKitSkill, GcRoutine, GcSetupResult } from '@anum/contracts';
import { AgentMemoryPanel } from '../AgentMemoryPanel';
import { ConfirmDialog } from '../Dialog';
import { apiRequest } from '../lib/api';
import { ApiError, describeError, isPermissionDenied } from '../lib/errors';
import { createKitClient, kitText, routineAction, scheduleWords, setupOutcomeKey, skillCounts, sortSkills } from '../lib/greenConcreteKit';
import { formatDateTime, formatNumber, t, type MessageKey } from '../lib/i18n';
import { LoadingSkeleton } from '../PageParts';
import { useLocale } from '../useLocale';

type Load = { state: 'loading' } | { state: 'ready'; kit: GcKit } | { state: 'denied' } | { state: 'notConnected' } | { state: 'error'; message: string };

export default function GreenConcreteKitPanel({ onGo, onChanged }: { onGo(view: string): void; onChanged?(): void }) {
  const locale = useLocale();
  const lang = locale === 'ar' ? 'ar' : 'en';
  const client = useMemo(() => createKitClient(apiRequest), []);
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [switching, setSwitching] = useState<{ routine: GcRoutine; enabled: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [results, setResults] = useState<GcSetupResult['results'] | null>(null);
  const [memoryOf, setMemoryOf] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setLoad({ state: 'ready', kit: await client.get(lang) }); }
    catch (error) {
      if (isPermissionDenied(error)) setLoad({ state: 'denied' });
      else if (error instanceof ApiError && error.status === 503) setLoad({ state: 'notConnected' });
      else setLoad({ state: 'error', message: t('gck.error', { detail: describeError(error) }) });
    }
  }, [client, lang]);
  useEffect(() => { void refresh(); }, [refresh]);

  const kit = load.state === 'ready' ? load.kit : null;
  const chosen = kit ? kit.items.filter((i) => picked.has(i.id)) : [];
  const toggle = (id: string) => setPicked((old) => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const sourceName = (id: string) => { const s = kit?.sources.find((x) => x.id === id); return s ? kitText(s.name, locale) : id; };
  const roleName = (key: string) => { const r = kit?.roles.find((x) => x.key === key); return r ? kitText(r.name, locale) : key; };
  const itemLabel = (id: string) => {
    if (id === 'knowledge') return t('gck.item.knowledge');
    const r = kit?.routines.find((x) => `routine:${x.key}` === id);
    return r ? t('gck.item.routine', { name: kitText(r.name, locale) }) : id;
  };

  const runSetup = async () => {
    setConfirming(false); setBusy(true); setNote(null); setResults(null);
    try {
      const out = await client.setup(chosen);
      setResults(out.results);
      setNote({ text: t('gck.setupDone', { count: formatNumber(out.created) }) });
      setPicked(new Set()); onChanged?.();
    } catch (error) {
      setNote({ text: (error as { status?: number }).status === 409 ? t('gck.stale') : t('gck.setupFailed', { detail: describeError(error) }), error: true });
    } finally { setBusy(false); await refresh(); }
  };
  const runSwitch = async () => {
    if (!switching) return;
    const { routine, enabled } = switching;
    setSwitching(null); setBusy(true); setNote(null);
    try {
      await client.routine(routine.key, enabled);
      setNote({ text: t(enabled ? 'gck.routine.turnedOn' : 'gck.routine.turnedOff', { name: kitText(routine.name, locale) }) });
    } catch (error) { setNote({ text: t('gck.routine.switchFailed', { detail: describeError(error) }), error: true }); }
    finally { setBusy(false); await refresh(); }
  };

  return <section className="gcKit" aria-labelledby="gck-title">
    <h3 id="gck-title">{t('gck.title')}</h3>
    <p className="muted">{t('gck.intro')}</p>
    {load.state === 'loading' && <LoadingSkeleton label={t('gck.loading')} rows={3} />}
    {load.state === 'denied' && <p className="notice">{t('gck.denied')}</p>}
    {load.state === 'notConnected' && <p className="notice">{t('gck.notConnected')}</p>}
    {load.state === 'error' && <div className="notice errorNotice" role="alert"><span>{load.message}</span>
      <button type="button" className="secondary osSmall" onClick={() => { setLoad({ state: 'loading' }); void refresh(); }}>{t('gck.retry')}</button></div>}
    {note && <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}>{note.text}</p>}
    {results && <ul className="osResults">{results.map((r) => <li key={r.id} data-outcome={r.outcome}>
      <span dir="auto">{itemLabel(r.id)}</span>: {r.outcome === 'failed' ? t('gck.outcome.failedDetail', { detail: r.detail ?? '' }) : t(setupOutcomeKey(r.outcome))}</li>)}</ul>}
    {kit && <>
      {!kit.building && <div className="notice"><span>{t('gck.noBuilding')}</span>
        <button type="button" className="secondary osSmall" onClick={() => onGo('office')}>{t('gck.noBuildingAction')}</button></div>}
      <SetupBar kit={kit} picked={picked} setPicked={setPicked} busy={busy} onReview={() => setConfirming(true)} />

      <section aria-labelledby="gck-skills"><h4 id="gck-skills">{t('gck.skills', { ready: formatNumber(skillCounts(kit.skills).ready), total: formatNumber(kit.skills.length) })}</h4>
        <ul className="gcGrid">{sortSkills(kit.skills).map((s) => <SkillCard key={s.id} skill={s} locale={locale} sourceName={sourceName} roleName={roleName} kit={kit} />)}</ul>
      </section>

      <section aria-labelledby="gck-gaps"><h4 id="gck-gaps">{t('gck.gaps')}</h4>
        <p className="muted osSmallText">{t('gck.gapsIntro')}</p>
        <ul className="gcList">{kit.gaps.map((g) => <li key={g.id} data-gap={g.id}><div className="gcRow"><strong dir="auto">{kitText(g.name, locale)}</strong><span className="osPill not_connected">{t('gck.notConnectedPill')}</span></div>
          <p className="osSmallText">{kitText(g.detail, locale)}</p></li>)}</ul>
      </section>

      <section aria-labelledby="gck-knowledge"><h4 id="gck-knowledge">{t('gck.knowledge')}</h4>
        <Knowledge kit={kit} picked={picked} toggle={toggle} busy={busy} onGo={onGo} />
      </section>

      <section aria-labelledby="gck-routines"><h4 id="gck-routines">{t('gck.routines')}</h4>
        <p className="muted osSmallText">{t('gck.routinesIntro')}</p>
        <ul className="gcList">{kit.routines.map((r) => {
          const words = scheduleWords(r.cron);
          const action = routineAction(r, kit.can_manage_automations);
          const item = kit.items.find((i) => i.id === `routine:${r.key}`);
          return <li key={r.key} data-routine={r.key} data-state={r.state}>
            <div className="gcRow"><strong dir="auto">{kitText(r.name, locale)}</strong><span className={`osPill ${r.state === 'on' ? 'connected' : 'not_connected'}`}>{t(`gck.routine.state.${r.state}` as MessageKey)}</span></div>
            <p className="osSmallText">{kitText(r.description, locale)}</p>
            <p className="osSmallText muted">{words ? t(words.key, { time: words.time, day: words.day ? t(words.day) : '', tz: r.timezone }) : r.cron}
              {r.next_run_at ? ` | ${t('gck.routine.next', { when: formatDateTime(r.next_run_at) })}` : ''}</p>
            {r.agent ? <p className="osSmallText">{t('gck.routine.agent', { name: r.agent.name, role: roleName(r.role) })}
              {r.agent.status === 'paused' && <span className="osWarn"> {t('gck.routine.agentPaused')}</span>}</p>
              : <p className="osSmallText osWarn">{t('gck.routine.noAgent', { role: roleName(r.role) })}</p>}
            {action === 'add' && item && <label className="osPick"><input type="checkbox" checked={picked.has(item.id)} disabled={busy} onChange={() => toggle(item.id)} />{t('gck.routine.add')}</label>}
            {action === 'turn_on' && <button type="button" className="secondary osSmall" disabled={busy} onClick={() => setSwitching({ routine: r, enabled: true })}>{t('gck.routine.turnOn')}</button>}
            {action === 'turn_off' && <button type="button" className="secondary osSmall" disabled={busy} onClick={() => setSwitching({ routine: r, enabled: false })}>{t('gck.routine.turnOff')}</button>}
            {r.workflow_id && <button type="button" className="linkButton osSmallText" onClick={() => onGo('automation')}>{t('gck.routine.open')}</button>}
          </li>;
        })}</ul>
        {!kit.can_manage_automations && <p className="muted osSmallText">{t('gck.routine.ownerOnly')}</p>}
      </section>

      <section aria-labelledby="gck-agents"><h4 id="gck-agents">{t('gck.agents')}</h4>
        {kit.agents.length === 0 ? <p className="muted">{t('gck.agentsEmpty')}</p> : <ul className="gcList">{kit.agents.map((a) => <li key={a.id} data-agent={a.id}>
          <div className="gcRow"><strong dir="auto">{a.name}</strong><span className="osPill">{t(`ostaff.status.${a.status}` as MessageKey)}</span><span className="muted osSmallText">{roleName(a.role)}</span></div>
          <p className="osSmallText">{t('gck.agent.facts', { skills: formatNumber(a.skills.length), tools: formatNumber(a.tools.length) })}
            {' | '}{a.memories === null ? t('gck.agent.memoriesHidden') : t('gck.agent.memories', { count: a.memories })}
            {' | '}{a.reads_collection ? t('gck.agent.readsCollection') : t('gck.agent.noCollection')}</p>
          {a.skills.length === 0 && <p className="osSmallText osWarn">{t('gck.agent.noSkills')}</p>}
          {a.memories !== null && <button type="button" className="secondary osSmall" aria-expanded={memoryOf === a.id} aria-controls={`gck-mem-${a.id}`}
            onClick={() => setMemoryOf(memoryOf === a.id ? null : a.id)}>{memoryOf === a.id ? t('gck.agent.hideMemory') : t('gck.agent.showMemory')}</button>}
          {memoryOf === a.id && <div id={`gck-mem-${a.id}`}><AgentMemoryPanel agentId={a.id} canEdit={a.can_edit} heading="h4" /></div>}
        </li>)}</ul>}
      </section>
    </>}
    {confirming && kit && <ConfirmDialog title={t('gck.confirmTitle')} confirmLabel={t('gck.confirm')} busy={busy} confirmDisabled={chosen.length === 0}
      onCancel={() => setConfirming(false)} onConfirm={() => void runSetup()}>
      <p>{t('gck.confirmIntro')}</p>
      <ul className="osConfirm">{chosen.map((i) => <li key={i.id} dir="auto">{i.kind === 'knowledge' ? t('gck.confirm.knowledge') : t('gck.confirm.routine', { name: itemLabel(i.id) })}</li>)}</ul>
      <p className="muted">{t('gck.confirm.off')}</p>
    </ConfirmDialog>}
    {switching && <ConfirmDialog title={t(switching.enabled ? 'gck.routine.onTitle' : 'gck.routine.offTitle', { name: kitText(switching.routine.name, locale) })}
      confirmLabel={t(switching.enabled ? 'gck.routine.turnOn' : 'gck.routine.turnOff')} busy={busy} onCancel={() => setSwitching(null)} onConfirm={() => void runSwitch()}>
      <p>{t(switching.enabled ? 'gck.routine.onText' : 'gck.routine.offText')}</p>
      {switching.enabled && switching.routine.agent?.status === 'paused' && <p className="osWarn">{t('gck.routine.agentPaused')}</p>}
    </ConfirmDialog>}
  </section>;
}

function SetupBar({ kit, picked, setPicked, busy, onReview }: { kit: GcKit; picked: Set<string>; setPicked(s: Set<string>): void; busy: boolean; onReview(): void }) {
  if (!kit.can_manage) return <p className="notice">{t('gck.ownerOnly')}</p>;
  if (kit.items.length === 0) return <p className="notice">{t('gck.nothingToSetUp')}</p>;
  return <div className="osApplyBar" role="group" aria-label={t('gck.select')}>
    <span>{t('gck.select')}</span>
    <button type="button" className="secondary osSmall" disabled={busy} onClick={() => setPicked(new Set(kit.items.map((i) => i.id)))}>{t('ostaff.selectAll')}</button>
    <button type="button" className="secondary osSmall" disabled={busy || picked.size === 0} onClick={() => setPicked(new Set())}>{t('ostaff.selectNone')}</button>
    <button type="button" disabled={busy || picked.size === 0} onClick={onReview}>{t('gck.review', { count: picked.size })}</button>
  </div>;
}

function SkillCard({ skill, locale, sourceName, roleName, kit }: {
  skill: GcKitSkill; locale: string; sourceName(id: string): string; roleName(key: string): string; kit: GcKit;
}) {
  const tool = kit.tools.find((x) => x.name === skill.tool);
  return <li className="osRole" data-skill={skill.id} data-state={skill.state}>
    <div className="gcRow"><h5 dir="auto">{kitText(skill.name, locale)}</h5><span className={`osPill ${skill.state === 'ready' ? 'connected' : skill.state === 'no_access' ? 'no_access' : 'not_connected'}`}>{t(`gck.skill.state.${skill.state}` as MessageKey)}</span></div>
    <p className="osSmallText">{kitText(skill.description, locale)}</p>
    <p className="osSmallText"><span className="osLabel">{t('gck.skill.needs')}</span> {skill.sources.map((s, i) => <span key={s.id}>{i > 0 ? ', ' : ''}<span dir="auto">{sourceName(s.id)}</span> ({t(`ostaff.source.${s.state}` as MessageKey)})</span>)}</p>
    <p className="osSmallText"><span className="osLabel">{t('gck.skill.tool')}</span> <span dir="auto">{tool ? kitText(tool.label, locale) : skill.tool}</span> (<code>{skill.tool}</code>, {t('gck.skill.readOnly', { risk: t(`ostaff.risk.${tool?.risk ?? 'high'}` as MessageKey) })})</p>
    <p className="osSmallText"><span className="osLabel">{t('gck.skill.roles')}</span> {skill.roles.map(roleName).join(', ')}</p>
    <details className="osRules"><summary>{t('gck.skill.details')}</summary>
      <p className="osLabel">{t('gck.skill.inputs')}</p><ul>{skill.inputs.map((x, i) => <li key={i}>{kitText(x, locale)}</li>)}</ul>
      <p className="osLabel">{t('gck.skill.output')}</p><p>{kitText(skill.output, locale)}</p>
      <p className="osLabel">{t('gck.skill.guardrails')}</p><ul>{skill.guardrails.map((x, i) => <li key={i}>{kitText(x, locale)}</li>)}</ul>
    </details>
  </li>;
}

function Knowledge({ kit, picked, toggle, busy, onGo }: { kit: GcKit; picked: Set<string>; toggle(id: string): void; busy: boolean; onGo(view: string): void }) {
  const k = kit.knowledge;
  const item = kit.items.find((i) => i.id === 'knowledge');
  if (k.state === 'no_access') return <p className="muted">{t('gck.knowledge.noAccess')}</p>;
  if (k.state === 'missing') return <div className="gcBox"><p>{t('gck.knowledge.missing')}</p>
    {item && kit.can_manage ? <label className="osPick"><input type="checkbox" checked={picked.has(item.id)} disabled={busy} onChange={() => toggle(item.id)} />{t('gck.knowledge.create')}</label>
      : <p className="muted osSmallText">{t('gck.ownerOnly')}</p>}</div>;
  return <div className="gcBox" data-state={k.state}>
    <p><strong dir="auto">{k.collection?.name}</strong> {k.state === 'empty' ? t('gck.knowledge.empty') : t('gck.knowledge.items', { count: k.collection?.item_count ?? 0 })}</p>
    <p className="muted osSmallText">{t('gck.knowledge.how')}</p>
    <button type="button" className="secondary osSmall" onClick={() => onGo('knowledge')}>{t('gck.knowledge.open')}</button>
  </div>;
}
