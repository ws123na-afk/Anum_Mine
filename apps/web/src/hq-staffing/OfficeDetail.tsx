/** One office against its blueprint: roles, agents and what each lacks, data sources, and the owner's apply step. */
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import type { StaffingAction, StaffingApplyResult, StaffingCreateContent, StaffingOffice, StaffingOfficeSummary, StaffingRole, StaffingUpdateContent } from '@anum/contracts';
import { ConfirmDialog, Drawer } from '../Dialog';
import { describeError, isPermissionDenied } from '../lib/errors';
import { formatNumber, t, type MessageKey } from '../lib/i18n';
import { applyAll, connectView, localText, rolesByUnit, totals, type StaffingClient } from '../lib/officeStaffing';
import { useLocale } from '../useLocale';
import { LoadingSkeleton } from '../PageParts';
import { openInOffice } from './openInOffice';

const GreenConcreteKitPanel = lazy(() => import('./GreenConcreteKitPanel'));

type Load = { state: 'loading' } | { state: 'ready'; office: StaffingOffice } | { state: 'error'; message: string; denied: boolean };

const level = (v: string) => t(`ostaff.level.${v}` as MessageKey);
const risk = (v: string | null) => t(`ostaff.risk.${v ?? 'high'}` as MessageKey);

export function OfficeDetail({ client, summary, onClose, onChanged, onGo }: {
  client: StaffingClient; summary: StaffingOfficeSummary; onClose(): void; onChanged(): void; onGo(view: string): void;
}) {
  const locale = useLocale();
  const lang = locale === 'ar' ? 'ar' : 'en';
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [results, setResults] = useState<StaffingApplyResult['results'] | null>(null);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const name = localText(summary.name, locale);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoad({ state: 'loading' });
    try { setLoad({ state: 'ready', office: await client.office(summary.building_id, lang) }); setPicked(new Set()); }
    catch (error) { setLoad({ state: 'error', message: t('ostaff.detailError', { detail: describeError(error) }), denied: isPermissionDenied(error) }); }
  }, [client, summary.building_id, lang]);
  useEffect(() => { void refresh(); }, [refresh]);

  const office = load.state === 'ready' ? load.office : null;
  const actions = office?.actions ?? [];
  const chosen = useMemo(() => actions.filter((a) => picked.has(a.id)), [actions, picked]);
  const roleName = (key: string) => { const r = office?.roles.find((x) => x.key === key); return r ? localText(r.name, locale) : key; };
  const agentName = (id?: string) => office?.roles.flatMap((r) => r.agents).find((a) => a.id === id)?.name ?? id ?? '';

  const toggle = (id: string) => setPicked((old) => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next; });

  const run = async () => {
    if (!office) return;
    setConfirming(false); setNote(null); setResults(null); setProgress({ done: 0, total: chosen.length });
    try {
      const out = await applyAll(client, office.building_id, lang, chosen, (done, total) => setProgress({ done, total }));
      const sum = totals(out);
      setResults(out);
      setNote(sum.stale && sum.stale === out.length ? { text: t('ostaff.stale'), error: true }
        : { text: t('ostaff.result', { created: formatNumber(sum.created), updated: formatNumber(sum.updated), other: formatNumber(sum.other) }) });
      onChanged();
      await refresh();
    } catch (error) {
      setNote({ text: (error as { status?: number }).status === 409 ? t('ostaff.stale') : t('ostaff.applyFailed', { detail: describeError(error) }), error: true });
      await refresh();
    } finally { setProgress(null); }
  };

  return <Drawer title={name} onClose={onClose}>
    <div className="osDetail">
      <div className="exActions"><button type="button" className="secondary" onClick={() => openInOffice(summary.name.en, onGo)}>{t('ostaff.open3d')}</button></div>
      {load.state === 'loading' && <LoadingSkeleton label={t('ostaff.detailLoading')} rows={4} />}
      {load.state === 'error' && <div className="notice errorNotice" role="alert"><span>{load.denied ? t('ostaff.denied') : load.message}</span>
        {!load.denied && <button type="button" className="secondary" onClick={() => void refresh()}>{t('ostaff.retry')}</button>}</div>}
      {note && <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}>{note.text}</p>}
      {progress && <p className="notice" role="status">{t('ostaff.applying', { done: formatNumber(progress.done), total: formatNumber(progress.total) })}</p>}
      {results && <ul className="osResults">{results.map((r) => <li key={r.id} data-outcome={r.outcome}>
        <span dir="auto">{r.id.startsWith('create:') ? roleName(r.id.slice(7)) : agentName(r.id.slice(7))}</span>: {r.outcome === 'failed' ? t('ostaff.outcome.failed', { detail: r.detail ?? '' }) : t(`ostaff.outcome.${r.outcome}` as MessageKey)}
      </li>)}</ul>}
      {office && <>
        <p className="muted">{t('ostaff.kind', { kind: localText(office.kind_name, locale) })} | {t('ostaff.coverage', { staffed: formatNumber(office.roles_staffed), total: formatNumber(office.roles_total) })} | {t('ostaff.limit', { used: formatNumber(office.limits.agents), max: formatNumber(office.limits.max_agents) })}</p>
        <section aria-labelledby="os-sources"><h3 id="os-sources">{t('ostaff.sources')}</h3>
          <ul className="osSources">{office.sources.map((s) => {
            const view = connectView(s);
            const label = localText(s.name, locale);
            return <li key={s.id} data-state={s.state}><span dir="auto">{label}</span><span className={`osPill ${s.state}`}>{t(`ostaff.source.${s.state}` as MessageKey)}</span>
              {view && <button type="button" className="secondary osSmall" onClick={() => onGo(view)}>{t('ostaff.connect', { name: label })}</button>}
              {s.state === 'not_connected' && !view && <span className="muted osSmallText">{t('ostaff.connectOperator', { name: label })}</span>}</li>;
          })}</ul>
        </section>
        <ApplyBar office={office} actions={actions} picked={picked} setPicked={setPicked} onReview={() => setConfirming(true)} busy={progress !== null} />
        {rolesByUnit(office).map((unit) => <section key={unit.id} className="osUnit" aria-labelledby={`os-u-${unit.id}`}>
          <h3 id={`os-u-${unit.id}`}>{localText(unit.name, locale)}</h3>
          {unit.roles.map((role) => <RoleCard key={role.key} role={role} locale={locale} actions={actions} picked={picked} toggle={toggle} canApply={office.can_apply} busy={progress !== null} />)}
        </section>)}
        {office.kind === 'green_concrete' && <Suspense fallback={<LoadingSkeleton label={t('gck.loading')} rows={2} />}>
          <GreenConcreteKitPanel onGo={onGo} onChanged={() => { onChanged(); void refresh(true); }} />
        </Suspense>}
        {office.other_agents.length > 0 && <section aria-labelledby="os-other"><h3 id="os-other">{t('ostaff.otherAgents')}</h3><p className="muted">{t('ostaff.otherAgentsText')}</p>
          <ul className="osAgents">{office.other_agents.map((a) => <li key={a.id}><strong dir="auto">{a.name}</strong> <span className="osPill">{t(`ostaff.status.${a.status}` as MessageKey)}</span></li>)}</ul></section>}
      </>}
    </div>
    {confirming && office && <ConfirmDialog title={t('ostaff.confirmTitle', { name })} confirmLabel={t('ostaff.apply')} onCancel={() => setConfirming(false)} onConfirm={() => void run()} confirmDisabled={chosen.length === 0}>
      <p>{t('ostaff.confirmIntro')}</p>
      <ul className="osConfirm">{chosen.map((a) => <li key={a.id} dir="auto">{describeAction(a, roleName, agentName)}</li>)}</ul>
      <p className="muted">{t('ostaff.confirm.paused')}</p>
    </ConfirmDialog>}
  </Drawer>;
}

function describeAction(a: StaffingAction, roleName: (k: string) => string, agentName: (id?: string) => string): string {
  if (a.kind === 'create') {
    const c = a.content as StaffingCreateContent;
    const seat = c.placement ? t('ostaff.confirm.seat', { desk: c.placement.desk_label, room: c.placement.room_name }) : t('ostaff.confirm.noSeat');
    return t('ostaff.confirm.create', { agent: c.agent.name, tools: formatNumber(c.agent.allowed_tools.length), skills: formatNumber(c.agent.allowed_skills.length), cap: formatNumber(c.monthly_cost_limit_usd), seat });
  }
  const c = a.content as StaffingUpdateContent;
  const rules = [c.autonomy ? t('ostaff.rule.autonomy') : null, c.add_rules_text ? t('ostaff.rule.never_list') : null, c.monthly_cost_limit_usd !== null ? t('ostaff.rule.cost_cap') : null,
    c.add_collection ? t('ostaff.rule.knowledge') : null].filter(Boolean).join(', ');
  return t('ostaff.confirm.update', { name: `${agentName(c.agent_id)} (${roleName(c.role)})`, tools: formatNumber(c.add_tools.length), skills: formatNumber(c.add_skills.length), rules: rules ? t('ostaff.confirm.rules', { items: rules }) : '' });
}

function ApplyBar({ office, actions, picked, setPicked, onReview, busy }: {
  office: StaffingOffice; actions: StaffingAction[]; picked: Set<string>; setPicked(s: Set<string>): void; onReview(): void; busy: boolean;
}) {
  if (!office.can_apply) return <p className="notice">{t('ostaff.ownerOnly')}</p>;
  if (actions.length === 0) return <p className="notice">{t('ostaff.nothingToApply')}</p>;
  return <div className="osApplyBar" role="group" aria-label={t('ostaff.select')}>
    <span>{t('ostaff.select')}</span>
    <button type="button" className="secondary osSmall" disabled={busy} onClick={() => setPicked(new Set(actions.map((a) => a.id)))}>{t('ostaff.selectAll')}</button>
    <button type="button" className="secondary osSmall" disabled={busy || picked.size === 0} onClick={() => setPicked(new Set())}>{t('ostaff.selectNone')}</button>
    <button type="button" disabled={busy || picked.size === 0} onClick={onReview}>{t('ostaff.review', { count: picked.size })}</button>
  </div>;
}

function RoleCard({ role, locale, actions, picked, toggle, canApply, busy }: {
  role: StaffingRole; locale: string; actions: StaffingAction[]; picked: Set<string>; toggle(id: string): void; canApply: boolean; busy: boolean;
}) {
  const name = localText(role.name, locale);
  const create = actions.find((a) => a.id === `create:${role.key}`);
  const r = role.rules;
  return <article className="osRole" data-role={role.key} data-state={role.state}>
    <header className="exCardHead"><h4 dir="auto">{name}</h4>
      <span className={`osPill ${role.state}`}>{t(`ostaff.role.${role.state}` as MessageKey)}</span>
      {role.limited && <span className="osPill limited">{t('ostaff.role.limited')}</span>}</header>
    <p>{localText(role.purpose, locale)}</p>
    {role.limited && <p className="muted osSmallText">{t('ostaff.role.limitedText', { items: role.missing.map((m) => localText(m, locale)).join('; ') })}</p>}
    <details className="osRules"><summary>{t('ostaff.rulesSummary')}</summary>
      <p>{t('ostaff.rules.autonomy', { read: level(r.autonomy.read), write: level(r.autonomy.write), external: level(r.autonomy.external) })}</p>
      <p>{t('ostaff.rules.maxRisk', { risk: risk(r.max_risk) })} | {t('ostaff.rules.cap', { amount: formatNumber(r.monthly_cost_limit_usd) })}</p>
      <p className="osLabel">{t('ostaff.rules.never')}</p>
      <ul>{r.never.map((n) => <li key={n.id}>{localText(n, locale)}</li>)}</ul>
      <p className="osLabel">{t('ostaff.skills')}</p>
      <ul className="osChips">{role.skills.map((s) => <li key={s.id} data-state={s.state}><code>{s.id}</code></li>)}</ul>
      <p className="osLabel">{t('ostaff.tools')}</p>
      <ul className="osChips">{role.tools.map((tool) => <li key={tool.name} data-state={tool.state}><code>{tool.name}</code> <span className="muted">{t('ostaff.toolRisk', { risk: risk(tool.risk) })}</span></li>)}</ul>
    </details>
    <p className="osLabel">{t('ostaff.agentsTitle')}</p>
    {role.agents.length === 0 && <p className="muted">{t('ostaff.noAgent')}</p>}
    {create && canApply && <label className="osPick"><input type="checkbox" checked={picked.has(create.id)} disabled={busy} onChange={() => toggle(create.id)} />{t('ostaff.action.create', { role: name })}</label>}
    {role.agents.length > 0 && <ul className="osAgents">{role.agents.map((a) => {
      const upd = actions.find((x) => x.id === `update:${a.id}`);
      return <li key={a.id} data-agent={a.id}>
        <div className="osAgentHead"><strong dir="auto">{a.name}</strong><span className="osPill">{t(`ostaff.status.${a.status}` as MessageKey)}</span></div>
        {a.complete && <span className="muted osSmallText">{t('ostaff.gap.complete')}</span>}
        {a.missing_skills.length > 0 && <p className="osSmallText">{t('ostaff.gap.skills', { items: a.missing_skills.join(', ') })}</p>}
        {a.missing_tools.length > 0 && <p className="osSmallText">{t('ostaff.gap.tools', { items: a.missing_tools.join(', ') })}</p>}
        {a.missing_rules.length > 0 && <p className="osSmallText">{t('ostaff.gap.rules', { items: a.missing_rules.map((x) => t(`ostaff.rule.${x}` as MessageKey)).join(', ') })}</p>}
        {a.missing_knowledge && <p className="osSmallText">{t('ostaff.gap.knowledge')}</p>}
        {a.over_risk_tools.length > 0 && <p className="osSmallText osWarn">{t('ostaff.gap.overRisk', { items: a.over_risk_tools.join(', ') })}</p>}
        {!a.complete && !a.can_edit && <p className="muted osSmallText">{t('ostaff.gap.cantEdit')}</p>}
        {upd && canApply && <label className="osPick"><input type="checkbox" checked={picked.has(upd.id)} disabled={busy} onChange={() => toggle(upd.id)} />{t('ostaff.action.update', { name: a.name, role: name })}</label>}
      </li>;
    })}</ul>}
  </article>;
}
