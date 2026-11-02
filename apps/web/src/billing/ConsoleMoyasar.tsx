// Moyasar card payments in the console's Settings tab (docs/billing.md#moyasar): mada, cards, Apple Pay and
// STC Pay on Moyasar's own page. The secret key and the webhook secret token are write-only: the page shows
// whether each is saved and whether the key is a test key, never the value (null keeps it, "" removes it).
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Copy, CreditCard, RefreshCw } from 'lucide-react';
import { apiBaseUrl } from '../lib/api';
import { describeError } from '../lib/errors';
import { t } from '../lib/i18n';
import type { MoyasarSettings } from '../lib/selling';
import { getMoyasar, saveMoyasar } from '../lib/sellingApi';
import { Badge, Note } from './parts';

export function ConsoleMoyasar() {
  const [saved, setSaved] = useState<MoyasarSettings | null>(null), [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(async () => { setProblem(null); try { setSaved(await getMoyasar()); } catch (e) { setProblem(describeError(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  if (problem && !saved) return <section className="surface blPanel"><div className="blRetry"><Note error>{t('billing.moyasar.failed', { message: problem })}</Note><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></section>;
  if (!saved) return <section className="surface blPanel"><Note>{t('billing.moyasar.loading')}</Note></section>;
  return <MoyasarForm key={JSON.stringify(saved)} saved={saved} onSaved={setSaved} />;
}

function MoyasarForm({ saved, onSaved }: { saved: MoyasarSettings; onSaved: (next: MoyasarSettings) => void }) {
  const [enabled, setEnabled] = useState(saved.enabled), [secret, setSecret] = useState(''), [hook, setHook] = useState('');
  const [removeSecret, setRemoveSecret] = useState(false), [removeHook, setRemoveHook] = useState(false);
  const [busy, setBusy] = useState(false), [note, setNote] = useState<{ text: string; error?: boolean } | null>(null), [copied, setCopied] = useState(false);
  const formId = useId();
  const webhook = `${apiBaseUrl.replace(/\/+$/, '')}${saved.webhook_url}`;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const key = secret.trim(), token = hook.trim();
    if (key && !/^sk_(test|live)_/.test(key)) { setNote({ text: t('billing.moyasar.keyShape'), error: true }); return; }
    if (token && token.length < 16) { setNote({ text: t('billing.moyasar.tokenShort'), error: true }); return; }
    if (enabled && ((!saved.has_secret_key && !key) || removeSecret)) { setNote({ text: t('billing.moyasar.needsKey'), error: true }); return; }
    if (enabled && ((!saved.has_webhook_secret && !token) || removeHook)) { setNote({ text: t('billing.moyasar.needsToken'), error: true }); return; }
    setBusy(true); setNote(null);
    try {
      onSaved(await saveMoyasar({ enabled, secret_key: removeSecret ? '' : key || null, webhook_secret: removeHook ? '' : token || null }));
    } catch (e) { setNote({ text: t('billing.failed', { message: describeError(e) }), error: true }); } finally { setBusy(false); }
  };
  const copy = async () => { try { await navigator.clipboard.writeText(webhook); setCopied(true); } catch { setCopied(false); } };
  return <form className="surface blPanel" aria-labelledby={`${formId}-title`} onSubmit={(e) => void submit(e)} noValidate>
    <div className="blHead"><div><h2 id={`${formId}-title`}><CreditCard aria-hidden="true" className="blInlineIcon" />{t('billing.moyasar.title')}</h2><p className="muted">{t('billing.moyasar.lead')}</p></div>
      <div className="blBadges">
        <Badge tone={saved.enabled ? 'success' : 'neutral'}>{saved.enabled ? t('billing.moyasar.on') : t('billing.moyasar.off')}</Badge>
        <Badge tone={saved.has_secret_key ? 'success' : 'neutral'}>{saved.has_secret_key ? t('billing.settings.keySaved') : t('billing.settings.noKey')}</Badge>
        {saved.test_mode !== null && <Badge tone={saved.test_mode ? 'warning' : 'success'}>{saved.test_mode ? t('billing.settings.testMode') : t('billing.settings.liveMode')}</Badge>}
        <Badge tone={saved.has_webhook_secret ? 'success' : 'neutral'}>{saved.has_webhook_secret ? t('billing.moyasar.tokenSaved') : t('billing.moyasar.noToken')}</Badge>
      </div></div>
    <ol className="blSteps">
      <li>{t('billing.moyasar.step1')}</li>
      <li>{t('billing.moyasar.step2')}<div className="blCopy"><code dir="ltr">{webhook}</code><button type="button" className="secondary" onClick={() => void copy()}><Copy aria-hidden="true" />{copied ? t('billing.moyasar.copied') : t('billing.moyasar.copy')}</button></div><small className="muted">{t('billing.moyasar.events', { events: saved.events.join(', ') })}</small></li>
      <li>{t('billing.moyasar.step3')}</li>
    </ol>
    <label className="blCheck"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /><span>{t('billing.moyasar.enable')}</span></label>
    <div className="blTwo">
      <label className="field" htmlFor={`${formId}-sk`}><span>{saved.has_secret_key ? t('billing.settings.replaceKey') : t('billing.moyasar.secretKey')}</span><input id={`${formId}-sk`} type="password" autoComplete="off" spellCheck={false} dir="ltr" value={secret} disabled={removeSecret} placeholder="sk_test_..." onChange={(e) => setSecret(e.target.value)} /></label>
      <label className="field" htmlFor={`${formId}-wh`}><span>{saved.has_webhook_secret ? t('billing.moyasar.replaceToken') : t('billing.moyasar.token')}</span><input id={`${formId}-wh`} type="password" autoComplete="off" spellCheck={false} dir="ltr" value={hook} disabled={removeHook} onChange={(e) => setHook(e.target.value)} /></label>
    </div>
    <small className="muted">{t('billing.moyasar.keysHint')}</small>
    {saved.has_secret_key && <label className="blCheck"><input type="checkbox" checked={removeSecret} onChange={(e) => setRemoveSecret(e.target.checked)} /><span>{t('billing.settings.removeKey')}</span></label>}
    {saved.has_webhook_secret && <label className="blCheck"><input type="checkbox" checked={removeHook} onChange={(e) => setRemoveHook(e.target.checked)} /><span>{t('billing.moyasar.removeToken')}</span></label>}
    {note && <Note error={note.error}>{note.text}</Note>}
    <div className="actions blSave"><button type="submit" disabled={busy}>{busy ? t('common.loading') : t('billing.moyasar.save')}</button></div>
  </form>;
}
