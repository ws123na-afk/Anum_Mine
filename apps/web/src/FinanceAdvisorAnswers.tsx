// Money advisor, Ask (docs/finance.md#advisor-answers): a question about your own money answered by the API from your
// records, with the records it read, the calculation and what is missing. A guess is never shown as confirmed.
import { useId, useState, type FormEvent } from 'react';
import { AlertTriangle, Calculator, ListChecks } from 'lucide-react';
import * as dx from './lib/financeDebtsApi';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Denied, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, StatusChip } from './FinanceShell';
import './financeDebts.css';

const day = (value: string | null) => (value ? formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '-');

export function AdvisorAnswers({ base }: { base: string }) {
  const formId = useId();
  const [question, setQuestion] = useState<dx.Question>('owe');
  const [values, setValues] = useState<dx.AnswerParams>({ currency: base });
  const [state, setState] = useState<Load<dx.AdvisorAnswer> | null>(null);
  const params = dx.answerParams(question, values);
  const needs = Object.keys(dx.answerParams(question, { category: 'x', words: 'x', country: 'x', currency: 'x', month: 'x', amount: 'x' })) as (keyof dx.AnswerParams)[];
  async function ask(event: FormEvent) {
    event.preventDefault();
    setState({ kind: 'loading' });
    try { setState({ kind: 'ready', value: await dx.advisorAnswer(question, params) }); } catch (e) { setState(failed(e)); }
  }
  const set = (key: keyof dx.AnswerParams) => (e: { target: { value: string } }) => setValues((v) => ({ ...v, [key]: e.target.value }));
  return <div className="fdebtAnswers">
    <FinCard title={t('fanswer.title')} icon={<ListChecks />} subtitle={t('fanswer.sub')}>
      <form className="finForm fdebtInline" onSubmit={ask} aria-describedby={`${formId}-hint`}>
        <label>{t('fanswer.question')}<select value={question} onChange={(e) => { setQuestion(e.target.value as dx.Question); setState(null); }}>
          {dx.QUESTIONS.map((q) => <option key={q} value={q}>{t(`fanswer.q.${q}` as MessageKey)}</option>)}</select></label>
        {needs.includes('category') && <label>{t('fanswer.p.category')}<input value={values.category ?? ''} onChange={set('category')} placeholder="food" /></label>}
        {needs.includes('words') && <label>{t('fanswer.p.words')}<input value={values.words ?? ''} onChange={set('words')} placeholder="laundry" /></label>}
        {needs.includes('country') && <label>{t('fanswer.p.country')}<input value={values.country ?? ''} onChange={set('country')} placeholder="Pakistan" /></label>}
        {needs.includes('amount') && <label>{t('fanswer.p.amount')}<input inputMode="decimal" value={values.amount ?? ''} onChange={set('amount')} /></label>}
        {needs.includes('currency') && <label>{t('fanswer.p.currency')}<input value={values.currency ?? ''} maxLength={3} onChange={(e) => setValues((v) => ({ ...v, currency: e.target.value.toUpperCase() }))} /></label>}
        {needs.includes('month') && <label>{t('fanswer.p.month')}<input type="month" value={values.month ?? ''} onChange={set('month')} /></label>}
        <button type="submit" disabled={state?.kind === 'loading'}>{t('fanswer.ask')}</button>
      </form>
      <p id={`${formId}-hint`} className="muted small">{t('fanswer.hint')}</p>
    </FinCard>
    {state?.kind === 'loading' && <Notice busy>{t('fanswer.loading')}</Notice>}
    {state?.kind === 'denied' && <Denied />}
    {state?.kind === 'error' && <Notice error>{t('fanswer.error', { message: state.message })}</Notice>}
    {state?.kind === 'ready' && <AnswerView answer={state.value} />}
  </div>;
}

export function AnswerView({ answer }: { answer: dx.AdvisorAnswer }) {
  return <FinCard title={t(`fanswer.q.${answer.question}` as MessageKey)} icon={<Calculator />} subtitle={t('fanswer.asOf', { date: day(answer.as_of) })}>
    <p className="fdebtAnswerText"><StatusChip tone={answer.complete ? 'ok' : 'warn'}>{t(answer.complete ? 'fanswer.complete' : 'fanswer.incomplete')}</StatusChip> {answer.answer}</p>
    {answer.figures.length > 0 && <dl className="finStatement">{answer.figures.map((f) => <div key={`${f.label}:${f.currency}`}><dt>{f.label}</dt>
      <dd><Money value={f.amount} currency={f.currency} /></dd></div>)}</dl>}
    {answer.calculation.length > 0 && <><h3 className="finAdvisorSub">{t('fanswer.calculation')}</h3>
      <ul className="fdebtSources" dir="auto">{answer.calculation.map((line, i) => <li key={i}><bdi>{line}</bdi></li>)}</ul></>}
    {answer.missing.length > 0 && <FinBanner tone="warn" icon={<AlertTriangle />} title={t('fanswer.missing')}>
      <ul className="fdebtSources">{answer.missing.map((m) => <li key={m}><bdi>{m}</bdi></li>)}</ul></FinBanner>}
    <h3 className="finAdvisorSub">{t('fanswer.records', { count: answer.records.length })}</h3>
    {answer.records.length === 0 ? <p className="muted">{t('fanswer.noRecords')}</p> : <div className="finTableWrap" tabIndex={0}><table className="finGrid">
      <thead><tr><th scope="col">{t('fdebt.col.date')}</th><th scope="col">{t('fanswer.record')}</th><th scope="col" className="num">{t('fdebt.col.amount')}</th>
        <th scope="col">{t('fdebt.col.evidence')}</th></tr></thead>
      <tbody>{answer.records.map((r, i) => <tr key={`${r.ref_type}:${r.ref_id}:${i}`}><td>{day(r.on)}</td><td><bdi>{r.label}</bdi></td>
        <td className="num">{r.amount && r.currency ? <Money value={r.amount} currency={r.currency} /> : '-'}</td>
        <td>{r.evidence ? <StatusChip tone={dx.evidenceTone(r.evidence)}>{dx.evidenceKey(r.evidence) ? t(dx.evidenceKey(r.evidence) as MessageKey) : r.evidence}</StatusChip> : '-'}</td></tr>)}</tbody>
    </table></div>}
    <p className="muted small">{answer.note}</p>
  </FinCard>;
}
