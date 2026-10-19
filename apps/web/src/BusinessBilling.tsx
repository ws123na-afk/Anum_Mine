// The billing profile (docs/business.md#invoices): who invoices come from and how a customer pays.
// Owners edit it; members read it. A sent invoice keeps the profile it was sent with.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import type { BusinessSetup } from '@anum/contracts';
import { t } from './lib/i18n';
import { profileBody, profileDraft, type ProfileDraft } from './lib/invoices';
import * as inv from './lib/businessInvoicesApi';
import { Note, failure } from './BusinessParts';

export function BillingProfileSection({ setup }: { setup: BusinessSetup }) {
  const [draft, setDraft] = useState<ProfileDraft | null>(null), [load, setLoad] = useState<'loading' | 'ready' | { error: string }>('loading');
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState(false);
  const heading = useId();
  const manage = setup.permissions.manage;
  const fetchProfile = useCallback(async () => {
    setLoad('loading');
    try { setDraft(profileDraft(await inv.getBillingProfile())); setLoad('ready'); } catch (error) { setLoad({ error: failure(error, t('inv.profile.title')) }); }
  }, []);
  useEffect(() => { void fetchProfile(); }, [fetchProfile]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    const body = profileBody(draft);
    if (!body.ok) { setMessage({ text: body.error, error: true }); return; }
    setBusy(true); setMessage(null);
    try { setDraft(profileDraft(await inv.saveBillingProfile(body.value))); setMessage({ text: t('inv.profile.saved') }); }
    catch (error) { setMessage({ text: failure(error, t('inv.profile.title')), error: true }); } finally { setBusy(false); }
  }
  const set = (key: keyof ProfileDraft) => (event: { target: { value: string } }) => draft && setDraft({ ...draft, [key]: event.target.value });
  return <section className="surface bzCard bzSpanAll" aria-labelledby={heading}>
    <h2 id={heading}>{t('inv.profile.title')}</h2>
    <p className="muted">{t('inv.profile.help')}</p>
    {load === 'loading' && <Note>{t('biz.loading')}</Note>}
    {typeof load === 'object' && <Note error>{load.error} <button type="button" className="secondary" onClick={() => void fetchProfile()}>{t('common.tryAgain')}</button></Note>}
    {draft && load === 'ready' && <form onSubmit={(event) => void save(event)} aria-label={t('inv.profile.title')} noValidate>
      <fieldset className="bzFieldset" disabled={!manage || busy}>
        <legend>{t('inv.profile.business')}</legend>
        <div className="bzGrid2">
          <label className="field"><span>{t('inv.profile.legalName')}</span><input value={draft.legal_name} onChange={set('legal_name')} dir="auto" /></label>
          <label className="field"><span>{t('inv.profile.email')}</span><input type="email" value={draft.email} onChange={set('email')} dir="ltr" /></label>
          <label className="field"><span>{t('inv.profile.phone')}</span><input value={draft.phone} onChange={set('phone')} dir="ltr" /></label>
          <label className="field"><span>{t('inv.profile.address')}</span><textarea rows={2} value={draft.address} onChange={set('address')} dir="auto" /></label>
          <label className="field"><span>{t('inv.profile.taxLabel')}</span><input value={draft.tax_label} onChange={set('tax_label')} dir="auto" /></label>
          <label className="field"><span>{t('inv.profile.taxNumber')}</span><input value={draft.tax_number} onChange={set('tax_number')} dir="auto" /></label>
        </div>
      </fieldset>
      <fieldset className="bzFieldset" disabled={!manage || busy}>
        <legend>{t('inv.profile.paying')}</legend>
        <label className="field"><span>{t('inv.profile.bank')}</span><textarea rows={3} value={draft.bank_details} onChange={set('bank_details')} dir="auto" /></label>
        <label className="field"><span>{t('inv.profile.footer')}</span><input value={draft.footer_note} onChange={set('footer_note')} dir="auto" /></label>
        <div className="bzGrid3">
          <label className="field"><span>{t('inv.profile.prefix')}</span><input value={draft.invoice_prefix} onChange={set('invoice_prefix')} dir="ltr" maxLength={8} /></label>
          <label className="field"><span>{t('inv.profile.dueDays')}</span><input inputMode="numeric" value={draft.default_due_days} onChange={set('default_due_days')} /></label>
          <label className="field"><span>{t('inv.profile.taxRate')}</span><input inputMode="decimal" value={draft.default_tax_rate_pct} onChange={set('default_tax_rate_pct')} /></label>
        </div>
      </fieldset>
      <p className="muted bzHint">{t('inv.profile.keeps')}</p>
      {manage ? <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.profile.save')}</button></div> : <p className="muted">{t('inv.profile.ownersOnly')}</p>}
      <div aria-live="polite">{message && <Note error={message.error}>{message.text}</Note>}</div>
    </form>}
  </section>;
}
