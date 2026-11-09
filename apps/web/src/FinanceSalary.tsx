// Finance, Money advisor, Salary (docs/finance.md#salary): the salary profile, the month by month check of the
// salary against what was expected, and the payslips. Detection runs on the server over every source on the salary
// account (statement, SMS, typed); here the person confirms or rejects each suggested month. Every figure is the
// API's; nothing here moves money.
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { BadgeCheck, Briefcase, CalendarClock, Pencil, Plus, Trash2, Wallet, X } from 'lucide-react';
import type {
  FinanceAccount, FinanceSalaryDeductionReason, FinanceSalaryMonth, FinanceSalaryMonthsPage, FinanceSalaryProfile, FinanceSalaryRuleInput,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import * as pm from './lib/financePersonalApi';
import { formatAmount, isolate, monthLabel } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';
import { PayslipsSection } from './FinancePersonalMoney';
import './financePersonal.css';

const REASONS: readonly FinanceSalaryDeductionReason[] = ['social_insurance', 'loan', 'absence', 'other'];
const PAGE = 12;
const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const shortDay = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'long', timeZone: 'UTC' });
const amount = (value: string, currency: string) => isolate(formatAmount(value, currency));
const STATUS_TONE: Record<FinanceSalaryMonth['status'], 'ok' | 'warn' | 'bad' | 'info' | 'muted'> = {
  received: 'ok', late: 'warn', partial: 'warn', missing: 'bad', upcoming: 'muted',
};

export function SalarySection({ base }: { base: string }) {
  const [profile, setProfile] = useState<Load<FinanceSalaryProfile | null>>({ kind: 'loading' });
  const [months, setMonths] = useState<Load<FinanceSalaryMonthsPage>>({ kind: 'loading' });
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState(false), [removing, setRemoving] = useState(false), [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const loadProfile = useCallback(() => {
    setProfile((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    pm.getSalaryProfile().then((v) => setProfile({ kind: 'ready', value: v.profile })).catch((e) => setProfile(failed(e)));
  }, []);
  const loadMonths = useCallback(() => {
    setMonths((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    pm.salaryMonths(PAGE, offset).then((v) => setMonths({ kind: 'ready', value: v })).catch((e) => setMonths(failed(e)));
  }, [offset]);
  useEffect(() => { loadProfile(); }, [loadProfile]);
  useEffect(() => { loadMonths(); }, [loadMonths]);
  const changed = (text: string) => { setNotice({ text }); loadProfile(); loadMonths(); };
  const decide = async (row: FinanceSalaryMonth, decision: 'confirm' | 'reject' | 'undo') => {
    if (!row.found) return;
    setBusy(true); setNotice(null);
    try {
      if (decision === 'undo') await pm.undoSalaryMonth(row.month, row.found.transaction_id);
      else await pm.decideSalaryMonth(row.month, row.found.transaction_id, decision);
      changed(t(`fsal.decided.${decision}` as MessageKey, { month: monthLabel(row.month) }));
    } catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  const remove = async () => {
    setBusy(true);
    try { await pm.removeSalaryProfile(); setRemoving(false); changed(t('fsal.removed')); } catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };

  if (profile.kind === 'loading') return <Notice busy>{t('fsal.loading')}</Notice>;
  if (profile.kind === 'denied') return <Denied />;
  if (profile.kind === 'error') return <LoadError message={t('fsal.error', { message: profile.message })} retry={loadProfile} />;
  const p = profile.value;
  return <div className="fpmPage fsalPage">
    <p className="muted fpmIntro">{t('fsal.intro')}</p>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {!p ? <FinCard title={t('fsal.profile.title')} icon={<Briefcase />}>
      <Empty icon={<Briefcase aria-hidden="true" />} title={t('fsal.empty.title')} text={t('fsal.empty.text')}
        action={<button type="button" onClick={() => setEditing(true)}>{t('fsal.add')}</button>} />
    </FinCard> : <>
      <KpiRow label={t('fsal.kpis')}>
        <KpiCard icon={<Wallet />} label={t('fsal.kpi.net')} value={<Money value={p.expected_net} currency={p.currency} />}
          hint={t('fsal.kpi.gross', { amount: amount(p.expected_gross, p.currency) })} />
        <KpiCard icon={<CalendarClock />} label={t('fsal.kpi.next')} value={day(p.next_pay_on)} hint={t('fsal.kpi.payDay', { day: p.pay_day })} />
        <KpiCard icon={<BadgeCheck />} label={t('fsal.kpi.waiting')} value={months.kind === 'ready' ? months.value.waiting : '-'}
          tone={months.kind === 'ready' && months.value.waiting > 0 ? 'warn' : ''} hint={t('fsal.kpi.waitingHint')} />
        <KpiCard icon={<X />} label={t('fsal.kpi.missing')} value={months.kind === 'ready' ? months.value.missing : '-'}
          tone={months.kind === 'ready' && months.value.missing > 0 ? 'bad' : ''} hint={t('fsal.kpi.missingHint')} />
      </KpiRow>
      <FinCard title={t('fsal.profile.title')} icon={<Briefcase />} actions={<div className="finCardButtons">
        <button type="button" className="secondary" onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('fsal.edit')}</button>
        <button type="button" className="secondary" onClick={() => setRemoving(true)}><Trash2 aria-hidden="true" />{t('fsal.remove')}</button></div>}>
        <dl className="fsalFacts">
          <div><dt>{t('fsal.employer')}</dt><dd dir="auto">{p.employer}</dd></div>
          <div><dt>{t('fsal.joined')}</dt><dd>{day(p.joined_on)}</dd></div>
          <div><dt>{t('fsal.gross')}</dt><dd><Money value={p.expected_gross} currency={p.currency} /></dd></div>
          <div><dt>{t('fsal.payDay')}</dt><dd>{p.pay_day}</dd></div>
        </dl>
        {p.rules.length > 0 && <ul className="finMoveList fsalRules" aria-label={t('fsal.rules')}>{p.rules.map((r) => {
          const advance = p.advances.find((a) => a.rule_id === r.id);
          return <li key={r.id}><div><span dir="auto">{r.label || t(`fsal.reason.${r.kind === 'advance' ? 'advance' : r.reason}` as MessageKey)}</span>
            <small>{r.kind === 'advance' && advance ? t('fsal.rule.advance', { total: amount(advance.total, p.currency), left: amount(advance.left, p.currency), from: monthLabel(r.starts) })
              : r.ends ? t('fsal.rule.until', { from: monthLabel(r.starts), to: monthLabel(r.ends) }) : t('fsal.rule.from', { from: monthLabel(r.starts) })}</small></div>
            <Money value={r.amount} currency={p.currency} /></li>;
        })}</ul>}
      </FinCard>
      <MonthsCard state={months} retry={loadMonths} busy={busy} offset={offset} onOffset={setOffset} onDecide={(row, d) => void decide(row, d)} />
    </>}
    <PayslipsSection base={p?.currency ?? base} />
    {editing && <Modal title={t(p ? 'fsal.edit' : 'fsal.add')} className="formDialog" onClose={() => setEditing(false)}>
      <ProfileForm profile={p} base={base} onDone={(text) => { setEditing(false); if (text) changed(text); }} />
    </Modal>}
    {removing && <ConfirmDialog title={t('fsal.remove')} confirmLabel={t('fsal.remove')} danger busy={busy} onConfirm={() => void remove()} onCancel={() => setRemoving(false)}>
      <p>{t('fsal.removeText')}</p></ConfirmDialog>}
  </div>;
}

function MonthsCard({ state, retry, busy, offset, onOffset, onDecide }: {
  state: Load<FinanceSalaryMonthsPage>; retry: () => void; busy: boolean; offset: number; onOffset: (next: number) => void;
  onDecide: (row: FinanceSalaryMonth, decision: 'confirm' | 'reject' | 'undo') => void;
}) {
  if (state.kind === 'loading') return <Notice busy>{t('fsal.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={t('fsal.error', { message: state.message })} retry={retry} />;
  const page = state.value;
  return <FinCard title={t('fsal.months.title')} icon={<CalendarClock />} subtitle={t('fsal.months.sub')}>
    {page.months.length === 0 ? <p className="muted">{t('fsal.months.none')}</p>
      : <ul className="fsalMonths">{page.months.map((m) => <li key={m.month} className="fsalMonth">
        <div className="fsalMonthHead"><strong>{monthLabel(m.month)}</strong><StatusChip tone={STATUS_TONE[m.status]}>{t(`fsal.status.${m.status}` as MessageKey)}</StatusChip>
          {m.expected.payslip && <StatusChip tone="info">{t('fsal.payslip')}</StatusChip>}</div>
        <dl className="fsalFigures">
          <div><dt>{t('fsal.expected')}</dt><dd><Money value={m.expected.net} currency={m.currency} /></dd></div>
          <div><dt>{t('fsal.received')}</dt><dd><Money value={m.received} currency={m.currency} /></dd></div>
          {m.found && <div><dt>{t('fsal.difference')}</dt><dd><Money value={m.difference} currency={m.currency} signed /></dd></div>}
        </dl>
        {m.status === 'missing' && <p className="fsalFlag" role="note">{t('fsal.missing', { month: monthLabel(m.month), date: shortDay(m.pay_on) })}</p>}
        {m.found && <div className="fsalFound">
          <p><span dir="auto">{m.found.description}</span> <span className="muted">{day(m.found.posted_on)}</span>
            {m.basis === 'suggested' && <> <StatusChip tone={m.found.confidence === 'high' ? 'ok' : m.found.confidence === 'medium' ? 'info' : 'warn'}>
              {t(`fsal.confidence.${m.found.confidence}` as MessageKey)}</StatusChip></>}
            {m.basis !== 'suggested' && <> <StatusChip tone="ok">{t(`fsal.basis.${m.basis}` as MessageKey)}</StatusChip></>}</p>
          <div className="finCardButtons">{m.basis === 'suggested' ? <>
            <button type="button" disabled={busy} onClick={() => onDecide(m, 'confirm')}>{t('fsal.confirm')}</button>
            <button type="button" className="secondary" disabled={busy} onClick={() => onDecide(m, 'reject')}>{t('fsal.reject')}</button></>
            : m.basis === 'confirmed' && <button type="button" className="secondary" disabled={busy} onClick={() => onDecide(m, 'undo')}>{t('fsal.undo')}</button>}</div>
        </div>}
        {m.rejected > 0 && <p className="muted fsalNote">{t('fsal.rejectedCount', { count: m.rejected })}</p>}
      </li>)}</ul>}
    {page.total > PAGE && <div className="actions fpmPager">
      <button type="button" className="secondary" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - PAGE))}>{t('fpm.previous')}</button>
      <span>{t('fpm.page', { page: Math.floor(offset / PAGE) + 1, pages: Math.ceil(page.total / PAGE) })}</span>
      <button type="button" className="secondary" disabled={offset + PAGE >= page.total} onClick={() => onOffset(offset + PAGE)}>{t('fpm.next')}</button>
    </div>}
  </FinCard>;
}

interface RuleDraft extends FinanceSalaryRuleInput { key: number }

function ProfileForm({ profile, base, onDone }: { profile: FinanceSalaryProfile | null; base: string; onDone: (text: string | null) => void }) {
  const thisMonth = new Date().toISOString().slice(0, 7);
  const [employer, setEmployer] = useState(profile?.employer ?? ''), [joined, setJoined] = useState(profile?.joined_on ?? '');
  const [currency, setCurrency] = useState(profile?.currency ?? base), [gross, setGross] = useState(profile?.expected_gross ?? '');
  const [payDay, setPayDay] = useState(String(profile?.pay_day ?? 27)), [accountId, setAccountId] = useState(profile?.account_id ?? '');
  const [rules, setRules] = useState<RuleDraft[]>((profile?.rules ?? []).map((r, i) => ({ ...r, key: i })));
  const [accounts, setAccounts] = useState<FinanceAccount[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  useEffect(() => { api.listAccounts().then(setAccounts).catch(() => setAccounts([])); }, []);
  const setRule = (key: number, patch: Partial<RuleDraft>) => setRules(rules.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const addRule = (kind: 'deduction' | 'advance') => setRules([...rules, { key: Date.now(), kind, reason: kind === 'advance' ? 'advance' : 'social_insurance', amount: '', starts: thisMonth,
    ends: null, total: kind === 'advance' ? '' : null, received_on: null, label: null }]);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      await pm.saveSalaryProfile({ employer: employer.trim(), joined_on: joined, currency, expected_gross: gross.trim(), pay_day: Number(payDay), account_id: accountId || null,
        rules: rules.filter((r) => r.amount.trim()).map(({ key: _key, ...r }) => ({ ...r, amount: r.amount.trim(), total: r.total?.trim() || null, label: r.label?.trim() || null,
          ends: r.ends || null, received_on: r.received_on || null })) });
      onDone(t('fsal.saved'));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const usable = accounts.filter((a) => !a.archived && (a.kind === 'bank' || a.kind === 'cash') && a.currency === currency);
  return <form className="finForm" onSubmit={(e) => void submit(e)}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('fsal.employer')}</span><input value={employer} onChange={(e) => setEmployer(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('fsal.joined')}</span><input type="date" value={joined} onChange={(e) => setJoined(e.target.value)} required /></label>
      <label className="field"><span>{t('fpm.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('fsal.gross')}</span><input value={gross} onChange={(e) => setGross(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('fsal.payDay')}</span><input type="number" min={1} max={31} value={payDay} onChange={(e) => setPayDay(e.target.value)} dir="ltr" required /></label>
      <label className="field"><span>{t('fsal.account')}</span><select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
        <option value="">{t('fsal.account.any', { currency })}</option>
        {usable.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
    </div>
    <fieldset className="fpmDeductions"><legend>{t('fsal.rules')}</legend>
      <p className="muted finHelp">{t('fsal.rules.help')}</p>
      {rules.map((r) => <div key={r.key} className="fpmDeduction fsalRule">
        {r.kind === 'deduction'
          ? <label className="field"><span>{t('fsal.rule.reason')}</span><select value={r.reason} onChange={(e) => setRule(r.key, { reason: e.target.value as FinanceSalaryDeductionReason })}>
            {REASONS.map((k) => <option key={k} value={k}>{t(`fsal.reason.${k}` as MessageKey)}</option>)}</select></label>
          : <label className="field"><span>{t('fsal.rule.total')}</span><input value={r.total ?? ''} onChange={(e) => setRule(r.key, { total: e.target.value })} inputMode="decimal" dir="ltr" required /></label>}
        <label className="field"><span>{t(r.kind === 'advance' ? 'fsal.rule.repay' : 'fsal.rule.amount')}</span><input value={r.amount} onChange={(e) => setRule(r.key, { amount: e.target.value })} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('fsal.rule.starts')}</span><input type="month" value={r.starts} onChange={(e) => setRule(r.key, { starts: e.target.value })} required /></label>
        {r.kind === 'deduction'
          ? <label className="field"><span>{t('fsal.rule.ends')}</span><input type="month" value={r.ends ?? ''} onChange={(e) => setRule(r.key, { ends: e.target.value || null })} /></label>
          : <label className="field"><span>{t('fsal.rule.received')}</span><input type="date" value={r.received_on ?? ''} onChange={(e) => setRule(r.key, { received_on: e.target.value || null })} /></label>}
        <label className="field"><span>{t('fpm.pay.label')}</span><input value={r.label ?? ''} onChange={(e) => setRule(r.key, { label: e.target.value })} maxLength={80} dir="auto" /></label>
        <button type="button" className="secondary iconOnly" onClick={() => setRules(rules.filter((x) => x.key !== r.key))} aria-label={t('fsal.rule.remove')}><Trash2 aria-hidden="true" /></button>
      </div>)}
      <div className="finCardButtons">
        <button type="button" className="secondary" onClick={() => addRule('deduction')} disabled={rules.length >= 30}><Plus aria-hidden="true" />{t('fsal.rule.addDeduction')}</button>
        <button type="button" className="secondary" onClick={() => addRule('advance')} disabled={rules.length >= 30}><Plus aria-hidden="true" />{t('fsal.rule.addAdvance')}</button>
      </div>
    </fieldset>
    {error && <p className="formError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy}>{t('fpm.save')}</button><button type="button" className="secondary" onClick={() => onDone(null)}>{t('fpm.cancel')}</button></div>
  </form>;
}
