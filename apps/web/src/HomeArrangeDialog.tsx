// Arrange Home (docs/home.md#arrange-home): the person chooses which sections Home shows and in what
// order. Their own Home only; the API stores it per person. Moving uses buttons, so no drag is needed.
import { useState } from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { layoutDraft, layoutToSave, moveSection, type HomeLayout, type HomeSection } from './lib/home';
import * as homeApi from './lib/homeApi';

export default function HomeArrangeDialog({ layout, onSaved, onClose }: { layout: HomeLayout; onSaved: (layout: HomeLayout) => void; onClose: () => void }) {
  const [draft, setDraft] = useState(() => layoutDraft(layout));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const order = draft.map((row) => row.section);
  const move = (section: HomeSection, step: -1 | 1) => {
    const next = moveSection(order, section, step);
    setDraft(next.map((s) => draft.find((row) => row.section === s)!));
  };
  const save = async (reset = false) => {
    setBusy(true); setError(null);
    try { onSaved(reset ? await homeApi.resetLayout() : await homeApi.saveLayout(layoutToSave(draft))); onClose(); }
    catch (failure) { setError(t('home.arrange.failed', { message: describeError(failure) })); }
    finally { setBusy(false); }
  };
  const shown = layoutToSave(draft).length;
  return <ConfirmDialog title={t('home.arrange.title')} confirmLabel={t('home.arrange.save')} busy={busy} confirmDisabled={shown === 0} onConfirm={() => void save()} onCancel={onClose}>
    <p className="muted">{t('home.arrange.intro')}</p>
    <ol className="homeArrangeList">{draft.map((row, index) => <li key={row.section}>
      <label><input type="checkbox" checked={row.shown} disabled={busy} onChange={(event) => setDraft(draft.map((r) => r.section === row.section ? { ...r, shown: event.target.checked } : r))} />
        <span>{t(`home.section.${row.section}` as MessageKey)}</span></label>
      <span className="homeArrangeMove">
        <button type="button" className="secondary iconButton" disabled={busy || index === 0} aria-label={t('home.arrange.up', { name: t(`home.section.${row.section}` as MessageKey) })} onClick={() => move(row.section, -1)}><ArrowUp aria-hidden="true" /></button>
        <button type="button" className="secondary iconButton" disabled={busy || index === draft.length - 1} aria-label={t('home.arrange.down', { name: t(`home.section.${row.section}` as MessageKey) })} onClick={() => move(row.section, 1)}><ArrowDown aria-hidden="true" /></button>
      </span>
    </li>)}</ol>
    {shown === 0 && <p className="planWarn" role="alert">{t('home.arrange.none')}</p>}
    {error && <p className="planWarn" role="alert">{error}</p>}
    {layout.customized && <button type="button" className="secondary" disabled={busy} onClick={() => void save(true)}>{t('home.arrange.reset')}</button>}
  </ConfirmDialog>;
}
