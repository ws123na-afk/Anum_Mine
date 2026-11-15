// Finance, Bills and budgets, Recurring: the recurring payments found in the records and the ones the owner keeps
// (docs/finance.md#recurring-payments). Each found payment says what kind it is (rent, phone, streaming, a loan, money
// home) and whether its price moved; ordinary shopping is never listed. Keep turns it into a bill, optionally letting a
// later payment link itself when it is the one strong match; Not a bill dismisses it (restored under Suggestions).
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Link2, Plus, Repeat, X } from 'lucide-react';
import type { FinanceKeptBill, FinanceRecurringFound, FinanceRecurringKind, FinanceRecurringView, FinanceSubscriptionUsage } from '@anum/contracts';
import * as b from './lib/financeBudgetsApi';
import { priceChangeText } from './lib/financeBudgets';
import { categoryLabel, formatAmount, isolate } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Modal } from './Dialog';
import { Denied, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import './financeBudgets.css';

export const RECURRING_KINDS: readonly FinanceRecurringKind[] = ['rent', 'phone', 'internet', 'streaming', 'subscription', 'insurance', 'school_fees', 'loan',
  'remittance', 'membership', 'utility', 'installment', 'other'];
const USAGES: readonly (FinanceSubscriptionUsage | 'unknown')[] = ['unknown', 'often', 'sometimes', 'rarely'];
const longDate = (day: string) => formatDateTime(`${day}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

export function RecurringPayments({ onChanged }: { onChanged: () => void }) {
  const [view, setView] = useState<Load<FinanceRecurringView>>({ kind: 'loading' });
  const [keeping, setKeeping] = useState<FinanceRecurringFound | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => { try { setView({ kind: 'ready', value: await b.recurring() }); } catch (e) { setView(failed(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  async function reject(found: FinanceRecurringFound) {
    setBusy(found.key);
    try { await b.rejectRecurring(found.key); setFlash({ text: t('frec.found.rejected', { name: found.name }) }); await load(); }
    catch (e) { setFlash({ error: true, text: describeError(e) }); } finally { setBusy(null); }
  }
  async function settings(bill: FinanceKeptBill, patch: { auto_link?: boolean; usage?: FinanceSubscriptionUsage | 'unknown' }) {
    setBusy(bill.id);
    try { await b.recurringSettings(bill.id, patch); setFlash({ text: t('frec.kept.saved', { name: bill.name }) }); await load(); }
    catch (e) { setFlash({ error: true, text: describeError(e) }); } finally { setBusy(null); }
  }
  async function linkNow() {
    setBusy('link');
    try { const done = await b.linkRecurringNow(); setFlash({ text: t('frec.kept.linked', { linked: done.linked, left: done.left }) }); onChanged(); await load(); }
    catch (e) { setFlash({ error: true, text: describeError(e) }); } finally { setBusy(null); }
  }
  if (view.kind === 'loading') return <Notice busy>{t('fplan.loading')}</Notice>;
  if (view.kind === 'denied') return <Denied />;
  if (view.kind === 'error') return <LoadError message={view.message} retry={() => void load()} />;
  const { found, kept, dismissed } = view.value;
  const recurringKept = kept.filter((k) => k.active);
  return <>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <FinCard title={t('frec.found.title')} subtitle={t('frec.found.subtitle')} icon={<Repeat />}>
      {found.length === 0 ? <p className="muted">{t('frec.found.empty')}</p>
        : <ul className="cmList frecList">{found.map((f) => <li key={f.key}>
          <div><strong dir="auto">{f.name}</strong>
            <small>{t(`frec.kind.${f.kind ?? 'other'}` as MessageKey)} · {t(`finance.cadence.${f.cadence}` as MessageKey)} · {t('frec.found.seen', { count: f.occurrences })} · {t('finance.bills.next', { date: isolate(f.next_due_on) })}</small>
            {f.price_change && <StatusChip tone="warn">{t('frec.found.priceChange', { amount: isolate(priceChangeText(f.price_change, f.currency)) })}</StatusChip>}</div>
          <div className="finItemEnd"><Money value={f.last_amount ?? f.amount} currency={f.currency} />
            <button type="button" className="secondary" onClick={() => setKeeping(f)}><Plus aria-hidden="true" />{t('frec.found.keep')}</button>
            <button type="button" className="secondary" disabled={busy === f.key} onClick={() => void reject(f)}><X aria-hidden="true" />{t('frec.found.reject')}</button></div>
        </li>)}</ul>}
      {dismissed > 0 && <p className="muted">{t('frec.found.dismissed', { count: dismissed })}</p>}
    </FinCard>
    <FinCard title={t('frec.kept.title')} icon={<Link2 />} actions={recurringKept.some((k) => k.auto_link)
      ? <button type="button" className="secondary" disabled={busy === 'link'} onClick={() => void linkNow()}><Link2 aria-hidden="true" />{t('frec.kept.linkNow')}</button> : undefined}>
      {recurringKept.length === 0 ? <p className="muted">{t('finance.bills.emptyText')}</p>
        : <ul className="cmList frecList">{recurringKept.map((k) => <KeptRow key={k.id} bill={k} busy={busy === k.id} onSettings={(patch) => void settings(k, patch)} />)}</ul>}
    </FinCard>
    {keeping && <KeepDialog found={keeping} onClose={(name) => { setKeeping(null); if (name) { setFlash({ text: t('finance.bills.kept', { name }) }); onChanged(); void load(); } }} />}
  </>;
}

function KeptRow({ bill, busy, onSettings }: { bill: FinanceKeptBill; busy: boolean; onSettings: (patch: { auto_link?: boolean; usage?: FinanceSubscriptionUsage | 'unknown' }) => void }) {
  const usageId = useId();
  return <li>
    <div><strong dir="auto">{bill.name}</strong>
      <small>{bill.recurring_kind ? t(`frec.kind.${bill.recurring_kind}` as MessageKey) : categoryLabel(bill.category)} · {t(`finance.cadence.${bill.cadence}` as MessageKey)}
        {' · '}{bill.last_paid_on && bill.last_amount ? t('frec.kept.lastPaid', { date: isolate(longDate(bill.last_paid_on)), amount: isolate(formatAmount(bill.last_amount, bill.currency)) })
          : t('frec.kept.notSeen')}</small>
      <span className="fbudChips">
        <StatusChip tone={bill.auto_link ? 'info' : 'muted'}>{bill.auto_link ? t('frec.kept.autoLink') : t('frec.kept.manual')}</StatusChip>
        {bill.price_change && <StatusChip tone="warn">{t('frec.kept.priceChange', { amount: isolate(priceChangeText(bill.price_change, bill.currency)) })}</StatusChip>}
        {bill.still_billing_rarely_used && <StatusChip tone="bad">{t('frec.kept.rarely')}</StatusChip>}
      </span></div>
    <div className="finItemEnd"><Money value={bill.amount} currency={bill.currency} />
      <label className="visuallyHidden" htmlFor={usageId}>{t('frec.kept.usageFor', { name: bill.name })}</label>
      <select id={usageId} value={bill.usage ?? 'unknown'} disabled={busy} onChange={(e) => onSettings({ usage: e.target.value as FinanceSubscriptionUsage | 'unknown' })}>
        {USAGES.map((u) => <option key={u} value={u}>{t(`frec.usage.${u}` as MessageKey)}</option>)}</select>
      <button type="button" className="secondary" disabled={busy || (!bill.auto_link && !bill.merchant && !bill.account_id)} onClick={() => onSettings({ auto_link: !bill.auto_link })}>
        {bill.auto_link ? t('frec.kept.autoOff') : t('frec.kept.autoOn')}</button></div>
  </li>;
}

function KeepDialog({ found, onClose }: { found: FinanceRecurringFound; onClose: (name: string | null) => void }) {
  const [name, setName] = useState(found.name.slice(0, 120));
  const [kind, setKind] = useState<FinanceRecurringKind>(found.kind ?? 'other');
  const [autoLink, setAutoLink] = useState(Boolean(found.merchant));
  const [usage, setUsage] = useState<FinanceSubscriptionUsage | 'unknown'>('unknown');
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const ids = { name: useId(), kind: useId(), usage: useId(), hint: useId() };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('fbud.form.needName')); return; }
    setBusy(true); setError(null);
    try {
      await b.confirmRecurring({ key: found.key, name: name.trim(), amount: found.last_amount ?? found.amount, currency: found.currency, cadence: found.cadence,
        next_due_on: found.next_due_on, category: found.category, account_id: found.account_id, merchant: found.merchant ?? null, recurring_kind: kind,
        auto_link: autoLink, usage: usage === 'unknown' ? null : usage });
      onClose(name.trim());
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('frec.confirm.title', { name: found.name })} className="formDialog" onClose={() => onClose(null)}>
    <form onSubmit={(e) => void submit(e)} noValidate className="fbudForm">
      <label className="field" htmlFor={ids.name}><span>{t('fbud.form.name')}</span><input id={ids.name} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field" htmlFor={ids.kind}><span>{t('frec.confirm.kind')}</span>
        <select id={ids.kind} value={kind} onChange={(e) => setKind(e.target.value as FinanceRecurringKind)}>
          {RECURRING_KINDS.map((k) => <option key={k} value={k}>{t(`frec.kind.${k}` as MessageKey)}</option>)}</select></label>
      <label className="field" htmlFor={ids.usage}><span>{t('frec.confirm.usage')}</span>
        <select id={ids.usage} value={usage} onChange={(e) => setUsage(e.target.value as FinanceSubscriptionUsage | 'unknown')}>
          {USAGES.map((u) => <option key={u} value={u}>{t(`frec.usage.${u}` as MessageKey)}</option>)}</select></label>
      <label className="fbudCheck"><input type="checkbox" checked={autoLink} aria-describedby={ids.hint} onChange={(e) => setAutoLink(e.target.checked)} />{t('frec.confirm.autoLink')}</label>
      <small id={ids.hint} className="muted">{t('frec.confirm.autoLinkHint')}</small>
      {error && <Notice error>{error}</Notice>}
      <div className="dialogActions"><button type="button" className="secondary" onClick={() => onClose(null)}>{t('common.cancel')}</button>
        <button type="submit" disabled={busy}>{t('frec.confirm.save')}</button></div>
    </form>
  </Modal>;
}
