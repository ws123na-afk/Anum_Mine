// The issuer's billing settings in the console: who issues the invoices, the bank details shown on
// them, tax rules (country to percent), payment terms, the dunning schedule, the workspace whose email
// sends invoices, and Stripe. Stripe keys are write-only: the page shows whether one is saved and
// whether it is a test key, never the key itself (null keeps it, "" removes it).
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { KeyRound, Plus, RefreshCw, Trash2 } from 'lucide-react';
import * as b from '../lib/billingApi';
import { bpToPercent, formatRate, percentToBp, type IssuerSettings, type SettingsBody } from '../lib/billing';
import { countryName } from '../lib/billing';
import { describeError } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { Badge, Note } from './parts';

type TextKey = 'issuer_name' | 'issuer_vat_number' | 'issuer_email' | 'bank_name' | 'bank_account_name' | 'bank_iban' | 'bank_swift' | 'invoice_prefix' | 'credit_note_prefix';
const ISSUER: { key: TextKey; label: MessageKey; ltr?: boolean }[] = [
  { key: 'issuer_name', label: 'billing.settings.issuerName' }, { key: 'issuer_vat_number', label: 'billing.settings.vat', ltr: true }, { key: 'issuer_email', label: 'billing.settings.email', ltr: true },
];
const BANK: { key: TextKey; label: MessageKey; ltr?: boolean }[] = [
  { key: 'bank_name', label: 'billing.bank.bank' }, { key: 'bank_account_name', label: 'billing.bank.accountName' }, { key: 'bank_iban', label: 'billing.bank.iban', ltr: true }, { key: 'bank_swift', label: 'billing.bank.swift', ltr: true },
];

export function ConsoleSettings() {
  const [saved, setSaved] = useState<IssuerSettings | null>(null), [problem, setProblem] = useState<string | null>(null), [done, setDone] = useState(false);
  const load = useCallback(async () => { setProblem(null); try { setSaved(await b.getSettings()); } catch (e) { setProblem(describeError(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  if (problem && !saved) return <section className="surface blPanel"><div className="blRetry"><Note error>{t('billing.settings.failed', { message: problem })}</Note><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></section>;
  if (!saved) return <section className="surface blPanel"><Note>{t('billing.settings.loading')}</Note></section>;
  return <>{done && <Note>{t('billing.settings.saved')}</Note>}<SettingsForm key={JSON.stringify(saved)} saved={saved} onSaved={(next) => { setSaved(next); setDone(true); }} /></>;
}

function SettingsForm({ saved, onSaved }: { saved: IssuerSettings; onSaved: (next: IssuerSettings) => void }) {
  const [form, setForm] = useState(saved);
  const [address, setAddress] = useState(saved.issuer_address ?? '');
  const [rules, setRules] = useState<{ country: string; percent: string }[]>(() => Object.entries(saved.tax_rules ?? {}).map(([country, bp]) => ({ country, percent: bpToPercent(bp) })));
  const [defaultTax, setDefaultTax] = useState(bpToPercent(saved.default_tax_bp ?? 0)), [reminders, setReminders] = useState((saved.reminder_days ?? []).join(', '));
  const [secret, setSecret] = useState(''), [hook, setHook] = useState(''), [removeSecret, setRemoveSecret] = useState(false), [removeHook, setRemoveHook] = useState(false);
  const [busy, setBusy] = useState(false), [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const formId = useId();
  const text = (key: TextKey, label: MessageKey, ltr?: boolean) => <label key={key} className="field" htmlFor={`${formId}-${key}`}><span>{t(label)}</span>
    <input id={`${formId}-${key}`} value={form[key] ?? ''} dir={ltr ? 'ltr' : 'auto'} onChange={(e) => setForm({ ...form, [key]: e.target.value })} /></label>;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const taxRules: Record<string, number> = {};
    for (const rule of rules) {
      const code = rule.country.trim().toUpperCase(), bp = percentToBp(rule.percent);
      if (!/^[A-Z]{2}$/.test(code) || bp === null) { setNote({ text: t('billing.settings.taxInvalid'), error: true }); return; }
      taxRules[code] = bp;
    }
    const defaultBp = percentToBp(defaultTax);
    if (defaultBp === null) { setNote({ text: t('billing.settings.taxInvalid'), error: true }); return; }
    const days = reminders.split(/[\s,]+/).filter(Boolean).map(Number);
    if (days.some((d) => !Number.isInteger(d) || d < 0 || d > 120) || days.length > 10) { setNote({ text: t('billing.settings.remindersInvalid'), error: true }); return; }
    if (form.stripe_enabled && !form.has_stripe_secret_key && !secret.trim()) { setNote({ text: t('billing.settings.stripeNeedsKey'), error: true }); return; }
    const body: SettingsBody = {
      issuer_name: form.issuer_name ?? '', issuer_address: address, issuer_vat_number: form.issuer_vat_number ?? '', issuer_email: form.issuer_email ?? '',
      bank_name: form.bank_name ?? '', bank_account_name: form.bank_account_name ?? '', bank_iban: (form.bank_iban ?? '').trim(), bank_swift: (form.bank_swift ?? '').trim().toUpperCase(),
      invoice_prefix: (form.invoice_prefix || 'INV').toUpperCase(), credit_note_prefix: (form.credit_note_prefix || 'CN').toUpperCase(), payment_terms_days: Number(form.payment_terms_days) || 0,
      tax_rules: taxRules, default_tax_bp: defaultBp, reminder_days: days, grace_days: Number(form.grace_days) || 0,
      sending_workspace_id: form.sending_workspace_id?.trim() || null, stripe_enabled: form.stripe_enabled,
      stripe_secret_key: removeSecret ? '' : secret.trim() || null, stripe_webhook_secret: removeHook ? '' : hook.trim() || null,
    };
    setBusy(true); setNote(null);
    try { onSaved(await b.saveSettings(body)); } catch (e) { setNote({ text: t('billing.failed', { message: describeError(e) }), error: true }); } finally { setBusy(false); }
  };
  return <form className="blStack" onSubmit={(e) => void submit(e)} noValidate>
    <section className="surface blPanel" aria-labelledby={`${formId}-issuer`}>
      <h2 id={`${formId}-issuer`}>{t('billing.settings.issuer')}</h2><p className="muted">{t('billing.settings.issuerLead')}</p>
      <div className="blTwo">{ISSUER.map((f) => text(f.key, f.label, f.ltr))}</div>
      <label className="field" htmlFor={`${formId}-address`}><span>{t('billing.settings.address')}</span><textarea id={`${formId}-address`} rows={3} maxLength={600} dir="auto" value={address} onChange={(e) => setAddress(e.target.value)} /></label>
    </section>
    <section className="surface blPanel" aria-labelledby={`${formId}-bank`}>
      <h2 id={`${formId}-bank`}>{t('billing.bank.title')}</h2><p className="muted">{t('billing.settings.bankLead')}</p>
      <div className="blTwo">{BANK.map((f) => text(f.key, f.label, f.ltr))}</div>
    </section>
    <section className="surface blPanel" aria-labelledby={`${formId}-tax`}>
      <h2 id={`${formId}-tax`}>{t('billing.settings.tax')}</h2><p className="muted">{t('billing.settings.taxLead')}</p>
      <ul className="blRules">{rules.map((rule, index) => <li key={index}>
        <label className="field" htmlFor={`${formId}-rc-${index}`}><span>{t('billing.settings.country')}</span><input id={`${formId}-rc-${index}`} value={rule.country} maxLength={2} dir="ltr" onChange={(e) => setRules(rules.map((r, i) => i === index ? { ...r, country: e.target.value.toUpperCase() } : r))} /></label>
        <label className="field" htmlFor={`${formId}-rp-${index}`}><span>{t('billing.settings.rate')}</span><input id={`${formId}-rp-${index}`} inputMode="decimal" value={rule.percent} dir="ltr" onChange={(e) => setRules(rules.map((r, i) => i === index ? { ...r, percent: e.target.value } : r))} /></label>
        <span className="muted blRuleName">{/^[A-Z]{2}$/.test(rule.country) ? `${countryName(rule.country)}: ${percentToBp(rule.percent) === null ? '-' : formatRate(percentToBp(rule.percent)!)}` : ''}</span>
        <button type="button" className="secondary iconOnly" aria-label={t('billing.settings.removeRule', { country: rule.country || '-' })} onClick={() => setRules(rules.filter((_, i) => i !== index))}><Trash2 aria-hidden="true" /></button>
      </li>)}</ul>
      <div><button type="button" className="secondary" onClick={() => setRules([...rules, { country: '', percent: '15' }])}><Plus aria-hidden="true" />{t('billing.settings.addRule')}</button></div>
      <div className="blTwo">
        <label className="field" htmlFor={`${formId}-dtax`}><span>{t('billing.settings.defaultRate')}</span><input id={`${formId}-dtax`} inputMode="decimal" dir="ltr" value={defaultTax} onChange={(e) => setDefaultTax(e.target.value)} /></label>
        <label className="field" htmlFor={`${formId}-terms`}><span>{t('billing.settings.terms')}</span><input id={`${formId}-terms`} type="number" min={0} max={120} value={form.payment_terms_days} onChange={(e) => setForm({ ...form, payment_terms_days: Number(e.target.value) })} /></label>
      </div>
    </section>
    <section className="surface blPanel" aria-labelledby={`${formId}-dunning`}>
      <h2 id={`${formId}-dunning`}>{t('billing.settings.dunning')}</h2><p className="muted">{t('billing.settings.dunningLead')}</p>
      <div className="blTwo">
        <label className="field" htmlFor={`${formId}-rem`}><span>{t('billing.settings.reminders')}</span><input id={`${formId}-rem`} dir="ltr" value={reminders} placeholder="1, 7, 14" onChange={(e) => setReminders(e.target.value)} /></label>
        <label className="field" htmlFor={`${formId}-grace`}><span>{t('billing.settings.grace')}</span><input id={`${formId}-grace`} type="number" min={0} max={90} value={form.grace_days} onChange={(e) => setForm({ ...form, grace_days: Number(e.target.value) })} /></label>
        {text('invoice_prefix', 'billing.settings.invoicePrefix', true)}
        {text('credit_note_prefix', 'billing.settings.creditPrefix', true)}
        <label className="field" htmlFor={`${formId}-ws`}><span>{t('billing.settings.sendingWorkspace')}</span><input id={`${formId}-ws`} dir="ltr" value={form.sending_workspace_id ?? ''} placeholder="workspace_foundation" onChange={(e) => setForm({ ...form, sending_workspace_id: e.target.value })} aria-describedby={`${formId}-ws-hint`} /></label>
      </div>
      <small className="muted" id={`${formId}-ws-hint`}>{t('billing.settings.sendingHint')}</small>
    </section>
    <section className="surface blPanel" aria-labelledby={`${formId}-stripe`}>
      <div className="blHead"><div><h2 id={`${formId}-stripe`}><KeyRound aria-hidden="true" className="blInlineIcon" />{t('billing.settings.stripe')}</h2><p className="muted">{t('billing.settings.stripeLead')}</p></div>
        <div className="blBadges">
          <Badge tone={saved.has_stripe_secret_key ? 'success' : 'neutral'}>{saved.has_stripe_secret_key ? t('billing.settings.keySaved') : t('billing.settings.noKey')}</Badge>
          {saved.stripe_test_mode !== null && <Badge tone={saved.stripe_test_mode ? 'warning' : 'success'}>{saved.stripe_test_mode ? t('billing.settings.testMode') : t('billing.settings.liveMode')}</Badge>}
          <Badge tone={saved.has_stripe_webhook_secret ? 'success' : 'neutral'}>{saved.has_stripe_webhook_secret ? t('billing.settings.hookSaved') : t('billing.settings.noHook')}</Badge>
        </div></div>
      <label className="blCheck"><input type="checkbox" checked={form.stripe_enabled} onChange={(e) => setForm({ ...form, stripe_enabled: e.target.checked })} /><span>{t('billing.settings.stripeOn')}</span></label>
      <div className="blTwo">
        <label className="field" htmlFor={`${formId}-sk`}><span>{saved.has_stripe_secret_key ? t('billing.settings.replaceKey') : t('billing.settings.secretKey')}</span><input id={`${formId}-sk`} type="password" autoComplete="off" spellCheck={false} dir="ltr" value={secret} disabled={removeSecret} placeholder="sk_test_..." onChange={(e) => setSecret(e.target.value)} /></label>
        <label className="field" htmlFor={`${formId}-wh`}><span>{saved.has_stripe_webhook_secret ? t('billing.settings.replaceHook') : t('billing.settings.hookSecret')}</span><input id={`${formId}-wh`} type="password" autoComplete="off" spellCheck={false} dir="ltr" value={hook} disabled={removeHook} placeholder="whsec_..." onChange={(e) => setHook(e.target.value)} /></label>
      </div>
      <small className="muted">{t('billing.settings.keysHint')}</small>
      {saved.has_stripe_secret_key && <label className="blCheck"><input type="checkbox" checked={removeSecret} onChange={(e) => setRemoveSecret(e.target.checked)} /><span>{t('billing.settings.removeKey')}</span></label>}
      {saved.has_stripe_webhook_secret && <label className="blCheck"><input type="checkbox" checked={removeHook} onChange={(e) => setRemoveHook(e.target.checked)} /><span>{t('billing.settings.removeHook')}</span></label>}
    </section>
    {note && <Note error={note.error}>{note.text}</Note>}
    <div className="actions blSave"><button type="submit" disabled={busy}>{busy ? t('common.loading') : t('billing.settings.save')}</button></div>
  </form>;
}
