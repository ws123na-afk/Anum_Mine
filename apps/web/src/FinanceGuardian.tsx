// Finance, Guardian (docs/finance.md#guardian): the person's monitoring rules, the review queue of
// payments a rule flags, notification delivery as it really is, quiet hours and the Guardian audit log.
// Every figure comes from the API. Guardian flags and asks; it never moves money, approves, trades or
// contacts a bank. A rule change (a pause included) applies only when the person confirms it with the
// payload hash shown.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  AlertTriangle, Ban, Banknote, Bell, CalendarDays, Check, ChevronLeft, ChevronRight, CircleCheck, Clock, Coins, ExternalLink, Hourglass,
  Info, ListChecks, Lock, Mail, MessageSquare, Moon, Pause, Pencil, PieChart, Play, Plus, RotateCcw, Scale, Search, ShieldCheck, Store, Trash2, X,
} from 'lucide-react';
import type {
  FinanceCategory, FinanceEvaluation, FinanceGuardian, FinanceGuardianActivity, FinanceGuardianAlerts, FinanceGuardianChange,
  FinanceGuardianFlaggedItem, FinanceGuardianReviewQueue, FinanceGuardianRuleKind, FinanceGuardianRules,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import {
  categoryLabel, describeRules, draftFromRules, expiresLabel, formatAmount, isolate, monthLabel, parseAmount, ruleKindLabel, rulesFromDraft,
  shiftMonth, shortHash, type FinanceTab, type RulesDraft,
} from './lib/finance';
import {
  QUEUE_PAGE, RULE_KINDS, activityCells, appendPage, latestOnly, nextQueuePage, reasonText, configuredKinds, draftAdding, isPaused, isSet, lastTriggered, monitoringState, testPrefill,
  thresholdLines, withAllPaused, withPaused, type TestPrefill,
} from './lib/financeGuardian';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';
import { LimitRows } from './FinancePlan';
import { FinCard, FinancePageHead, KpiCard, KpiRow, PeriodPicker, ScopeSwitch, StatusChip } from './FinanceShell';
import './financeGuardian.css';

const CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'cash', 'other',
];
const KIND_ICON: Record<FinanceGuardianRuleKind, ReactNode> = {
  allowed_currencies: <Coins aria-hidden="true" />, max_single: <Banknote aria-hidden="true" />, max_daily_total: <CalendarDays aria-hidden="true" />,
  category_caps: <PieChart aria-hidden="true" />, blocked_categories: <Ban aria-hidden="true" />, blocked_merchants: <Store aria-hidden="true" />,
  approval_above: <CircleCheck aria-hidden="true" />, quiet_hours: <Moon aria-hidden="true" />, cooling_off: <Hourglass aria-hidden="true" />,
};
const SNOOZE_DAYS = [1, 3, 7, 30] as const;
const AUDIT_PAGE = 5;

/** Loads with ``load``; when ``load`` changes (another month or tab) an older answer is dropped, never shown. */
function useLoad<T>(load: () => Promise<T>): [Load<T>, () => Promise<void>] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const [guard] = useState(latestOnly);
  const run = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try {
      const answer = await guard(load());
      if (answer.current) setState({ kind: 'ready', value: answer.value });
    } catch (e) { setState(failed(e)); }
  }, [load, guard]);
  useEffect(() => { void run(); }, [run]);
  return [state, run];
}

const money = (amount: string, currency: string) => isolate(formatAmount(amount, currency));
const day = (iso: string) => formatDateTime(`${iso}T00:00:00Z`, { dateStyle: 'medium', timeZone: 'UTC' });

type Notify = (text: string, error?: boolean) => void;

/**
 * The review queue for a month and tab: the first bounded page, then "Show more" appends the next
 * page by offset. A month or tab change starts again; answers for an earlier month or tab are dropped.
 */
function useQueue(month: string | null, tab: 'awaiting' | 'resolved' | 'snoozed'): [Load<FinanceGuardianReviewQueue>, () => Promise<void>, () => Promise<void>] {
  const [state, setState] = useState<Load<FinanceGuardianReviewQueue>>({ kind: 'loading' });
  const [guard] = useState(latestOnly);
  const fetchPage = useCallback((offset: number, limit: number) => api.guardianReview({ month: month ?? undefined, state: tab, offset, limit }), [month, tab]);
  const reload = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try {
      const answer = await guard(fetchPage(0, QUEUE_PAGE));
      if (answer.current) setState({ kind: 'ready', value: answer.value });
    } catch (e) { setState(failed(e)); }
  }, [fetchPage, guard]);
  const more = useCallback(async () => {
    if (state.kind !== 'ready') return;
    const page = nextQueuePage(state.value.items.length, state.value.total);
    if (!page) return;
    try {
      const answer = await guard(fetchPage(page.offset, page.limit));
      if (answer.current) setState((current) => (current.kind === 'ready'
        ? { kind: 'ready', value: { ...answer.value, items: appendPage(current.value.items, answer.value.items) } } : current));
    } catch (e) { setState(failed(e)); }
  }, [state, fetchPage, guard]);
  useEffect(() => { setState({ kind: 'loading' }); void reload(); }, [reload]);
  return [state, reload, more];
}

// ---- the page --------------------------------------------------------------------------------------

export function GuardianPage({ base, onOpen }: { base: string; onOpen: (tab: FinanceTab) => void }) {
  const [state, reload] = useLoad(api.getGuardian);
  const thisMonth = useMemo(() => new Date().toISOString().slice(0, 7), []);
  const [month, setMonth] = useState<string | null>(null);
  const [queueTab, setQueueTab] = useState<'awaiting' | 'resolved' | 'snoozed'>('awaiting');
  const [queue, reloadQueue, moreQueue] = useQueue(month, queueTab);
  const loadAlerts = useCallback(() => api.guardianAlerts(month ?? undefined), [month]);
  const [alerts, reloadAlerts] = useLoad(loadAlerts);
  const [auditKey, setAuditKey] = useState(0);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [editor, setEditor] = useState<{ only?: FinanceGuardianRuleKind; adding?: boolean } | null>(null);
  const [proposal, setProposal] = useState<{ rules: FinanceGuardianRules; reason: string } | null>(null);
  const [testing, setTesting] = useState<FinanceGuardianRuleKind | null>(null);
  const [picking, setPicking] = useState(false);
  const notify: Notify = (text, error) => setNotice({ text, error });
  const refresh = () => { void reload(); void reloadQueue(); void reloadAlerts(); setAuditKey((k) => k + 1); };

  if (state.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('fguard.loading')}</Notice></section>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('fguard.error', { message: state.message })} retry={() => void reload()} /></section>;
  const guardian = state.value;
  const rules = guardian.rules;
  const monitoring = monitoringState(rules);
  const shownMonth = month ?? (queue.kind === 'ready' ? queue.value.month : alerts.kind === 'ready' ? alerts.value.month : thisMonth);
  const timezone = queue.kind === 'ready' ? queue.value.timezone : null;
  const propose = (next: FinanceGuardianRules, reason: string) => setProposal({ rules: next, reason });
  const unset = RULE_KINDS.filter((kind) => !isSet(rules, kind));

  return <div className="fgPage">
    <FinancePageHead title={t('fguard.title')} subtitle={t('fguard.subtitle')}
      actions={<>
        {monitoring === 'on' && <button type="button" onClick={() => propose(withAllPaused(rules, true), t('fguard.pauseReason'))}><Pause aria-hidden="true" />{t('fguard.pause')}</button>}
        {monitoring === 'off' && <button type="button" onClick={() => propose(withAllPaused(rules, false), t('fguard.resumeReason'))}><Play aria-hidden="true" />{t('fguard.resume')}</button>}
        {monitoring === 'none' && <button type="button" onClick={() => setPicking(true)}><Plus aria-hidden="true" />{t('fguard.rules.add')}</button>}
      </>}
      controls={<>
        <ScopeSwitch value="personal" onChange={() => undefined} unavailable={{ business: t('fguard.scope.note'), all: t('fguard.scope.note') }} />
        <PeriodPicker month={shownMonth} onChange={setMonth} max={thisMonth} />
        <div className="finControl fgMonitoring">
          <label className="fgSwitch">
            <span className="finControlLabel">{t('fguard.monitoring')}</span>
            <input type="checkbox" role="switch" checked={monitoring === 'on'} disabled={monitoring === 'none'} aria-label={t('fguard.monitoring.switch')}
              onChange={(e) => propose(withAllPaused(rules, !e.target.checked), e.target.checked ? t('fguard.resumeReason') : t('fguard.pauseReason'))} />
            <span className="fgSwitchState">{t(`fguard.monitoring.${monitoring}` as MessageKey)}</span>
          </label>
          {monitoring === 'on' && <StatusChip tone="ok"><Check aria-hidden="true" />{t('fguard.monitoring.active')}</StatusChip>}
          {monitoring === 'off' && <StatusChip tone="warn"><Pause aria-hidden="true" />{t('fguard.monitoring.paused')}</StatusChip>}
        </div>
      </>} />

    {notice && <Notice error={notice.error}>{notice.text}</Notice>}

    <Kpis queue={queue} rules={rules} month={shownMonth} />

    <div className="fgGrid">
      <div className="fgColumn">
        {guardian.pending.length > 0 && <section className="finCard"><Pending changes={guardian.pending} current={rules} onDone={(text, error) => { notify(text, error); refresh(); }} /></section>}
        <RulesCard guardian={guardian} queue={queue} month={shownMonth}
          onAdd={() => setPicking(true)} onEditAll={() => setEditor({})} onEdit={(kind) => setEditor({ only: kind })} onTest={setTesting}
          onToggle={(kind, paused) => propose(withPaused(rules, kind, paused), paused ? t('fguard.pauseReason') : t('fguard.resumeReason'))} />
        <AlertsSummary alerts={alerts} hasRules={configuredKinds(rules).length > 0} retry={() => void reloadAlerts()} />
      </div>
      <div className="fgColumn">
        <ReviewQueue queue={queue} tab={queueTab} month={shownMonth} hasActive={monitoring === 'on'}
          onTab={setQueueTab} onMore={() => void moreQueue()}
          onRetry={() => void reloadQueue()} onAdd={() => setPicking(true)} onOpen={onOpen}
          onDecided={(text, error) => { notify(text, error); void reloadQueue(); setAuditKey((k) => k + 1); }} />
        <FinCard id="fg-privacy" className="fgPrivacy" icon={<Lock />} title={t('fguard.privacy.title')}>
          <p className="muted">{t('fguard.privacy.text')}</p>
          <div className="fgDelivery">
            <Delivery />
            <QuietHours rules={rules} timezone={timezone} onEdit={() => setEditor(isSet(rules, 'quiet_hours') ? { only: 'quiet_hours' } : { only: 'quiet_hours', adding: true })} />
          </div>
        </FinCard>
        <AuditLog key={auditKey} timezone={timezone} />
      </div>
    </div>

    {picking && <AddRulePicker unset={unset} onPick={(kind) => { setPicking(false); setEditor({ only: kind, adding: true }); }} onClose={() => setPicking(false)} />}
    {editor && <RulesEditor rules={rules} base={base} only={editor.only} adding={editor.adding}
      onClose={(text) => { setEditor(null); if (text) { notify(text); refresh(); } }} />}
    {proposal && <ProposeDialog rules={proposal.rules} reason={proposal.reason} current={rules}
      onDone={(text, error) => { setProposal(null); notify(text, error); refresh(); }} />}
    {testing && <TestDialog kind={testing} rules={rules} base={base} onClose={() => setTesting(null)} />}
  </div>;
}

// ---- KPIs ------------------------------------------------------------------------------------------

function Kpis({ queue, rules, month }: { queue: Load<FinanceGuardianReviewQueue>; rules: FinanceGuardianRules; month: string }) {
  const set = configuredKinds(rules);
  const active = set.filter((kind) => !isPaused(rules, kind));
  const counts = queue.kind === 'ready' ? queue.value.counts : null;
  const figure = (value: number | null) => (value === null ? (queue.kind === 'loading' ? '...' : '-') : String(value));
  return <KpiRow label={t('fguard.kpi.label')}>
    <KpiCard icon={<Bell />} label={t('fguard.kpi.alerts')} value={figure(counts?.flagged ?? null)} hint={t('fguard.kpi.alertsHint', { month: monthLabel(month) })} />
    <KpiCard icon={<ShieldCheck />} label={t('fguard.kpi.rules')} value={String(active.length)}
      hint={set.length ? t('fguard.kpi.rulesHint', { active: active.length, total: set.length }) : t('fguard.kpi.rulesNone')} />
    <KpiCard icon={<CircleCheck />} label={t('fguard.kpi.resolved')} value={figure(counts?.resolved ?? null)} hint={t('fguard.kpi.resolvedHint')} tone={counts && counts.resolved > 0 ? 'good' : ''} />
    <KpiCard icon={<AlertTriangle />} label={t('fguard.kpi.awaiting')} value={figure(counts?.awaiting ?? null)}
      hint={t('fguard.kpi.awaitingHint', { count: counts?.snoozed ?? 0 })} tone={counts && counts.awaiting > 0 ? 'warn' : ''} />
  </KpiRow>;
}

// ---- monitoring rules ------------------------------------------------------------------------------

function RulesCard({ guardian, queue, month, onAdd, onEditAll, onEdit, onTest, onToggle }: {
  guardian: FinanceGuardian; queue: Load<FinanceGuardianReviewQueue>; month: string | null; onAdd: () => void; onEditAll: () => void;
  onEdit: (kind: FinanceGuardianRuleKind) => void; onTest: (kind: FinanceGuardianRuleKind) => void; onToggle: (kind: FinanceGuardianRuleKind, paused: boolean) => void;
}) {
  const rules = guardian.rules;
  const kinds = configuredKinds(rules);
  const stats = queue.kind === 'ready' ? queue.value.rules : [];
  const shownMonth = month ? monthLabel(month) : '';
  return <FinCard id="fin-guardian" className="fgRules" title={t('fguard.rules.title')} subtitle={t('fguard.rules.text')} actions={<>
    <button type="button" className="secondary" onClick={onEditAll}><Pencil aria-hidden="true" />{kinds.length ? t('finance.guardian.change') : t('finance.guardian.set')}</button>
    <button type="button" onClick={onAdd} disabled={kinds.length === RULE_KINDS.length}><Plus aria-hidden="true" />{t('fguard.rules.add')}</button>
  </>}>
    {kinds.length > 0 && <p className="finVersion"><span className="statusBadge neutral">{t('finance.guardian.version', { version: guardian.version })}</span>
      {guardian.confirmed_at && <small className="muted">{t('finance.guardian.confirmedAt', { date: formatDateTime(guardian.confirmed_at) })}</small>}</p>}
    {kinds.length === 0
      ? <Empty icon={<ShieldCheck aria-hidden="true" />} title={t('finance.guardian.empty')} text={t('finance.guardian.emptyText')}
        action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('fguard.rules.add')}</button>} />
      : <ul className="fgRuleList" aria-label={t('fguard.rules.list')}>{kinds.map((kind) => {
        const name = ruleKindLabel(kind);
        const paused = isPaused(rules, kind);
        const lines = thresholdLines(rules, kind);
        const nameId = `fg-rule-${kind}`;
        return <li key={kind} className={`fgRule ${paused ? 'fgRulePaused' : ''}`} aria-labelledby={nameId}>
          <label className="fgSwitch fgRuleSwitch">
            <input type="checkbox" role="switch" checked={!paused} aria-label={t('fguard.rule.switch', { name })} onChange={(e) => onToggle(kind, !e.target.checked)} />
          </label>
          <div className="fgRuleMain">
            <div className="fgRuleName">
              <span className="fgRuleIcon">{KIND_ICON[kind]}</span>
              <h3 id={nameId}>{name}</h3>
              <StatusChip tone={paused ? 'muted' : 'ok'}>{paused ? t('fguard.rule.paused') : t('fguard.rule.enabled')}</StatusChip>
            </div>
            <p className="fgRuleWhen"><strong>{t('fguard.rule.when')}</strong> {t(`fguard.when.${kind}` as MessageKey)}</p>
            <p className="fgRuleMeta muted"><span><strong>{t('fguard.rule.scope')}</strong> {t('fguard.scope.personal')}</span>
              <span><strong>{t('fguard.rule.last')}</strong> {lastTriggered(kind, stats.find((s) => s.kind === kind), shownMonth)}</span></p>
          </div>
          <div className="fgThreshold">
            <span className="fgThresholdLabel">{t(`fguard.threshold.${kind}` as MessageKey)}</span>
            <div className="fgThresholdField">
              <span className="fgThresholdValue">{lines.slice(0, 2).join(' · ')}{lines.length > 2 && <> <small className="muted">{t('fguard.threshold.more', { count: lines.length - 2 })}</small></>}</span>
              <button type="button" className="iconButton" aria-label={t('fguard.rule.editThreshold', { name })} onClick={() => onEdit(kind)}><Pencil aria-hidden="true" /></button>
            </div>
          </div>
          <div className="fgRuleActions">
            <button type="button" className="secondary" aria-label={t('fguard.rule.editLabel', { name })} onClick={() => onEdit(kind)}><Pencil aria-hidden="true" />{t('fguard.rule.edit')}</button>
            <button type="button" className="secondary" aria-label={t('fguard.rule.testLabel', { name })} onClick={() => onTest(kind)}><Play aria-hidden="true" />{t('fguard.rule.test')}</button>
            <button type="button" className="secondary" aria-label={t(paused ? 'fguard.rule.resumeLabel' : 'fguard.rule.pauseLabel', { name })} onClick={() => onToggle(kind, !paused)}>
              {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}{paused ? t('fguard.rule.resume') : t('fguard.rule.pause')}</button>
          </div>
        </li>;
      })}</ul>}
  </FinCard>;
}

function AddRulePicker({ unset, onPick, onClose }: { unset: FinanceGuardianRuleKind[]; onPick: (kind: FinanceGuardianRuleKind) => void; onClose: () => void }) {
  return <Modal title={t('fguard.rules.addTitle')} className="formDialog" onClose={onClose}>
    <p className="muted">{unset.length ? t('fguard.rules.addText') : t('fguard.rules.allSet')}</p>
    <ul className="fgPicker">{unset.map((kind) => <li key={kind}>
      <button type="button" className="secondary" onClick={() => onPick(kind)}>
        <span className="fgRuleIcon">{KIND_ICON[kind]}</span>
        <span><strong>{ruleKindLabel(kind)}</strong><small className="muted">{t(`fguard.when.${kind}` as MessageKey)}</small></span>
      </button>
    </li>)}</ul>
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
  </Modal>;
}

/** Proposes the rules, shows exactly what changes with its code, and confirms only on the person's word. */
function ProposeDialog({ rules, reason, current, onDone }: { rules: FinanceGuardianRules; reason: string; current: FinanceGuardianRules; onDone: Notify }) {
  const [change, setChange] = useState<FinanceGuardianChange | null>(null);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    api.proposeRules(rules, reason).then((c) => { if (live) setChange(c); }, (e) => { if (live) setError(describeError(e)); });
    return () => { live = false; };
  }, [rules, reason]);
  async function confirm() {
    if (!change) return;
    setBusy(true);
    try { const result = await api.confirmChange(change.id, change.payload_hash); onDone(t('finance.guardian.confirmed', { version: result.version })); }
    catch (e) { setError(describeError(e)); setBusy(false); }
  }
  async function cancel() {
    // A pause or resume that was not confirmed is withdrawn, so nothing waits half done.
    if (change) { try { await api.rejectChange(change.id); } catch { /* it lapses by itself in 24 hours */ } }
    onDone(t('fguard.change.cancelled'));
  }
  if (!change) {
    return <Modal title={t('finance.guardian.reviewTitle')} className="formDialog" onClose={() => onDone(t('fguard.change.cancelled'))}>
      {error ? <><Notice error>{error}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => onDone(t('fguard.change.cancelled'))}>{t('common.cancel')}</button></div></>
        : <Notice busy>{t('finance.saving')}</Notice>}
    </Modal>;
  }
  return <ConfirmDialog title={t('finance.guardian.reviewTitle')} confirmLabel={t('finance.guardian.confirm')} busy={busy} onConfirm={() => void confirm()} onCancel={() => void cancel()}>
    <ChangeSummary change={change} current={current} />
    {error && <p className="cmFieldError" role="alert">{error}</p>}
  </ConfirmDialog>;
}

function Pending({ changes, current, onDone }: { changes: FinanceGuardianChange[]; current: FinanceGuardianRules; onDone: Notify }) {
  const [reviewing, setReviewing] = useState<FinanceGuardianChange | null>(null), [busy, setBusy] = useState(false);
  async function reject(change: FinanceGuardianChange) {
    try { await api.rejectChange(change.id); onDone(t('finance.guardian.rejected')); } catch (e) { onDone(describeError(e), true); }
  }
  async function confirm() {
    if (!reviewing) return;
    setBusy(true);
    try { const result = await api.confirmChange(reviewing.id, reviewing.payload_hash); onDone(t('finance.guardian.confirmed', { version: result.version })); }
    catch (e) { onDone(describeError(e), true); } finally { setBusy(false); setReviewing(null); }
  }
  return <section className="finPending" aria-labelledby="fin-pending">
    <h3 id="fin-pending">{t('finance.guardian.pending', { count: changes.length })}</h3>
    <ul className="cmList">{changes.map((change) => <li key={change.id}>
      <div><strong>{change.proposed_by === 'agent' ? t('finance.guardian.byAgent') : t('finance.guardian.byYou')}</strong>
        <small>{expiresLabel(change.expires_at)}{change.reason ? ' · ' : ''}{change.reason && <span dir="auto">{change.reason}</span>}</small>
        {change.loosens.length > 0 && <small className="finLoosens">{t('finance.guardian.loosensList', { list: change.loosens.map(ruleKindLabel).join(', ') })}</small>}</div>
      <div className="finItemEnd">
        <button type="button" onClick={() => setReviewing(change)}>{t('finance.guardian.review')}</button>
        <button type="button" className="secondary" onClick={() => void reject(change)}><X aria-hidden="true" />{t('finance.guardian.reject')}</button>
      </div>
    </li>)}</ul>
    {reviewing && <ConfirmDialog title={t('finance.guardian.reviewTitle')} confirmLabel={t('finance.guardian.confirm')} busy={busy} onConfirm={() => void confirm()} onCancel={() => setReviewing(null)}>
      <ChangeSummary change={reviewing} current={current} />
    </ConfirmDialog>}
  </section>;
}

function ChangeSummary({ change, current }: { change: FinanceGuardianChange; current: FinanceGuardianRules }) {
  const before = new Set(describeRules(current));
  const after = describeRules(change.rules);
  const removed = [...before].filter((line) => !after.includes(line));
  return <div className="finChange">
    <p>{t('finance.guardian.reviewText')}</p>
    {after.length === 0 ? <p className="muted">{t('finance.guardian.noRulesAfter')}</p> : <ul className="finRules">{after.map((line) => <li key={line} className={before.has(line) ? '' : 'finRuleNew'}>
      <Check aria-hidden="true" />{line}{!before.has(line) && <span className="statusBadge neutral">{t('finance.guardian.new')}</span>}</li>)}</ul>}
    {removed.length > 0 && <><p className="finLoosens">{t('finance.guardian.removed')}</p><ul className="finRules finRemoved">{removed.map((line) => <li key={line}><X aria-hidden="true" />{line}</li>)}</ul></>}
    {change.loosens.length > 0 && <p className="notice" role="note"><AlertTriangle aria-hidden="true" />{t('finance.guardian.loosensWarning', { list: change.loosens.map(ruleKindLabel).join(', ') })}</p>}
    <p className="muted finHash">{t('finance.guardian.code')} <code dir="ltr">{shortHash(change.payload_hash)}</code></p>
  </div>;
}

function RulesEditor({ rules, base, only, adding, onClose }: {
  rules: FinanceGuardianRules; base: string; only?: FinanceGuardianRuleKind; adding?: boolean; onClose: (message: string | null) => void;
}) {
  const [draft, setDraft] = useState<RulesDraft>(() => (only && adding ? draftAdding(draftFromRules(rules), only, base) : draftFromRules(rules)));
  const [reason, setReason] = useState(''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [proposed, setProposed] = useState<FinanceGuardianChange | null>(null);
  const errorId = useId();
  const set = <K extends keyof RulesDraft>(key: K, value: RulesDraft[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const shows = (kind: FinanceGuardianRuleKind) => !only || only === kind;
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = rulesFromDraft(draft);
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    try { setProposed(await api.proposeRules(checked.value, reason.trim())); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (!proposed) return;
    setBusy(true); setError(null);
    try { const result = await api.confirmChange(proposed.id, proposed.payload_hash); onClose(t('finance.guardian.confirmed', { version: result.version })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  if (proposed) {
    return <Modal title={t('finance.guardian.reviewTitle')} className="formDialog" onClose={() => onClose(t('finance.guardian.left'))}>
      <ChangeSummary change={proposed} current={rules} />
      <FormError id={errorId} text={error} />
      <div className="actions"><button type="button" disabled={busy} onClick={() => void confirm()}><ShieldCheck aria-hidden="true" />{busy ? t('finance.saving') : t('finance.guardian.confirm')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setProposed(null)}>{t('finance.guardian.back')}</button></div>
    </Modal>;
  }
  const limitSection = (kind: 'max_single' | 'max_daily_total' | 'approval_above', key: 'maxSingle' | 'maxDaily' | 'approvalAbove', help: string) => shows(kind) && <fieldset className="finMapping">
    <legend>{ruleKindLabel(kind)}</legend><p className="muted finHelp">{help}</p>
    <LimitRows rows={draft[key]} onChange={(rows) => set(key, rows)} label={t('finance.guardian.amount')} addLabel={t('finance.guardian.addLimit')} />
  </fieldset>;
  const title = only ? (adding ? `${t('fguard.rules.addTitle')}: ${ruleKindLabel(only)}` : t('fguard.rule.editLabel', { name: ruleKindLabel(only) })) : t('finance.guardian.editTitle');
  return <Modal title={title} className="formDialog finRulesDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <p className="muted">{t('finance.guardian.editIntro')}</p>
      {shows('category_caps') && <fieldset className="finMapping"><legend>{t('finance.guardian.kind.category_caps')}</legend>
        <p className="muted finHelp">{t('finance.guardian.capsHelp')}</p>
        {draft.caps.map((cap, index) => <div key={index} className="cmFilters finLimitRow">
          <label className="cmInline"><span>{t('finance.col.category')}</span><select value={cap.category} onChange={(e) => set('caps', draft.caps.map((c, i) => (i === index ? { ...c, category: e.target.value as FinanceCategory } : c)))}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
          <label className="cmInline"><span>{t('finance.form.currency')}</span><input value={cap.currency} maxLength={3} dir="ltr" className="finCurrencyInput" onChange={(e) => set('caps', draft.caps.map((c, i) => (i === index ? { ...c, currency: e.target.value.toUpperCase().slice(0, 3) } : c)))} /></label>
          <label className="cmInline"><span>{t('finance.guardian.perMonth')}</span><input value={cap.amount} inputMode="decimal" dir="ltr" onChange={(e) => set('caps', draft.caps.map((c, i) => (i === index ? { ...c, amount: e.target.value } : c)))} /></label>
          <button type="button" className="iconButton" onClick={() => set('caps', draft.caps.filter((_, i) => i !== index))} aria-label={t('finance.guardian.removeCap', { category: categoryLabel(cap.category) })}><Trash2 aria-hidden="true" /></button>
        </div>)}
        <button type="button" className="secondary" disabled={draft.caps.length >= 50} onClick={() => set('caps', [...draft.caps, { category: 'dining', currency: base, amount: '' }])}><Plus aria-hidden="true" />{t('finance.guardian.addCap')}</button>
      </fieldset>}
      {limitSection('max_single', 'maxSingle', t('finance.guardian.singleHelp'))}
      {limitSection('max_daily_total', 'maxDaily', t('finance.guardian.dailyHelp'))}
      {limitSection('approval_above', 'approvalAbove', t('finance.guardian.approvalHelp'))}
      {shows('blocked_categories') && <fieldset className="finMapping"><legend>{t('finance.guardian.kind.blocked_categories')}</legend>
        <div className="finChecks">{CATEGORIES.map((c) => <label key={c} className="finCheck"><input type="checkbox" checked={draft.blockedCategories.includes(c)}
          onChange={(e) => set('blockedCategories', e.target.checked ? [...draft.blockedCategories, c] : draft.blockedCategories.filter((x) => x !== c))} />{categoryLabel(c)}</label>)}</div>
      </fieldset>}
      {shows('blocked_merchants') && <label className="field"><span>{t('finance.guardian.kind.blocked_merchants')}</span>
        <textarea value={draft.blockedMerchants} onChange={(e) => set('blockedMerchants', e.target.value)} rows={3} maxLength={4000} dir="auto" placeholder={t('finance.guardian.merchantsPlaceholder')} /></label>}
      {shows('allowed_currencies') && <label className="field"><span>{t('finance.guardian.kind.allowed_currencies')}</span>
        <input value={draft.allowedCurrencies} onChange={(e) => set('allowedCurrencies', e.target.value.toUpperCase())} dir="ltr" placeholder="AED, USD" /></label>}
      {shows('quiet_hours') && <fieldset className="finMapping"><legend>{t('finance.guardian.kind.quiet_hours')}</legend>
        <label className="finCheck"><input type="checkbox" checked={draft.quietOn} onChange={(e) => set('quietOn', e.target.checked)} />{t('finance.guardian.quietOn')}</label>
        {draft.quietOn && <div className="cmFormGrid">
          <label className="field"><span>{t('finance.guardian.from')}</span><input type="time" value={draft.quietStart} onChange={(e) => set('quietStart', e.target.value)} dir="ltr" /></label>
          <label className="field"><span>{t('finance.guardian.to')}</span><input type="time" value={draft.quietEnd} onChange={(e) => set('quietEnd', e.target.value)} dir="ltr" /></label>
        </div>}
      </fieldset>}
      {shows('cooling_off') && <fieldset className="finMapping"><legend>{t('finance.guardian.kind.cooling_off')}</legend>
        <label className="finCheck"><input type="checkbox" checked={draft.coolingOn} onChange={(e) => set('coolingOn', e.target.checked)} />{t('finance.guardian.coolingOn')}</label>
        {draft.coolingOn && <>
          <label className="field"><span>{t('finance.guardian.hours')}</span><input type="number" min={1} max={168} value={draft.coolingHours} onChange={(e) => set('coolingHours', e.target.value)} dir="ltr" /></label>
          <LimitRows rows={draft.coolingAbove} onChange={(rows) => set('coolingAbove', rows)} label={t('finance.guardian.atOrAbove')} addLabel={t('finance.guardian.addLimit')} />
        </>}
      </fieldset>}
      <label className="field"><span>{t('finance.guardian.reason')}</span><input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} dir="auto" /></label>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} label={t('finance.guardian.reviewChange')} />
    </form>
  </Modal>;
}

function TestDialog({ kind, rules, base, onClose }: { kind: FinanceGuardianRuleKind; rules: FinanceGuardianRules; base: string; onClose: () => void }) {
  const prefill = useMemo(() => testPrefill(rules, kind, base), [rules, kind, base]);
  return <Modal title={t('finance.guardian.check')} className="formDialog" onClose={onClose}>
    <p className="muted">{t('fguard.test.title', { name: ruleKindLabel(kind) })}</p>
    {isPaused(rules, kind) && <p className="notice" role="note"><Info aria-hidden="true" />{t('fguard.test.paused')}</p>}
    <CheckSpend base={base} prefill={prefill} />
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('fguard.close')}</button></div>
  </Modal>;
}

function CheckSpend({ base, prefill }: { base: string; prefill: TestPrefill }) {
  const [amount, setAmount] = useState(prefill.amount), [currency, setCurrency] = useState(prefill.currency || base);
  const [category, setCategory] = useState<FinanceCategory | ''>(prefill.category), [merchant, setMerchant] = useState(prefill.merchant);
  const [result, setResult] = useState<FinanceEvaluation | null>(null), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = parseAmount(amount, currency.toUpperCase(), { signed: false, allowZero: false });
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    try { setResult(await api.evaluateAction({ amount: checked.value, currency: currency.toUpperCase(), category: category || null, merchant: merchant.trim() || null })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const outcome = result?.rules_decision.outcome;
  return <section className="fgCheck" aria-label={t('finance.guardian.check')}>
    <p className="muted">{t('finance.guardian.checkText')}</p>
    <form className="cmFilters finCheckForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <label className="cmInline"><span>{t('finance.col.amount')}</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="cmInline"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" className="finCurrencyInput" /></label>
      <label className="cmInline"><span>{t('finance.col.category')}</span><select value={category} onChange={(e) => setCategory(e.target.value as FinanceCategory | '')}><option value="">{t('finance.guardian.anyCategory')}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
      <label className="cmInline cmSearch"><span>{t('finance.guardian.merchant')}</span><input value={merchant} onChange={(e) => setMerchant(e.target.value)} maxLength={200} dir="auto" /></label>
      <button type="submit" className="secondary" disabled={busy}><Scale aria-hidden="true" />{t('finance.guardian.checkButton')}</button>
    </form>
    <FormError id={errorId} text={error} />
    {result && outcome && <div className={`finVerdict finVerdict-${outcome}`} role="status" aria-live="polite">
      <strong>{t(`finance.guardian.outcome.${outcome}` as MessageKey)}</strong>
      {result.rules_decision.reasons.length === 0 ? <p>{t('finance.guardian.noRuleApplies')}</p> : <ul>{result.rules_decision.reasons.map((r, i) => <li key={`${r.code}-${i}`}>{reasonText(r)}</li>)}</ul>}
      <p className="muted">{t('finance.guardian.checkNote')}</p>
    </div>}
  </section>;
}

// ---- review queue ----------------------------------------------------------------------------------

function ReviewQueue({ queue, tab, month, hasActive, onTab, onMore, onRetry, onAdd, onOpen, onDecided }: {
  queue: Load<FinanceGuardianReviewQueue>; tab: 'awaiting' | 'resolved' | 'snoozed'; month: string | null; hasActive: boolean;
  onTab: (tab: 'awaiting' | 'resolved' | 'snoozed') => void; onMore: () => void; onRetry: () => void; onAdd: () => void;
  onOpen: (tab: FinanceTab) => void; onDecided: Notify;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const data = queue.kind === 'ready' ? queue.value : null;
  const items = data?.items ?? [];
  const current = items.find((i) => i.transaction_id === selected) ?? items[0] ?? null;
  const shownMonth = month ? monthLabel(month) : '';
  const tabs = [
    { id: 'awaiting' as const, count: data?.counts.awaiting }, { id: 'resolved' as const, count: data?.counts.resolved }, { id: 'snoozed' as const, count: data?.counts.snoozed },
  ];
  return <FinCard id="fg-queue" className="fgQueue" title={t('fguard.queue.title')} subtitle={t('fguard.queue.text')}
    actions={data ? <StatusChip tone={data.counts.awaiting ? 'warn' : 'ok'}>{t('fguard.queue.count', { count: data.counts.awaiting })}</StatusChip> : undefined}>
    <div className="fgTabs" role="group" aria-label={t('fguard.queue.tabs')}>
      {tabs.map((item) => <button key={item.id} type="button" aria-pressed={tab === item.id} onClick={() => { onTab(item.id); setSelected(null); }}>
        {t(`fguard.queue.tab.${item.id}` as MessageKey)}{item.count !== undefined && <span className="fgTabCount" dir="ltr">{item.count}</span>}</button>)}
    </div>
    {queue.kind === 'loading' && <Notice busy>{t('fguard.queue.loading')}</Notice>}
    {(queue.kind === 'error' || queue.kind === 'denied') && <LoadError message={t('fguard.queue.error', { message: queue.message })} retry={onRetry} />}
    {data && items.length === 0 && (tab === 'awaiting' && !hasActive
      ? <Empty icon={<ShieldCheck aria-hidden="true" />} title={t('fguard.queue.empty.noRules')} text={t('fguard.queue.empty.noRulesText')}
        action={<button type="button" className="secondary" onClick={onAdd}><Plus aria-hidden="true" />{t('fguard.queue.addRule')}</button>} />
      : <Empty icon={<ListChecks aria-hidden="true" />} title={t(`fguard.queue.empty.${tab}` as MessageKey, { month: shownMonth })}
        text={t(`fguard.queue.empty.${tab}Text` as MessageKey, { month: shownMonth })} />)}
    {current && <FlaggedCard key={current.transaction_id} item={current} onOpen={onOpen} onDecided={(text, error) => { setSelected(null); onDecided(text, error); }} />}
    {items.length > 1 && <div className="fgOthers">
      <h3>{t('fguard.queue.other')}</h3>
      <ul className="cmList">{items.filter((i) => i !== current).map((item) => <li key={item.transaction_id}>
        <button type="button" className="fgOther" onClick={() => setSelected(item.transaction_id)}>
          <AlertTriangle aria-hidden="true" /><span dir="auto">{item.description}</span><span className="fgOtherAmount" dir="ltr">{formatAmount(item.amount, item.currency)}</span><small className="muted">{day(item.posted_on)}</small>
        </button>
      </li>)}</ul>
    </div>}
    {data && nextQueuePage(items.length, data.total) && <button type="button" className="secondary" onClick={onMore}>{t('fguard.queue.more')}</button>}
  </FinCard>;
}

function FlaggedCard({ item, onOpen, onDecided }: { item: FinanceGuardianFlaggedItem; onOpen: (tab: FinanceTab) => void; onDecided: Notify }) {
  const [busy, setBusy] = useState(false), [reviewing, setReviewing] = useState(false), [snoozeOpen, setSnoozeOpen] = useState(false);
  const titleId = useId();
  async function decide(decision: 'expected' | 'reviewed' | 'snoozed' | 'reopen', days?: 1 | 3 | 7 | 30) {
    setBusy(true);
    try {
      const result = await api.decideReview(item.transaction_id, decision, days);
      onDecided(decision === 'snoozed' && result.snoozed_until ? t('fguard.item.done.snoozed', { date: formatDateTime(result.snoozed_until, { dateStyle: 'medium' }) }) : t(`fguard.item.done.${decision}` as MessageKey));
    } catch (e) { onDecided(describeError(e), true); } finally { setBusy(false); setReviewing(false); setSnoozeOpen(false); }
  }
  const amount = money(item.amount, item.currency);
  const stateText = item.state === 'snoozed' && item.snoozed_until ? t('fguard.item.state.snoozed', { date: formatDateTime(item.snoozed_until, { dateStyle: 'medium' }) }) : t(`fguard.item.state.${item.state}` as MessageKey);
  return <article className={`fgItem fgItem-${item.state}`} aria-labelledby={titleId}>
    <header className="fgItemHead">
      <AlertTriangle aria-hidden="true" className="fgItemIcon" />
      <div>
        <h3 id={titleId}>{t('fguard.item.title', { merchant: `⁨${item.description}⁩`, amount })}</h3>
        <p className="fgWhy"><strong>{t('fguard.item.why')}:</strong> {item.reasons.map(reasonText).join(' ')}</p>
        <p className="muted fgTypical">{item.typical_amount ? t('fguard.item.typical', { amount: money(item.typical_amount, item.currency), count: item.prior_count }) : t('fguard.item.noTypical')}</p>
      </div>
      <StatusChip tone={item.state === 'awaiting' ? 'warn' : item.state === 'snoozed' ? 'muted' : 'ok'}>{stateText}</StatusChip>
    </header>
    <div className="fgItemBody">
      <dl className="fgFacts" aria-label={t('fguard.item.details')}>
        <div><dt><Store aria-hidden="true" />{t('fguard.item.merchant')}</dt><dd dir="auto">{item.description}</dd></div>
        <div><dt><Banknote aria-hidden="true" />{t('fguard.item.amount')}</dt><dd>{amount}</dd></div>
        <div><dt><CalendarDays aria-hidden="true" />{t('fguard.item.posted')}</dt><dd>{day(item.posted_on)}</dd></div>
        <div><dt><Coins aria-hidden="true" />{t('fguard.item.account')}</dt><dd dir="auto">{item.account_name ?? '-'}</dd></div>
        <div><dt><PieChart aria-hidden="true" />{t('fguard.item.category')}</dt><dd>{categoryLabel(item.category)}</dd></div>
        <div><dt><ListChecks aria-hidden="true" />{t('fguard.item.prior')}</dt><dd dir="ltr">{item.prior_count}</dd></div>
      </dl>
      <div className="fgTimeline">
        <h4><Clock aria-hidden="true" />{t('fguard.item.timeline')}</h4>
        <ol>
          <li className="fgDone"><strong>{t('fguard.item.recorded')}</strong><small className="muted">{formatDateTime(item.recorded_at)}</small></li>
          <li className={item.decided_at ? 'fgDone' : ''}><strong>{t('fguard.item.decision')}</strong>
            <small className="muted">{item.decided_at ? `${stateText}, ${formatDateTime(item.decided_at)}` : t('fguard.item.notYet')}</small></li>
        </ol>
      </div>
    </div>
    <p className="fgNote" role="note"><Info aria-hidden="true" />{t('fguard.item.note')}</p>
    <div className="actions fgItemActions">
      {item.state === 'awaiting' ? <>
        <button type="button" disabled={busy} onClick={() => setReviewing(true)}><Search aria-hidden="true" />{t('fguard.item.review')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => void decide('expected')}><Check aria-hidden="true" />{t('fguard.item.expected')}</button>
        <div className="fgSnooze">
          <button type="button" className="secondary" disabled={busy} aria-expanded={snoozeOpen} onClick={() => setSnoozeOpen((o) => !o)}><Clock aria-hidden="true" />{t('fguard.item.snooze')}</button>
          {snoozeOpen && <ul className="fgMenu">{SNOOZE_DAYS.map((days) => <li key={days}>
            <button type="button" className="secondary" onClick={() => void decide('snoozed', days)}>{t('fguard.item.snoozeFor', { days: t('fguard.item.days', { count: days }) })}</button></li>)}</ul>}
        </div>
      </> : <button type="button" className="secondary" disabled={busy} onClick={() => void decide('reopen')}><RotateCcw aria-hidden="true" />{t('fguard.item.reopen')}</button>}
    </div>
    {reviewing && <Modal title={t('fguard.item.reviewTitle')} className="formDialog" onClose={() => setReviewing(false)}>
      <p>{t('fguard.item.title', { merchant: `⁨${item.description}⁩`, amount })}</p>
      <ul className="finRules">{item.reasons.map((r, i) => <li key={`${r.code}-${i}`}><AlertTriangle aria-hidden="true" />{reasonText(r)}</li>)}</ul>
      <p className="muted">{t('fguard.item.reviewText')}</p>
      <div className="actions">
        <button type="button" disabled={busy} onClick={() => void decide('reviewed')}><Check aria-hidden="true" />{t('fguard.item.markReviewed')}</button>
        <button type="button" className="secondary" onClick={() => onOpen('transactions')}><ExternalLink aria-hidden="true" />{t('fguard.item.openTransactions')}</button>
        <button type="button" className="secondary" onClick={() => setReviewing(false)}>{t('common.cancel')}</button>
      </div>
    </Modal>}
  </article>;
}

function alertText(alert: FinanceGuardianAlerts['alerts'][number]): string {
  const p = alert.params;
  const amount = (value: string | undefined) => isolate(formatAmount(value ?? '0', p.currency ?? 'AED'));
  switch (alert.code) {
    case 'cap_over': case 'cap_near':
      return t(`finance.guardian.alert.${alert.code}`, { category: categoryLabel(p.category as FinanceCategory), spent: amount(p.spent), limit: amount(p.limit) });
    case 'merchant_blocked_seen': return t('finance.guardian.alert.merchant', { count: Number(p.count), merchant: p.merchant ?? '' });
    case 'category_blocked_seen': return t('finance.guardian.alert.category', { count: Number(p.count), category: categoryLabel(p.category as FinanceCategory) });
    case 'daily_over': return t('finance.guardian.alert.daily', { count: Number(p.count), limit: amount(p.limit) });
    default: return t('finance.guardian.alert.single', { count: Number(p.count), limit: amount(p.limit) });
  }
}

function AlertsSummary({ alerts, hasRules, retry }: { alerts: Load<FinanceGuardianAlerts>; hasRules: boolean; retry: () => void }) {
  return <FinCard id="fin-alerts" className="fgAlerts" icon={<AlertTriangle />} title={t('finance.guardian.alerts')}>
    {alerts.kind === 'loading' && <Notice busy>{t('finance.guardian.loading')}</Notice>}
    {(alerts.kind === 'error' || alerts.kind === 'denied') && <LoadError message={alerts.message} retry={retry} />}
    {alerts.kind === 'ready' && (!hasRules ? <p className="muted">{t('finance.guardian.alertsNoRules')}</p>
      : alerts.value.alerts.length === 0 ? <p className="muted">{t('finance.guardian.alertsNone')}</p>
        : <ul className="cmList finAlerts">{alerts.value.alerts.map((alert, index) => <li key={`${alert.code}-${index}`} className={alert.severity === 'over' ? 'finAlertOver' : 'finAlertNear'}>
          <div><strong>{alertText(alert)}</strong><small>{ruleKindLabel(alert.rule)}</small></div>
          <span className={`statusBadge ${alert.severity === 'over' ? 'warning' : 'neutral'}`}>{t(`finance.guardian.severity.${alert.severity}` as MessageKey)}</span>
        </li>)}</ul>)}
  </FinCard>;
}

// ---- delivery, quiet hours, audit log --------------------------------------------------------------

function Delivery() {
  const rows: { id: string; icon: ReactNode; label: string; state: string; tone: string; note: string }[] = [
    { id: 'inApp', icon: <Bell aria-hidden="true" />, label: t('fguard.delivery.inApp'), state: t('fguard.delivery.inAppState'), tone: 'success', note: t('fguard.delivery.inAppNote') },
    { id: 'email', icon: <Mail aria-hidden="true" />, label: t('fguard.delivery.email'), state: t('fguard.delivery.unavailable'), tone: 'neutral', note: t('fguard.delivery.emailNote') },
    { id: 'sms', icon: <MessageSquare aria-hidden="true" />, label: t('fguard.delivery.sms'), state: t('fguard.delivery.unavailable'), tone: 'neutral', note: t('fguard.delivery.smsNote') },
  ];
  return <div className="fgDeliveryList">
    <h3 id="fg-delivery">{t('fguard.delivery.title')}</h3>
    <ul aria-labelledby="fg-delivery">{rows.map((row) => <li key={row.id}>
      <span className="fgDeliveryIcon">{row.icon}</span>
      <div><strong>{row.label}</strong><small className="muted">{row.note}</small></div>
      <StatusChip tone={row.tone === 'success' ? 'ok' : 'muted'}>{row.state}</StatusChip>
    </li>)}</ul>
  </div>;
}

function QuietHours({ rules, timezone, onEdit }: { rules: FinanceGuardianRules; timezone: string | null; onEdit: () => void }) {
  const quiet = rules.quiet_hours;
  const paused = isPaused(rules, 'quiet_hours');
  return <div className="fgQuiet">
    <h3 id="fg-quiet"><Moon aria-hidden="true" className="cmInlineIcon" />{t('fguard.quiet.title')}</h3>
    <p className="muted">{t('fguard.quiet.text')}</p>
    <p className="fgQuietWindow">
      <strong>{quiet ? t('fguard.quiet.window', { start: isolate(quiet.start), end: isolate(quiet.end) }) : t('fguard.quiet.none')}</strong>
      {quiet && <StatusChip tone={paused ? 'muted' : 'ok'}>{paused ? t('fguard.rule.paused') : t('fguard.rule.enabled')}</StatusChip>}
    </p>
    {timezone && <p className="muted">{t('fguard.quiet.zone', { zone: isolate(timezone) })}</p>}
    <button type="button" className="secondary" onClick={onEdit}><Pencil aria-hidden="true" />{quiet ? t('fguard.quiet.edit') : t('fguard.quiet.set')}</button>
  </div>;
}

function useActivity(limit: number) {
  const [items, setItems] = useState<FinanceGuardianActivity[]>([]), [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [message, setMessage] = useState('');
  const load = useCallback(async (after: string | null) => {
    try {
      const page = await api.guardianActivity(limit, after);
      setItems((current) => (after ? [...current, ...page.items] : page.items)); setCursor(page.next_cursor); setState('ready');
    } catch (e) { setMessage(describeError(e)); setState('error'); }
  }, [limit]);
  useEffect(() => { void load(null); }, [load]);
  return { items, cursor, state, message, load };
}

function AuditTable({ items, timezone }: { items: FinanceGuardianActivity[]; timezone: string | null }) {
  const zone = timezone ?? undefined;
  return <div className="finTableWrap"><table className="finGrid fgTable">
    <caption className="visuallyHidden">{t('fguard.audit.caption')}</caption>
    <thead><tr>
      <th scope="col">{t('fguard.audit.col.time', { zone: isolate(timezone ?? 'UTC') })}</th><th scope="col">{t('fguard.audit.col.actor')}</th>
      <th scope="col">{t('fguard.audit.col.rule')}</th><th scope="col">{t('fguard.audit.col.action')}</th>
    </tr></thead>
    <tbody>{items.map((item) => {
      const cells = activityCells(item);
      return <tr key={item.id}>
        <td>{formatDateTime(item.created_at, { dateStyle: 'medium', timeStyle: 'short', timeZone: zone })}</td>
        <td>{t('fguard.audit.you')}</td>
        <td>{cells.rule}</td>
        <td><span className={`fgAction fgAction-${cells.tone}`}>{cells.tone === 'warn' ? <AlertTriangle aria-hidden="true" /> : cells.tone === 'ok' ? <Check aria-hidden="true" /> : <Clock aria-hidden="true" />}{cells.action}
          {cells.loosens && <StatusChip tone="warn">{t('fguard.audit.loosens')}</StatusChip>}</span></td>
      </tr>;
    })}</tbody>
  </table></div>;
}

function AuditLog({ timezone }: { timezone: string | null }) {
  const { items, state, message, load } = useActivity(AUDIT_PAGE);
  const [full, setFull] = useState(false);
  return <FinCard id="fg-audit" className="fgAudit" icon={<ListChecks />} title={t('fguard.audit.title')} subtitle={t('fguard.audit.text')}
    actions={state === 'ready' && items.length > 0 ? <button type="button" className="secondary" onClick={() => setFull(true)}><ExternalLink aria-hidden="true" />{t('fguard.audit.full')}</button> : undefined}>
    {state === 'loading' && <Notice busy>{t('finance.guardian.loading')}</Notice>}
    {state === 'error' && <LoadError message={t('fguard.audit.error', { message })} retry={() => void load(null)} />}
    {state === 'ready' && (items.length === 0
      ? <Empty icon={<ListChecks aria-hidden="true" />} title={t('fguard.audit.empty')} text={t('fguard.audit.emptyText')} />
      : <AuditTable items={items} timezone={timezone} />)}
    {full && <FullLog timezone={timezone} onClose={() => setFull(false)} />}
  </FinCard>;
}

function FullLog({ timezone, onClose }: { timezone: string | null; onClose: () => void }) {
  const { items, cursor, state, message, load } = useActivity(25);
  return <Modal title={t('fguard.audit.fullTitle')} className="formDialog fgFullLog" onClose={onClose}>
    {state === 'loading' && <Notice busy>{t('finance.guardian.loading')}</Notice>}
    {state === 'error' && <LoadError message={t('fguard.audit.error', { message })} retry={() => void load(null)} />}
    {state === 'ready' && (items.length === 0 ? <p className="muted">{t('fguard.audit.emptyText')}</p> : <AuditTable items={items} timezone={timezone} />)}
    <div className="actions">
      {cursor && <button type="button" className="secondary" onClick={() => void load(cursor)}>{t('fguard.audit.more')}</button>}
      <button type="button" className="secondary" onClick={onClose}>{t('fguard.close')}</button>
    </div>
  </Modal>;
}

