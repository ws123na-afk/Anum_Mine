// Finance, Debts and goals, Overview and Cards (docs/finance.md#debt-overview, #credit-cards): one view of everything the
// person owes and is owed, and every credit card with its statement. Every figure is the API's, worked out from the
// person's own records, and says where it came from. A provider's "paid" is shown as reported, never as paid, until a
// bank payment is matched. Nothing here pays a debt or moves money.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { AlertTriangle, CalendarDays, CreditCard, HandCoins, Layers, ListOrdered, Pencil, Plus, Scale } from 'lucide-react';
import * as dx from './lib/financeDebtsApi';
import { createDebt } from './lib/financeApi';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';
import { LoanPromises } from './FinanceLoanPromises';
import './financePlanning.css';
import './financeDebts.css';

const day = (value: string | null) => (value ? formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '-');
const typeLabel = (type: string) => t(`fdebt.type.${type}` as MessageKey);
const evidenceLabel = (evidence: string | null) => (evidence ? t(`fdebt.evidence.${evidence}` as MessageKey) : '-');

function useLoad<T>(fetcher: () => Promise<T>): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const load = useCallback(() => {
    setState({ kind: 'loading' });
    fetcher().then((value) => setState({ kind: 'ready', value }), (e) => setState(failed(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(load, [load]);
  return [state, load];
}

// -- the overview ---------------------------------------------------------------------------------------------

export function DebtOverviewSection({ onAddDebt, onChanged }: { onAddDebt: () => void; onChanged: () => void }) {
  const [state, reload] = useLoad(dx.debtOverview);
  const [open, setOpen] = useState<dx.Obligation | null>(null);
  const [editing, setEditing] = useState<dx.Obligation | null>(null);
  const [done, setDone] = useState<string | null>(null);
  if (state.kind === 'loading') return <Notice busy>{t('fdebt.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={t('fdebt.error.load', { message: state.message })} retry={reload} />;
  const view = state.value;
  const groups = dx.grouped(view.items);
  if (view.items.length === 0) {
    return <Empty icon={<Layers aria-hidden="true" />} title={t('fdebt.empty.title')} text={t('fdebt.empty.text')}
      action={<button type="button" onClick={onAddDebt}><Plus aria-hidden="true" />{t('fdebt.empty.add')}</button>} />;
  }
  return <div className="fdebtOverview">
    <p className="muted">{t('fdebt.intro')}</p>
    <KpiRow label={t('fdebt.kpis')}>
      {view.totals.map((total) => <KpiCard key={total.currency} icon={<Layers />} label={t('fdebt.kpi.owe', { currency: total.currency })}
        value={<Money value={total.owe} currency={total.currency} />} tone={Number(total.overdue) > 0 ? 'bad' : ''}
        hint={Number(total.overdue) > 0 ? t('fdebt.kpi.overdue', { amount: `${total.currency} ${total.overdue}` })
          : t('fdebt.kpi.due30', { amount: `${total.currency} ${total.due_30_days}` })} />)}
      {view.totals.filter((x) => Number(x.owed_to_me) > 0).map((total) => <KpiCard key={`in-${total.currency}`} icon={<HandCoins />}
        label={t('fdebt.kpi.owedToMe', { currency: total.currency })} value={<Money value={total.owed_to_me} currency={total.currency} />} />)}
    </KpiRow>
    {view.discrepancies > 0 && <FinBanner tone="warn" icon={<Scale />} title={t('fdebt.discrepancy.title', { count: view.discrepancies })}>
      {t('fdebt.discrepancy.text')}</FinBanner>}
    {done && <Notice>{done}</Notice>}
    <ObligationTable title={t('fdebt.owe.title')} rows={groups.owe} onOpen={setOpen} empty={t('fdebt.owe.empty')} />
    {groups.owedToMe.length > 0 && <ObligationTable title={t('fdebt.owedToMe.title')} rows={groups.owedToMe} onOpen={setOpen} empty="" />}
    <div className="fdebtTwo">
      <CalendarCard items={view.calendar} today={view.today} />
      <PlanCard rows={view.plan} />
    </div>
    {groups.finished.length > 0 && <ObligationTable title={t('fdebt.finished.title')} rows={groups.finished} onOpen={setOpen} empty="" />}
    <p className="muted small">{t('fdebt.note')}</p>
    {open && <ObligationDetail item={open} onClose={() => setOpen(null)} onEdit={() => { setEditing(open); setOpen(null); }}
      onChanged={() => { reload(); onChanged(); }} />}
    {editing && <ProfileDialog debtId={editing.ref_id} name={editing.name} card={editing.type === 'credit_card'} initial={editing}
      onClose={() => setEditing(null)} onDone={(text) => { setEditing(null); setDone(text); reload(); onChanged(); }} />}
  </div>;
}

function ObligationTable({ title, rows, onOpen, empty }: { title: string; rows: dx.Obligation[]; onOpen: (o: dx.Obligation) => void; empty: string }) {
  return <FinCard title={title} icon={<Layers />}>
    {rows.length === 0 ? <p className="muted">{empty}</p> : <div className="finTableWrap" tabIndex={0}><table className="finGrid">
      <thead><tr><th scope="col">{t('fdebt.col.name')}</th><th scope="col">{t('fdebt.col.type')}</th><th scope="col" className="num">{t('fdebt.col.remaining')}</th>
        <th scope="col">{t('fdebt.col.next')}</th><th scope="col">{t('fdebt.col.status')}</th><th scope="col"><span className="visuallyHidden">{t('fdebt.col.open')}</span></th></tr></thead>
      <tbody>{rows.map((o) => <tr key={o.id}>
        <th scope="row"><bdi>{o.name}</bdi>{o.counterparty && o.counterparty !== o.name && <small className="muted"> · <bdi>{o.counterparty}</bdi></small>}</th>
        <td>{typeLabel(o.type)}</td>
        <td className="num"><Money value={o.remaining} currency={o.currency} />
          {o.discrepancy && <><br /><StatusChip tone="warn">{t('fdebt.chip.differs')}</StatusChip></>}</td>
        <td>{o.next_due_on ? <>{day(o.next_due_on)}<br /><small><Money value={o.next_due_amount} currency={o.currency} /></small></> : '-'}</td>
        <td><StatusChip tone={dx.statusTone(o.status)}>{t(`fdebt.status.${o.status}` as MessageKey)}</StatusChip>
          {Number(o.unverified_amount) > 0 && <><br /><StatusChip tone="warn">{t('fdebt.chip.unverified', { amount: `${o.currency} ${o.unverified_amount}` })}</StatusChip></>}</td>
        <td><button type="button" className="secondary" onClick={() => onOpen(o)} aria-label={t('fdebt.open', { name: o.name })}>{t('fdebt.details')}</button></td>
      </tr>)}</tbody>
    </table></div>}
  </FinCard>;
}

function CalendarCard({ items, today }: { items: dx.CalendarItem[]; today: string }) {
  return <FinCard title={t('fdebt.calendar.title')} icon={<CalendarDays />} subtitle={t('fdebt.calendar.sub')}>
    {items.length === 0 ? <p className="muted">{t('fdebt.calendar.empty')}</p> : <ul className="fdebtList">
      {items.slice(0, 30).map((c) => <li key={`${c.obligation_id}:${c.on}:${c.amount}`}>
        <span>{c.on === today ? t('fdebt.cal.today') : day(c.on)}</span>
        <span><bdi>{c.name}</bdi></span>
        <Money value={c.amount} currency={c.currency} />
        <StatusChip tone={c.status === 'paid' ? 'ok' : c.status === 'overdue' ? 'bad' : c.status === 'due' ? 'info' : 'warn'}>{t(`fdebt.cal.${c.status}` as MessageKey)}</StatusChip>
      </li>)}
    </ul>}
  </FinCard>;
}

function PlanCard({ rows }: { rows: dx.PlanRow[] }) {
  return <FinCard title={t('fdebt.plan.title')} icon={<ListOrdered />} subtitle={t('fdebt.plan.sub')}>
    {rows.length === 0 ? <p className="muted">{t('fdebt.plan.empty')}</p> : <ol className="fdebtList">
      {rows.map((p) => <li key={p.obligation_id}>
        <span dir="ltr">{p.currency} #{p.rank}</span><span><bdi>{p.name}</bdi></span><Money value={p.remaining} currency={p.currency} />
        <small className="muted">{t(`fdebt.reason.${p.reason}` as MessageKey, { rate: p.rate ?? '' })}</small>
      </li>)}
    </ol>}
  </FinCard>;
}

function ObligationDetail({ item, onClose, onEdit, onChanged }: { item: dx.Obligation; onClose: () => void; onEdit: () => void; onChanged: () => void }) {
  const c = item.currency;
  const editable = item.ref_type === 'debt' || (item.ref_type === 'card' && item.type === 'credit_card');
  return <Modal title={item.name} onClose={onClose} className="fpmDialog">
    <dl className="finStatement">
      <div><dt>{t('fdebt.col.type')}</dt><dd>{typeLabel(item.type)}</dd></div>
      {item.original_amount && <div><dt>{t('fdebt.f.original')}</dt><dd><Money value={item.original_amount} currency={c} /></dd></div>}
      <div><dt>{t('fdebt.f.paid')}</dt><dd><Money value={item.paid} currency={c} /></dd></div>
      <div><dt>{t('fdebt.col.remaining')}</dt><dd><Money value={item.remaining} currency={c} /></dd></div>
      {item.expected_installment && <div><dt>{t('fdebt.f.installment')}</dt><dd><Money value={item.expected_installment} currency={c} /></dd></div>}
      <div><dt>{t('fdebt.f.rate')}</dt><dd>{item.rate ? `${Number(item.rate)}%` : t(`fdebt.interest.${item.interest}` as MessageKey)}</dd></div>
      {item.interest_paid && <div><dt>{t('fdebt.f.interestPaid')}</dt><dd><Money value={item.interest_paid} currency={c} /></dd></div>}
      {item.fees && <div><dt>{t('fdebt.f.fees')}</dt><dd><Money value={item.fees} currency={c} /></dd></div>}
      {Number(item.overdue_amount) > 0 && <div><dt>{t('fdebt.status.overdue')}</dt><dd><Money value={item.overdue_amount} currency={c} /></dd></div>}
      <div><dt>{t('fdebt.f.payoff')}</dt><dd>{day(item.payoff_on)}</dd></div>
      {item.verified_balance && <div><dt>{t('fdebt.f.lender', { date: day(item.verified_on) })}</dt><dd><Money value={item.verified_balance} currency={c} />
        {item.discrepancy && item.difference && <> <StatusChip tone="warn">{t('fdebt.f.difference', { amount: `${c} ${item.difference}` })}</StatusChip></>}</dd></div>}
      {item.disputed_amount && <div><dt>{t('fdebt.f.disputed')}</dt><dd><Money value={item.disputed_amount} currency={c} /></dd></div>}
      {Number(item.unverified_amount) > 0 && <div><dt>{t('fdebt.f.unverified')}</dt><dd><Money value={item.unverified_amount} currency={c} /></dd></div>}
    </dl>
    <h3 className="finAdvisorSub">{t('fdebt.sources.title')}</h3>
    <ul className="fdebtSources">{item.sources.map((s) => <li key={s.figure}>{t(`fdebt.figure.${s.figure}` as MessageKey)}: {t(`fdebt.source.${s.source}` as MessageKey)}</li>)}</ul>
    {item.assumptions.length > 0 && <><h3 className="finAdvisorSub">{t('fdebt.assumptions')}</h3><ul className="fdebtSources">{item.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></>}
    {item.missing.length > 0 && <FinBanner tone="info" icon={<AlertTriangle />} title={t('fdebt.missing.title')}>
      {item.missing.map((m) => t(`fdebt.missing.${m}` as MessageKey)).join(' ')}</FinBanner>}
    <h3 className="finAdvisorSub">{t('fdebt.history.title')}</h3>
    {item.history.length === 0 ? <p className="muted">{t('fdebt.history.empty')}</p> : <div className="finTableWrap" tabIndex={0}><table className="finGrid">
      <thead><tr><th scope="col">{t('fdebt.col.date')}</th><th scope="col" className="num">{t('fdebt.col.amount')}</th><th scope="col">{t('fdebt.col.evidence')}</th></tr></thead>
      <tbody>{item.history.map((h, i) => <tr key={`${h.ref_id}:${i}`}><td>{day(h.on)}</td><td className="num"><Money value={h.amount} currency={h.currency} /></td>
        <td><StatusChip tone={dx.evidenceTone(h.evidence)}>{evidenceLabel(h.evidence)}</StatusChip>{h.note && <small className="muted"> <bdi>{h.note}</bdi></small>}</td></tr>)}</tbody>
    </table></div>}
    {item.ref_type === 'person_loan' && <LoanPromises loanId={item.ref_id} currency={c} promises={item.promises} agreed={item.verified_balance} onChanged={onChanged} />}
    {editable && <div className="actions"><button type="button" className="secondary" onClick={onEdit}><Pencil aria-hidden="true" />{t('fdebt.profile.open')}</button></div>}
  </Modal>;
}

// -- the profile dialog: the lender's balance, a dispute, fees and a card's statement ------------------------------

export function ProfileDialog({ debtId, name, card, initial, onClose, onDone }: {
  debtId: string; name: string; card: boolean; initial: Partial<dx.Obligation> & { card?: dx.CardView }; onClose: () => void; onDone: (text: string) => void;
}) {
  const errorId = useId();
  const c = initial.card;
  const [values, setValues] = useState<Record<string, string>>({
    debt_type: card ? 'credit_card' : (initial.type && (dx.PROFILE_TYPES as readonly string[]).includes(initial.type) ? initial.type : ''),
    verified_balance: card ? '' : initial.verified_balance ?? '', verified_on: card ? '' : initial.verified_on ?? '', disputed_amount: initial.disputed_amount ?? '',
    dispute_note: '', fees: initial.fees ?? '', card_limit: c?.limit_source === 'entered' ? c.limit ?? '' : '',
    card_statement_day: c?.statement_day ? String(c.statement_day) : '', card_statement_balance: c?.statement_balance ?? '',
    card_statement_minimum: c?.statement_minimum ?? '', card_statement_on: c?.statement_on ?? '',
  });
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const set = (key: string) => (e: { target: { value: string } }) => setValues((v) => ({ ...v, [key]: e.target.value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null);
    try { await dx.saveDebtProfile(debtId, dx.profileBody(values)); onDone(t('fdebt.profile.saved', { name })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const field = (key: string, label: string, type = 'text', hint?: string) => <label key={key}>{label}
    <input type={type} inputMode={type === 'text' ? 'decimal' : undefined} value={values[key]} onChange={set(key)} aria-describedby={error ? errorId : undefined} />
    {hint && <small className="muted">{hint}</small>}</label>;
  return <Modal title={t('fdebt.profile.title', { name })} onClose={onClose} className="fpmDialog">
    <form className="finForm" onSubmit={submit} noValidate>
      <p className="muted">{t(card ? 'fdebt.profile.cardIntro' : 'fdebt.profile.intro')}</p>
      {!card && <label>{t('fdebt.col.type')}<select value={values.debt_type} onChange={set('debt_type')}>
        <option value="">{t('fdebt.profile.typeAuto')}</option>
        {dx.PROFILE_TYPES.map((type) => <option key={type} value={type}>{typeLabel(type)}</option>)}</select></label>}
      {!card && field('verified_balance', t('fdebt.profile.lender'))}
      {!card && field('verified_on', t('fdebt.profile.lenderOn'), 'date')}
      {card && field('card_limit', t('fdebt.profile.limit'), 'text', t('fdebt.profile.limitHint'))}
      {card && field('card_statement_day', t('fdebt.profile.closingDay'), 'number')}
      {card && field('card_statement_balance', t('fdebt.profile.statementBalance'))}
      {card && field('card_statement_on', t('fdebt.profile.statementOn'), 'date')}
      {card && field('card_statement_minimum', t('fdebt.profile.minimum'))}
      {field('disputed_amount', t('fdebt.profile.disputed'))}
      {field('dispute_note', t('fdebt.profile.disputeNote'))}
      {field('fees', t('fdebt.profile.fees'))}
      {card && <p className="muted small">{t('fdebt.profile.rateHint')}</p>}
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={onClose} />
    </form>
  </Modal>;
}

// -- credit cards ---------------------------------------------------------------------------------------------

export function CardsSection({ onChanged }: { onChanged: () => void }) {
  const [state, reload] = useLoad(dx.cards);
  const [editing, setEditing] = useState<dx.CardView | null>(null);
  const [done, setDone] = useState<string | null>(null), [error, setError] = useState<string | null>(null);
  if (state.kind === 'loading') return <Notice busy>{t('fdebt.cards.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={t('fdebt.error.load', { message: state.message })} retry={reload} />;
  const items = state.value.items;
  if (items.length === 0) return <Empty icon={<CreditCard aria-hidden="true" />} title={t('fdebt.cards.empty.title')} text={t('fdebt.cards.empty.text')} />;
  async function addTerms(card: dx.CardView) {
    setError(null);
    try {
      const debt = await createDebt({ name: card.name, kind: 'card', balance: '0', currency: card.currency, account_id: card.account_id });
      setEditing({ ...card, debt_id: debt.id }); onChanged();
    } catch (e) { setError(describeError(e)); }
  }
  return <div className="fdebtCards">
    <p className="muted">{t('fdebt.cards.intro')}</p>
    {done && <Notice>{done}</Notice>}{error && <Notice error>{error}</Notice>}
    {items.map((card) => <CardPanel key={card.account_id} card={card} onTerms={() => (card.debt_id ? setEditing(card) : void addTerms(card))} />)}
    {editing?.debt_id && <ProfileDialog debtId={editing.debt_id} name={editing.name} card initial={{ card: editing }} onClose={() => setEditing(null)}
      onDone={(text) => { setEditing(null); setDone(text); reload(); onChanged(); }} />}
  </div>;
}

function CardPanel({ card, onTerms }: { card: dx.CardView; onTerms: () => void }) {
  const c = card.currency;
  const minimum = card.pay_options.find((o) => o.code === 'minimum');
  const full = card.pay_options.find((o) => o.code === 'full');
  return <FinCard title={card.last4 ? `${card.name} · ${card.last4}` : card.name} icon={<CreditCard />}
    actions={<button type="button" className="secondary" onClick={onTerms}><Pencil aria-hidden="true" />{t('fdebt.cards.terms')}</button>}>
    <KpiRow label={t('fdebt.cards.kpis', { name: card.name })}>
      <KpiCard icon={<CreditCard />} label={t('fdebt.cards.outstanding')} value={<Money value={card.outstanding} currency={c} />} hint={t('fdebt.cards.fromLedger')} />
      <KpiCard icon={<Layers />} label={t('fdebt.cards.available')} value={card.available ? <Money value={card.available} currency={c} /> : '-'}
        hint={card.limit ? t('fdebt.cards.limit', { amount: `${c} ${card.limit}`, percent: card.utilization_percent ?? 0 }) : t('fdebt.cards.noLimit')} />
      <KpiCard icon={<CalendarDays />} label={t('fdebt.cards.due')} value={card.due_on ? day(card.due_on) : '-'} tone={card.payment_state === 'not_paid' ? 'bad' : ''}
        hint={t(`fdebt.cards.state.${card.payment_state}` as MessageKey)} />
    </KpiRow>
    {card.check.status === 'differs' && <FinBanner tone="warn" icon={<Scale />} title={t('fdebt.cards.differs.title')}>
      {t('fdebt.cards.differs.text', { statement: `${c} ${card.check.statement_balance}`, ledger: `${c} ${card.check.ledger_balance}`, date: day(card.check.statement_on),
        rows: card.check.unverified_rows })}</FinBanner>}
    {Number(card.paid_provisional) > 0 && <FinBanner tone="info" title={t('fdebt.cards.provisional', { amount: `${c} ${card.paid_provisional}` })} />}
    <div className="fdebtTwo">
      <div>
        <h3 className="finAdvisorSub">{t('fdebt.cards.payTitle')}</h3>
        <dl className="finStatement">
          {full && <div><dt>{t('fdebt.cards.payFull')}</dt><dd>{full.amount ? <Money value={full.amount} currency={c} /> : '-'}</dd></div>}
          {minimum && <div><dt>{t('fdebt.cards.payMinimum')}</dt><dd>{minimum.amount ? <Money value={minimum.amount} currency={c} /> : '-'}
            {minimum.leaves && <small className="muted"> {t('fdebt.cards.leaves', { amount: `${c} ${minimum.leaves}` })}</small>}</dd></div>}
          {minimum?.interest_estimate && <div><dt>{t('fdebt.cards.interestNext')}</dt><dd><Money value={minimum.interest_estimate} currency={c} /></dd></div>}
          {minimum?.months_to_clear && <div><dt>{t('fdebt.cards.monthsToClear')}</dt><dd>{minimum.months_to_clear}{minimum.total_interest && <small className="muted">
            {' '}{t('fdebt.cards.totalInterest', { amount: `${c} ${minimum.total_interest}` })}</small>}</dd></div>}
          {minimum?.never_clears && <div><dt>{t('fdebt.cards.monthsToClear')}</dt><dd>{t('fdebt.cards.never')}</dd></div>}
        </dl>
        {!card.apr_percent && <p className="muted small">{t('fdebt.cards.noRate')}</p>}
      </div>
      <div>
        <h3 className="finAdvisorSub">{t('fdebt.cards.cycle', { start: day(card.cycle.start), end: day(card.cycle.end) })}</h3>
        <dl className="finStatement">
          {(['purchases', 'refunds', 'cashback', 'fees', 'interest', 'cash_advances', 'installment_conversions', 'payments'] as const).map((k) =>
            <div key={k}><dt>{t(`fdebt.cards.k.${k}` as MessageKey)}</dt><dd><Money value={card.cycle[k]} currency={c} /></dd></div>)}
        </dl>
      </div>
    </div>
    <h3 className="finAdvisorSub">{t('fdebt.cards.payments')}</h3>
    {card.payments.length === 0 ? <p className="muted">{t('fdebt.cards.noPayments')}</p> : <ul className="fdebtList">
      {card.payments.slice(0, 8).map((p) => <li key={p.transaction_id}><span>{day(p.posted_on)}</span><Money value={p.amount} currency={c} />
        <StatusChip tone={p.linked ? 'ok' : 'warn'}>{p.linked ? t('fdebt.cards.linked', { account: p.from_account_name ?? '-' }) : t('fdebt.cards.notLinked')}</StatusChip>
        <StatusChip tone={dx.evidenceTone(p.evidence)}>{evidenceLabel(p.evidence)}</StatusChip></li>)}
    </ul>}
    <p className="muted small">{t('fdebt.cards.evidence', { posted: card.evidence.posted, pending: card.evidence.pending, unverified: card.evidence.unverified })}</p>
    {card.missing.length > 0 && <p className="muted small">{t('fdebt.cards.missing')} {card.missing.map((m) => t(`fdebt.missing.${m}` as MessageKey)).join(' ')}</p>}
  </FinCard>;
}
