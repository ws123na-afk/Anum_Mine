// Save as purchase from the Email Center (docs/purchases.md#save-as-purchase-from-the-email-center): the open message
// becomes a purchase draft, read by the same fixed patterns as a shared order message, and one attachment the person picks
// is kept privately in their document vault as the receipt. The server checks Purchases, the Email Center, the mailbox
// and, for the attachment, the vault; a refusal says which.
import { useId, useState, type FormEvent } from 'react';
import { PackagePlus } from 'lucide-react';
import type { PurchaseFromMailResult } from '@anum/contracts';
import { ApiError, describeError } from './lib/errors';
import { t } from './lib/i18n';
import { purchaseHref, receiptChoices, type MailAttachmentChoice } from './lib/purchases';
import { saveMailAsPurchase } from './lib/purchasesApi';
import { Modal } from './Dialog';

/** What a refusal means to the person, with the server's own words when it gave some. */
export function saveFailure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return t('mailp.err.network');
    if (error.status === 403) return error.detail ?? t('mailp.err.denied');
    if (error.status === 404) return error.detail ?? t('mailp.err.missing');
    if (error.detail) return error.detail;
  }
  return describeError(error);
}

export function SaveAsPurchaseButton({ accountId, folder, uid, attachments }: { accountId: string; folder: string; uid: number; attachments: readonly MailAttachmentChoice[] }) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<PurchaseFromMailResult | null>(null);
  return <div className="mailPurchase">
    <button type="button" className="secondary" onClick={() => { setDone(null); setOpen(true); }}><PackagePlus aria-hidden="true" />{t('mailp.button')}</button>
    <p className="muted mailSmall" role="status">{done && <>{done.message} <a href={purchaseHref(done.purchase_id)}>{t('mailp.open')}</a></>}</p>
    {open && <SaveDialog accountId={accountId} folder={folder} uid={uid} attachments={attachments} onClose={() => setOpen(false)}
      onSaved={(result) => { setOpen(false); setDone(result); }} />}
  </div>;
}

function SaveDialog({ accountId, folder, uid, attachments, onClose, onSaved }: {
  accountId: string; folder: string; uid: number; attachments: readonly MailAttachmentChoice[]; onClose: () => void; onSaved: (result: PurchaseFromMailResult) => void;
}) {
  const choices = receiptChoices(attachments);
  const [pick, setPick] = useState<string>(choices.length ? String(choices[0].index) : 'none');
  const [ownership, setOwnership] = useState<'personal' | 'business'>('personal');
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const fileGroup = useId(), whoGroup = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null);
    try { onSaved(await saveMailAsPurchase({ account_id: accountId, folder, uid, ownership, attachment_index: pick === 'none' ? null : Number(pick) })); }
    catch (error) { setProblem(saveFailure(error)); setBusy(false); }
  }
  return <Modal title={t('mailp.title')} className="formDialog" onClose={onClose}>
    <form className="mailpForm" onSubmit={(event) => void submit(event)} aria-label={t('mailp.title')} noValidate>
      <p className="muted">{t('mailp.help')}</p>
      <fieldset aria-labelledby={whoGroup}>
        <legend id={whoGroup}>{t('mailp.whose')}</legend>
        {(['personal', 'business'] as const).map((value) => <label key={value} className="mailpChoice">
          <input type="radio" name="mailp-owner" value={value} checked={ownership === value} onChange={() => setOwnership(value)} />{t(`mailp.owner.${value}`)}</label>)}
      </fieldset>
      <fieldset aria-labelledby={fileGroup}>
        <legend id={fileGroup}>{t('mailp.receipt')}</legend>
        {choices.map((item) => <label key={item.index} className="mailpChoice">
          <input type="radio" name="mailp-file" value={String(item.index)} checked={pick === String(item.index)} onChange={() => setPick(String(item.index))} />
          <span dir="auto">{item.filename}</span></label>)}
        <label className="mailpChoice"><input type="radio" name="mailp-file" value="none" checked={pick === 'none'} onChange={() => setPick('none')} />{t('mailp.noReceipt')}</label>
        {choices.length === 0 && <p className="muted mailSmall">{t('mailp.noFiles')}</p>}
        {pick !== 'none' && <p className="muted mailSmall">{t('mailp.private')}</p>}
      </fieldset>
      {problem && <p className="notice errorNotice" role="alert">{problem}</p>}
      <div className="actions">
        <button type="submit" disabled={busy}>{busy ? t('mailp.saving') : t('mailp.save')}</button>
        <button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button>
      </div>
    </form>
  </Modal>;
}
