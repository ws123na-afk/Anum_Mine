// Parts shared by the Bills and budgets and the Debts and goals pages (docs/finance.md#bills-and-budgets):
// the row menu, the scope control, the dialog that matches a recorded payment to a bill or a debt, and the
// dialog that adds or changes a budget (a Guardian category cap, confirmed with its code like every rule).
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { MoreVertical, Search } from 'lucide-react';
import type { FinanceAccount, FinanceCategory, FinanceGuardianChange, FinanceGuardianRules, FinancePaymentCandidate } from '@anum/contracts';
import * as api from './lib/financeApi';
import * as plan from './lib/financePlanningApi';
import { categoryLabel, formatAmount, isolate, parseAmount, shortHash } from './lib/finance';
import { describeError } from './lib/errors';
import { t } from './lib/i18n';
import { Modal } from './Dialog';
import { Empty, FormError, Money, Notice } from './FinanceParts';
import { ScopeSwitch } from './FinanceShell';
import { CATEGORIES } from './FinancePlanningForms';

/** A row's ⋮ menu: a button that opens a short list of actions; Escape or a click elsewhere closes it. */
export function RowMenu({ label, items }: { label: string; items: { label: string; onSelect: () => void; danger?: boolean; disabled?: boolean }[] }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  useEffect(() => {
    if (!open) return undefined;
    const close = (event: Event) => { if (box.current && !box.current.contains(event.target as Node)) setOpen(false); };
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); box.current?.querySelector<HTMLButtonElement>('button')?.focus(); } };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', key); };
  }, [open]);
  return <div className="fplMenu" ref={box}>
    <button type="button" className="iconButton small" aria-label={label} aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(!open)}><MoreVertical aria-hidden="true" /></button>
    {open && <ul id={menuId} className="fplMenuList">{items.map((item) => <li key={item.label}>
      <button type="button" className={item.danger ? 'danger' : undefined} disabled={item.disabled} onClick={() => { setOpen(false); item.onSelect(); }}>{item.label}</button>
    </li>)}</ul>}
  </div>;
}

/**
 * Personal only: bills, budgets, debts and goals are the person's own Finance records. Business bills and
 * budgets live in the business books (Business Expenses and P&L), so those options say so instead of
 * showing another workspace's figures here.
 */
export function PersonalScope() {
  const why = t('fplan.scope.business');
  return <ScopeSwitch value="personal" onChange={(next) => { if (next !== 'personal') location.hash = '#business-books'; }} unavailable={{ business: why, all: why }} />;
}

export function Progress({ percent, label, tone = 'ok' }: { percent: number; label: string; tone?: 'ok' | 'warn' | 'bad' }) {
  const shown = Math.max(0, Math.min(100, percent));
  return <span className={`fplBar ${tone}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={shown} aria-label={label}><span style={{ width: `${shown}%` }} /></span>;
}

/**
 * Match a recorded payment to a bill's due date or to a debt. The candidates are the person's own
 * payments near the date (the API ranks them by amount); nothing is matched without a pick, and the
 * bill shows Paid only once the API has the match.
 */
export function MatchDialog({ title, kind, around, amount, currency, accounts, onMatch, onClose, onOpenTransactions }: {
  title: string; kind: 'bill' | 'debt'; around: string | null; amount: string | null; currency: string; accounts: FinanceAccount[];
  onMatch: (candidate: FinancePaymentCandidate, key: string) => Promise<void>; onClose: (matched: boolean) => void; onOpenTransactions: () => void;
}) {
  const [q, setQ] = useState(''), [query, setQuery] = useState('');
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'ready'; items: FinancePaymentCandidate[] } | { kind: 'error'; message: string }>({ kind: 'loading' });
  const [busy, setBusy] = useState<string | null>(null), [error, setError] = useState<string | null>(null);
  const [key] = useState(plan.attemptKey);
  const errorId = useId();
  const names = new Map(accounts.map((a) => [a.id, a.last4 ? `${a.name} (${a.last4})` : a.name]));
  useEffect(() => {
    let live = true;
    setState({ kind: 'loading' });
    plan.paymentCandidates(kind, { around, amount, currency, q: query || null })
      .then((page) => { if (live) setState({ kind: 'ready', items: page.items }); })
      .catch((e) => { if (live) setState({ kind: 'error', message: describeError(e) }); });
    return () => { live = false; };
  }, [kind, around, amount, currency, query]);
  async function pick(candidate: FinancePaymentCandidate) {
    setBusy(candidate.canonical_id); setError(null);
    try { await onMatch(candidate, `${key}-${candidate.canonical_id}`); onClose(true); } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  }
  return <Modal title={title} className="formDialog fplMatch" onClose={() => onClose(false)}>
    <p className="muted">{kind === 'bill' ? t('fplan.match.billText') : t('fplan.match.debtText')}</p>
    <form className="cmFilters" role="search" onSubmit={(e: FormEvent) => { e.preventDefault(); setQuery(q.trim()); }}>
      <label className="cmSearch"><span className="visuallyHidden">{t('fplan.match.search')}</span><input type="search" value={q} maxLength={80} placeholder={t('fplan.match.search')} onChange={(e) => setQ(e.target.value)} /></label>
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
    </form>
    <FormError id={errorId} text={error} />
    {state.kind === 'loading' && <Notice busy>{t('fplan.match.loading')}</Notice>}
    {state.kind === 'error' && <Notice error>{state.message}</Notice>}
    {state.kind === 'ready' && (state.items.length === 0
      ? <Empty icon={<Search aria-hidden="true" />} title={t('fplan.match.none')} text={t('fplan.match.noneText', { currency })}
        action={<button type="button" className="secondary" onClick={() => { onClose(false); onOpenTransactions(); }}>{t('fplan.match.openTransactions')}</button>} />
      : <ul className="cmList fplCandidates">{state.items.map((c) => <li key={`${c.canonical_id}-${c.currency}`}>
        <div><strong dir="auto">{c.merchant}</strong><small><bdi dir="ltr">{c.posted_on}</bdi>{c.account_id && names.get(c.account_id) ? <> · <span dir="auto">{names.get(c.account_id)}</span></> : null}
          {c.remaining !== c.amount ? ` · ${t('fplan.match.left', { amount: isolate(formatAmount(c.remaining, c.currency)) })}` : ''}</small></div>
        <div className="finItemEnd"><Money value={c.amount} currency={c.currency} />
          <button type="button" disabled={busy !== null} aria-describedby={error ? errorId : undefined} onClick={() => void pick(c)}>{busy === c.canonical_id ? t('finance.saving') : t('fplan.match.pick')}</button></div>
      </li>)}</ul>)}
    <div className="actions"><button type="button" className="secondary" onClick={() => onClose(false)}>{t('common.cancel')}</button></div>
  </Modal>;
}

/**
 * Add or change a budget: a Guardian category cap. Like every rule it is proposed first and applies only
 * when confirmed with the code shown (docs/finance.md#guardian), so an assistant can never raise it.
 */
export function CapDialog({ rules, existing, base, onClose }: {
  rules: FinanceGuardianRules; existing: { category: FinanceCategory; currency: string; amount: string } | null; base: string; onClose: (changed: boolean) => void;
}) {
  const [category, setCategory] = useState<FinanceCategory>(existing?.category ?? 'groceries');
  const [currency, setCurrency] = useState(existing?.currency ?? base), [amount, setAmount] = useState(existing?.amount ?? '');
  const [change, setChange] = useState<FinanceGuardianChange | null>(null), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  const spendCategories = CATEGORIES.filter((c) => c !== 'income' && c !== 'transfer');
  async function propose(event: FormEvent) {
    event.preventDefault();
    if (!/^[A-Za-z]{3}$/.test(currency)) { setError(t('finance.error.currency')); return; }
    const checked = parseAmount(amount, currency.toUpperCase(), { signed: false, allowZero: false });
    if (!checked.ok) { setError(checked.error); return; }
    const cur = currency.toUpperCase();
    const caps = rules.category_caps.filter((c) => !(c.category === category && c.currency === cur) && !(existing && c.category === existing.category && c.currency === existing.currency));
    setBusy(true); setError(null);
    try { setChange(await api.proposeRules({ ...rules, category_caps: [...caps, { category, currency: cur, amount: checked.value }] }, t('fplan.cap.reason'))); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (!change) return;
    setBusy(true); setError(null);
    try { await api.confirmChange(change.id, change.payload_hash); onClose(true); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={existing ? t('fplan.cap.editTitle', { category: categoryLabel(existing.category) }) : t('fplan.cap.add')} className="formDialog" onClose={() => onClose(false)}>
    {!change ? <form className="finForm" onSubmit={(e) => void propose(e)} aria-describedby={error ? errorId : undefined}>
      <div className="cmFormGrid">
        <label className="field"><span>{t('finance.col.category')}</span><select value={category} disabled={Boolean(existing)} onChange={(e) => setCategory(e.target.value as FinanceCategory)}>{spendCategories.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
        <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} disabled={Boolean(existing)} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
        <label className="field"><span>{t('fplan.cap.amount', { currency: currency.toUpperCase() })}</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      </div>
      <p className="muted finHelp">{t('fplan.cap.help')}</p>
      <FormError id={errorId} text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('finance.saving') : t('fplan.cap.review')}</button><button type="button" className="secondary" disabled={busy} onClick={() => onClose(false)}>{t('common.cancel')}</button></div>
    </form> : <div className="finForm">
      <p>{change.loosens.length > 0 ? t('fplan.cap.loosens') : t('fplan.cap.stricter')}</p>
      <p>{t('fplan.cap.code')} <code dir="ltr">{shortHash(change.payload_hash)}</code></p>
      <FormError id={errorId} text={error} />
      <div className="actions"><button type="button" disabled={busy} onClick={() => void confirm()}>{busy ? t('finance.saving') : t('fplan.cap.confirm')}</button><button type="button" className="secondary" disabled={busy} onClick={() => onClose(false)}>{t('common.cancel')}</button></div>
    </div>}
  </Modal>;
}

/** A labelled figure inside a card (label above, value below). */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div className="fplFact"><span>{label}</span><strong>{children}</strong></div>;
}
