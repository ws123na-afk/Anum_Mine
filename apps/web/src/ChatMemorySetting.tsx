import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { CircleDot, MessageSquare, RefreshCw } from 'lucide-react';
import * as api from './lib/api';
import { chatMemoryStatus, savedMessage, type UserPreferences } from './lib/chatMemory';
import { describeError } from './lib/errors';
import { t } from './lib/i18n';

// "Remember things from my chats" (docs/memory.md#from-conversations): the caller's own switch, saved
// at once through PUT /api/v1/me/preferences. Shown in Settings and in the Memory view; `onReview`
// leads to the memories already remembered, where they can be deleted. `focusOnLoad` moves focus to
// the switch once it has loaded (the Privacy page's "Change Remember things from my chats").
export function ChatMemorySetting({ onReview, reviewLabel, focusOnLoad = false }: { onReview: () => void; reviewLabel: string; focusOnLoad?: boolean }) {
  const [prefs, setPrefs] = useState<UserPreferences | null>(null);
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const describedBy = useId();
  const toggle = useRef<HTMLInputElement>(null), focused = useRef(false);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setPrefs(await api.getMyPreferences()); } catch (e) { setError(t('chatMemory.loadFailed', { reason: describeError(e) })); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!focusOnLoad) { focused.current = false; return; }
    if (!prefs || focused.current || !toggle.current) return;
    focused.current = true;
    const el = toggle.current;
    el.focus({ preventScroll: true });
    el.scrollIntoView({ block: 'center' });
    // Sections above this one (onboarding, model, notifications) may still be loading and push it
    // down: keep the focused switch in view while they settle, for at most two seconds.
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => { if (document.activeElement === el) el.scrollIntoView({ block: 'center' }); });
    observer.observe(el.closest('.settingsStack') ?? document.body);
    const stop = window.setTimeout(() => observer.disconnect(), 2000);
    return () => { window.clearTimeout(stop); observer.disconnect(); };
  }, [focusOnLoad, prefs]);

  async function change(remember: boolean) {
    if (!prefs) return;
    const before = prefs;
    setPrefs({ ...prefs, remember_conversations: remember }); setSaving(true); setError(null); setNote(null);
    try { const saved = await api.saveMyPreferences(remember); setPrefs(saved); setNote(savedMessage(saved)); }
    catch (e) { setPrefs(before); setError(t('chatMemory.saveFailed', { reason: describeError(e) })); }
    finally { setSaving(false); }
  }

  const status = prefs ? chatMemoryStatus(prefs) : null;
  return <section className="surface settingsForm chatMemorySetting" aria-labelledby={`${describedBy}-title`} aria-busy={loading || saving}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('chatMemory.eyebrow')}</p><h2 id={`${describedBy}-title`}>{t('chatMemory.title')}</h2></div>
      {status && <span className={`statusBadge ${status.tone}`}>{status.badge}</span>}</div>
    {loading && !prefs && <p className="muted" role="status">{t('chatMemory.loading')}</p>}
    {prefs && <label className="settingToggle">
      <div><strong>{t('chatMemory.toggle')}</strong><p id={describedBy}>{status?.text}</p></div>
      <input ref={toggle} type="checkbox" checked={prefs.remember_conversations} disabled={saving} aria-describedby={describedBy} onChange={e => void change(e.target.checked)} />
    </label>}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {note && <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" />{note}</p>}
    <div className="actions">
      {!prefs && !loading && <button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button>}
      <button type="button" className="secondary" onClick={onReview}><MessageSquare aria-hidden="true" />{reviewLabel}</button>
    </div>
  </section>;
}
