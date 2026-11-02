// Company details and legal facts in the platform console (docs/selling-readiness.md#company-and-legal).
// The company fields fill every legal page and new invoice; the legal facts are what only the owner and a
// lawyer can decide (English, like the documents). Facts the deployment knows (retention, subprocessors in
// use, the service address) are filled by the server and shown read-only. A document is marked published
// only after the owner confirms a lawyer reviewed it, and only when none of its facts is missing.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { Building2, ExternalLink, RefreshCw, Scale } from 'lucide-react';
import { describeError } from '../lib/errors';
import { formatDateTime, t, type MessageKey } from '../lib/i18n';
import { LEGAL_DOCS, type LegalDocId } from '../lib/publicSite';
import { COMPANY_FIELDS, EMAIL_FIELDS, missingCompany, missingTotal, validEmail, type Company, type CompanyField, type LegalProfile } from '../lib/selling';
import { getLegal, saveLegal } from '../lib/sellingApi';
import { Badge, Note } from './parts';

const WIDE: ReadonlySet<CompanyField> = new Set(['address', 'governing_law']);
const LTR: ReadonlySet<CompanyField> = new Set(['registration_number', 'registration_country', 'contact_email', 'phone', 'vat_number', 'dpo_email', 'security_email']);
const DOC_NAME: Record<LegalDocId, MessageKey> = { terms: 'public.doc.terms', privacy: 'public.doc.privacy', dpa: 'public.doc.dpa', aup: 'public.doc.aup', subprocessors: 'public.doc.subprocessors' };

export function ConsoleCompany() {
  const [saved, setSaved] = useState<LegalProfile | null>(null), [problem, setProblem] = useState<string | null>(null), [done, setDone] = useState(false);
  const load = useCallback(async () => { setProblem(null); try { setSaved(await getLegal()); } catch (e) { setProblem(describeError(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  if (problem && !saved) return <section className="surface blPanel"><div className="blRetry"><Note error>{t('selling.company.failed', { message: problem })}</Note><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></section>;
  if (!saved) return <section className="surface blPanel"><Note>{t('selling.company.loading')}</Note></section>;
  return <>{done && <Note>{t('selling.company.saved')}</Note>}<CompanyForm key={JSON.stringify(saved)} saved={saved} onSaved={(next) => { setSaved(next); setDone(true); }} /></>;
}

function CompanyForm({ saved, onSaved }: { saved: LegalProfile; onSaved: (next: LegalProfile) => void }) {
  const [company, setCompany] = useState<Company>(saved.company);
  const [terms, setTerms] = useState<Record<string, string>>(saved.legal_terms);
  const [published, setPublished] = useState<LegalDocId[]>(saved.published_docs);
  const [lawyer, setLawyer] = useState(false), [busy, setBusy] = useState(false), [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [termQuery, setTermQuery] = useState('');
  const formId = useId();
  const missing = missingCompany(company, saved.required_company);
  const typedTerms = saved.terms.filter((term) => !term.generated);
  const shownTerms = useMemo(() => {
    const q = termQuery.trim().toLowerCase();
    return q ? typedTerms.filter((term) => `${term.label} ${term.help} ${term.key}`.toLowerCase().includes(q)) : typedTerms;
  }, [termQuery, typedTerms]);
  const set = (key: CompanyField, value: string) => setCompany({ ...company, [key]: value });
  const field = (key: CompanyField) => {
    const id = `${formId}-${key}`, required = saved.required_company.includes(key);
    const label = <span>{t(`selling.company.field.${key}` as MessageKey)}{required && <span className="muted"> {t('selling.company.required')}</span>}</span>;
    if (key === 'hosting_provider') return <label key={key} className="field" htmlFor={id}>{label}
      <select id={id} value={company.hosting_provider} onChange={(e) => set(key, e.target.value)}>
        <option value="">{t('selling.company.hosting.none')}</option><option value="oracle">Oracle Cloud</option><option value="render">Render</option><option value="other">{t('selling.company.hosting.other')}</option>
      </select></label>;
    if (WIDE.has(key)) return <label key={key} className="field blWide" htmlFor={id}>{label}<textarea id={id} rows={2} maxLength={key === 'address' ? 600 : 300} dir="auto" value={company[key]} onChange={(e) => set(key, e.target.value)} /></label>;
    return <label key={key} className="field" htmlFor={id}>{label}<input id={id} dir={LTR.has(key) ? 'ltr' : 'auto'} type={EMAIL_FIELDS.includes(key) ? 'email' : 'text'} maxLength={key === 'registration_country' ? 2 : 254}
      value={company[key]} aria-invalid={EMAIL_FIELDS.includes(key) && company[key].trim() !== '' && !validEmail(company[key]) ? true : undefined}
      onChange={(e) => set(key, key === 'registration_country' ? e.target.value.toUpperCase() : e.target.value)} /></label>;
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const badEmail = EMAIL_FIELDS.find((key) => company[key].trim() && !validEmail(company[key]));
    if (badEmail) { setNote({ text: t('selling.company.badEmail', { field: t(`selling.company.field.${badEmail}` as MessageKey) }), error: true }); return; }
    if (company.registration_country && !/^[A-Za-z]{2}$/.test(company.registration_country)) { setNote({ text: t('selling.company.badCountry'), error: true }); return; }
    if (published.length && !lawyer) { setNote({ text: t('selling.legal.lawyerNeeded'), error: true }); return; }
    const cleanTerms = Object.fromEntries(Object.entries(terms).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));
    setBusy(true); setNote(null);
    try { onSaved(await saveLegal({ ...company, legal_terms: cleanTerms, published_docs: published, lawyer_reviewed: lawyer })); }
    catch (e) { setNote({ text: t('billing.failed', { message: describeError(e) }), error: true }); }
    finally { setBusy(false); }
  };
  return <form className="blStack" onSubmit={(e) => void submit(e)} noValidate>
    <section className="surface blPanel" aria-labelledby={`${formId}-company`}>
      <div className="blHead"><div><h2 id={`${formId}-company`}><Building2 aria-hidden="true" className="blInlineIcon" />{t('selling.company.title')}</h2><p className="muted">{t('selling.company.lead')}</p></div>
        <div className="blBadges"><Badge tone={missing.length ? 'warning' : 'success'}>{missing.length ? t('selling.company.missing', { count: missing.length }) : t('selling.company.complete')}</Badge></div></div>
      {saved.public_issuer === false && <Note error>{t('selling.company.notPublicIssuer')}</Note>}
      <div className="blTwo">{COMPANY_FIELDS.map(field)}</div>
      {saved.updated_at && <p className="muted blSmall">{t('selling.company.updated', { when: formatDateTime(saved.updated_at, { dateStyle: 'medium', timeStyle: 'short' }) })}</p>}
    </section>
    <section className="surface blPanel" aria-labelledby={`${formId}-legal`}>
      <div className="blHead"><div><h2 id={`${formId}-legal`}><Scale aria-hidden="true" className="blInlineIcon" />{t('selling.legal.title')}</h2><p className="muted">{t('selling.legal.lead')}</p></div>
        <div className="blBadges"><Badge tone={missingTotal(saved.missing) ? 'warning' : 'success'}>{t('selling.legal.missingCount', { count: missingTotal(saved.missing) })}</Badge></div></div>
      <Note error>{t('selling.legal.lawyerWarning')}</Note>
      <ul className="blDocs">{LEGAL_DOCS.map((doc) => <li key={doc.id}>
        <a href={doc.path} target="_blank" rel="noopener noreferrer"><ExternalLink aria-hidden="true" />{t(DOC_NAME[doc.id])}</a>
        <Badge tone={saved.missing[doc.id]?.length ? 'warning' : 'success'}>{saved.missing[doc.id]?.length ? t('selling.legal.docMissing', { count: saved.missing[doc.id].length }) : t('selling.legal.docFilled')}</Badge>
        <label className="blCheck"><input type="checkbox" checked={published.includes(doc.id)} disabled={Boolean(saved.missing[doc.id]?.length) && !published.includes(doc.id)}
          onChange={(e) => setPublished(e.target.checked ? [...published, doc.id] : published.filter((d) => d !== doc.id))} /><span>{t('selling.legal.publish')}</span></label>
      </li>)}</ul>
      <label className="blCheck"><input type="checkbox" checked={lawyer} onChange={(e) => setLawyer(e.target.checked)} /><span>{t('selling.legal.lawyerConfirm')}</span></label>
      <p className="muted" lang="en">{t('selling.legal.englishNote')}</p>
      <label className="field blSearch" htmlFor={`${formId}-q`}><span>{t('selling.legal.search')}</span><input id={`${formId}-q`} type="search" value={termQuery} onChange={(e) => setTermQuery(e.target.value)} /></label>
      {shownTerms.length === 0 && <p className="muted">{t('selling.legal.noMatch')}</p>}
      <div className="blTerms" lang="en" dir="ltr">{shownTerms.map((term) => {
        const id = `${formId}-t-${term.key}`;
        return <label key={term.key} className="field" htmlFor={id}><span>{term.label}{!saved.values[term.key] && <Badge tone="warning">{t('public.legal.notSet')}</Badge>}</span>
          <textarea id={id} rows={2} maxLength={2000} value={terms[term.key] ?? ''} aria-describedby={`${id}-help`} onChange={(e) => setTerms({ ...terms, [term.key]: e.target.value })} />
          <small className="muted" id={`${id}-help`}>{term.help}</small></label>;
      })}</div>
      <details className="blGenerated"><summary>{t('selling.legal.generated', { count: saved.generated.length })}</summary>
        <p className="muted">{t('selling.legal.generatedLead')}</p>
        <dl className="blFacts" lang="en" dir="ltr">{saved.generated.map((fact) => <div key={fact.key}><dt><code>{fact.key}</code></dt><dd>{fact.value}</dd></div>)}</dl>
      </details>
    </section>
    {note && <Note error={note.error}>{note.text}</Note>}
    <div className="actions blSave"><button type="submit" disabled={busy}>{busy ? t('common.loading') : t('selling.company.save')}</button></div>
  </form>;
}
