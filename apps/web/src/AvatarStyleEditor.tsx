// The avatar style of an agent or a team member (docs/ANUM_MINE_HQ_REALISM_ASSETS.md, "Avatar styles"): a live preview, one group of
// choices per attribute (each with an Automatic choice) and Save. It is a drawing style, never a claim about the person, and
// nothing is guessed from a name. Used on the agent page, in the Team panel and on a team member's profile.
import { useEffect, useId, useMemo, useState, type CSSProperties } from 'react';
import { Check, Palette, RotateCcw } from 'lucide-react';
import type { AvatarAppearance } from '@anum/contracts';
import {
  ACCENTS, ACCENT_HEX, AVATAR_STYLES, HAIR_BY_STYLE, HAIR_COLORS, HAIR_HEX, OUTFITS, SKIN_HEX, SKIN_TONES, chipColours, figureLook,
  initialsOf, resolveAvatar, roleFamily, type RoleFamily,
} from './lib/hqAvatar';
import { describeError } from './lib/errors';
import { Modal } from './Dialog';
import { t, type MessageKey } from './lib/i18n';
import './avatarStyle.css';

const KEYS = ['avatar_style', 'skin_tone', 'hair_style', 'hair_color', 'outfit', 'accent'] as const;
const clean = (v: AvatarAppearance | null | undefined): AvatarAppearance => Object.fromEntries(KEYS.filter((k) => v?.[k]).map((k) => [k, v![k]])) as AvatarAppearance;
const isEmpty = (v: AvatarAppearance): boolean => KEYS.every((k) => !v[k]);
const same = (a: AvatarAppearance, b: AvatarAppearance): boolean => KEYS.every((k) => (a[k] ?? null) === (b[k] ?? null));

/** A round chip with the initials in the accent colour and a ring in the skin tone, so lists and cards match the scene. */
export function AvatarChip({ id, name, avatar, department, size = 32, decorative = false }: { id: string; name: string; avatar?: AvatarAppearance | null; department?: string | null; size?: number; decorative?: boolean }) {
  const c = useMemo(() => chipColours(id, avatar, roleFamilyOf(department)), [id, avatar, department]);
  return <span className="avatarChip" {...(decorative ? { 'aria-hidden': true } : { role: 'img', 'aria-label': t('avatar.chipLabel', { name }) })} data-avatar-chip={id}
    style={{ '--chip-bg': c.background, '--chip-ring': c.ring, '--chip-ink': c.ink, '--chip-hair': c.hair, '--chip-size': `${size}px` } as CSSProperties}>
    <span aria-hidden="true">{initialsOf(name)}</span>
  </span>;
}

export const roleFamilyOf = (department?: string | null): RoleFamily => roleFamily({ department });

/** A flat bust of the resolved style: face, hair, shoulders in the outfit colours, a tie or lanyard in the accent, a headset when the role has one. */
export function AvatarPreview({ seed, value, role }: { seed: string; value: AvatarAppearance | null | undefined; role: RoleFamily }) {
  const look = useMemo(() => figureLook(seed, value, role), [seed, value, role]);
  const hairStyle = look.avatar.hair_style;
  const formal = look.avatar.outfit === 'formal';
  const shadow = '#00000022';
  return <svg className="avatarPreview" viewBox="0 0 120 140" role="img" aria-label={t('avatar.preview')} data-look={look.code}>
    <rect width="120" height="140" rx="14" fill="var(--avatar-stage, #e7ecef)" />
    {hairStyle === 'long' || hairStyle === 'swept' ? <path d="M28 62c0-30 14-44 32-44s32 14 32 44v50H28z" fill={look.hair} /> : null}
    <path d="M14 140c0-26 18-42 46-42s46 16 46 42z" fill={look.top} />
    <path d="M44 98l16 22 16-22 8 4-24 38-24-38z" fill={look.trim} />
    {formal ? <path d="M56 106h8l3 22-7 8-7-8z" fill={look.accent} /> : <path d="M46 100l14 26 14-26" fill="none" stroke={look.accent} strokeWidth="4" />}
    {look.badge ? <rect x="70" y="116" width="12" height="15" rx="2" fill={look.accent} stroke="#ffffff88" /> : null}
    <rect x="52" y="82" width="16" height="20" rx="6" fill={look.skin} />
    <rect x="52" y="90" width="16" height="6" fill={shadow} />
    <ellipse cx="60" cy="60" rx="25" ry="29" fill={look.skin} />
    <ellipse cx="35" cy="62" rx="4" ry="7" fill={look.skin} /><ellipse cx="85" cy="62" rx="4" ry="7" fill={look.skin} />
    {hairStyle === 'spiky' ? <path d="M34 50l2-20 9 10 5-18 8 14 7-16 7 16 8-12 4 18c-6-10-14-14-25-14s-19 4-25 22z" fill={look.hair} /> : null}
    {hairStyle === 'parted' ? <path d="M34 56c0-22 12-32 26-32s26 10 26 32c-4-12-10-18-20-20-8 8-20 12-32 20z" fill={look.hair} /> : null}
    {hairStyle === 'bun' ? <><circle cx="60" cy="24" r="10" fill={look.hair} /><path d="M34 56c0-22 12-32 26-32s26 10 26 32c-6-10-14-14-26-14s-20 4-26 14z" fill={look.hair} /></> : null}
    {hairStyle === 'long' ? <path d="M34 58c0-22 12-34 26-34s26 12 26 34c-6-10-14-14-26-14s-20 4-26 14z" fill={look.hair} /> : null}
    {hairStyle === 'swept' ? <path d="M33 62c-2-24 10-38 28-38 16 0 26 12 26 34-10-4-20-12-26-20-4 12-14 20-28 24z" fill={look.hair} /> : null}
    <circle cx="50" cy="62" r="2.6" fill="#262a31" /><circle cx="70" cy="62" r="2.6" fill="#262a31" />
    <path d="M52 74q8 6 16 0" fill="none" stroke="#262a31" strokeWidth="2.4" strokeLinecap="round" />
    {look.headset ? <><path d="M32 60c0-24 12-38 28-38s28 14 28 38" fill="none" stroke="#20262d" strokeWidth="4" /><rect x="27" y="56" width="8" height="16" rx="3" fill="#20262d" /><rect x="85" y="56" width="8" height="16" rx="3" fill="#20262d" /><path d="M31 70c2 10 10 14 20 12" fill="none" stroke="#20262d" strokeWidth="2.5" /></> : null}
  </svg>;
}

type Option = { value: string; label: string; swatch?: string };

function Group({ legend, name, value, options, disabled, onChange }: { legend: string; name: string; value: string | null | undefined; options: Option[]; disabled: boolean; onChange: (v: string | null) => void }) {
  return <fieldset className="avatarGroup" disabled={disabled}>
    <legend>{legend}</legend>
    <div className="avatarOptions" role="radiogroup" aria-label={legend}>
      <label className="avatarOption isAuto"><input type="radio" name={name} checked={!value} onChange={() => onChange(null)} /><span>{t('avatar.auto')}</span></label>
      {options.map((o) => <label key={o.value} className={`avatarOption${o.swatch ? ' isSwatch' : ''}`} title={o.label}>
        <input type="radio" name={name} checked={value === o.value} onChange={() => onChange(o.value)} aria-label={o.label} />
        {o.swatch ? <span className="avatarSwatch" style={{ background: o.swatch }} aria-hidden="true">{value === o.value && <Check aria-hidden="true" />}</span> : <span>{o.label}</span>}
      </label>)}
    </div>
  </fieldset>;
}

export type AvatarEditorProps = Readonly<{
  /** The record id (agent id or user id): the default style is drawn from it, never from a name. */
  seed: string;
  role: RoleFamily;
  value: AvatarAppearance | null | undefined;
  canEdit: boolean;
  readOnlyText?: string;
  /** Saves the chosen style (null clears it). Rejects with the API error to show it. */
  onSave: (value: AvatarAppearance | null) => Promise<void>;
  /** Called after a successful save. */
  onSaved?: () => void;
}>;

export function AvatarStyleEditor({ seed, role, value, canEdit, readOnlyText, onSave, onSaved }: AvatarEditorProps) {
  const [draft, setDraft] = useState<AvatarAppearance>(clean(value));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const name = useId();
  useEffect(() => { setDraft(clean(value)); }, [value]);
  const dirty = !same(draft, clean(value));
  const resolved = useMemo(() => resolveAvatar(seed, draft, role), [seed, draft, role]);
  const set = (key: (typeof KEYS)[number], v: string | null) => {
    setSaved(false);
    setDraft((d) => {
      const next = { ...d, [key]: v } as AvatarAppearance;
      // A build change drops a hair shape that build does not offer.
      if (key === 'avatar_style' && v && next.hair_style && !(HAIR_BY_STYLE[v as keyof typeof HAIR_BY_STYLE] as readonly string[]).includes(next.hair_style)) next.hair_style = null;
      return clean(next);
    });
  };
  async function save(next: AvatarAppearance | null) {
    setBusy(true); setError(null); setSaved(false);
    try { await onSave(next); setSaved(true); onSaved?.(); }
    catch (e) { setError(t('avatar.failed', { reason: describeError(e) })); }
    finally { setBusy(false); }
  }
  const hairOptions = HAIR_BY_STYLE[resolved.avatar_style].map((h) => ({ value: h, label: t(`avatar.hair.${h}` as MessageKey) }));
  const disabled = !canEdit || busy;
  return <section className="avatarEditor" aria-labelledby={`${name}-h`} data-avatar-editor={seed}>
    <h3 id={`${name}-h`}><Palette aria-hidden="true" />{t('avatar.title')}</h3>
    <p className="muted">{t('avatar.hint')}</p>
    <div className="avatarBody">
      <div className="avatarStage"><AvatarPreview seed={seed} value={draft} role={role} />
        <p className="muted avatarNote">{t('avatar.roleNote')}</p></div>
      <div className="avatarControls">
        <Group legend={t('avatar.group.style')} name={`${name}-style`} value={draft.avatar_style} disabled={disabled} onChange={(v) => set('avatar_style', v)}
          options={AVATAR_STYLES.map((s) => ({ value: s, label: t(`avatar.style.${s}` as MessageKey) }))} />
        <Group legend={t('avatar.group.skin')} name={`${name}-skin`} value={draft.skin_tone} disabled={disabled} onChange={(v) => set('skin_tone', v)}
          options={SKIN_TONES.map((s) => ({ value: s, label: t(`avatar.skin.${s}` as MessageKey), swatch: SKIN_HEX[s] }))} />
        <Group legend={t('avatar.group.hairStyle')} name={`${name}-hair`} value={draft.hair_style} disabled={disabled} onChange={(v) => set('hair_style', v)} options={hairOptions} />
        <Group legend={t('avatar.group.hairColor')} name={`${name}-haircolor`} value={draft.hair_color} disabled={disabled} onChange={(v) => set('hair_color', v)}
          options={HAIR_COLORS.map((c) => ({ value: c, label: t(`avatar.hairColor.${c}` as MessageKey), swatch: HAIR_HEX[c] }))} />
        <Group legend={t('avatar.group.outfit')} name={`${name}-outfit`} value={draft.outfit} disabled={disabled} onChange={(v) => set('outfit', v)}
          options={OUTFITS.map((o) => ({ value: o, label: t(`avatar.outfit.${o}` as MessageKey) }))} />
        <Group legend={t('avatar.group.accent')} name={`${name}-accent`} value={draft.accent} disabled={disabled} onChange={(v) => set('accent', v)}
          options={ACCENTS.map((a) => ({ value: a, label: t(`avatar.accent.${a}` as MessageKey), swatch: ACCENT_HEX[a] }))} />
      </div>
    </div>
    <p className="muted avatarNote">{t('avatar.autoNote')}</p>
    {!canEdit && <p className="muted" role="note">{readOnlyText ?? t('avatar.readOnly')}</p>}
    {error && <p className="fieldError" role="alert">{error}</p>}
    {saved && !error && <p className="avatarSaved" role="status"><Check aria-hidden="true" />{t('avatar.saved')}</p>}
    {canEdit && <div className="actions">
      <button type="button" disabled={busy || !dirty} onClick={() => void save(isEmpty(draft) ? null : draft)}>{busy ? t('avatar.saving') : t('avatar.save')}</button>
      <button type="button" className="secondary" disabled={busy || (isEmpty(draft) && isEmpty(clean(value)))} onClick={() => { setDraft({}); void save(null); }}><RotateCcw aria-hidden="true" />{t('avatar.reset')}</button>
    </div>}
  </section>;
}

/** The editor in a dialog, for the Team panel and other lists. */
export function AvatarStyleDialog({ name, onClose, ...editor }: AvatarEditorProps & { name: string; onClose: () => void }) {
  return <Modal title={t('avatar.dialogTitle', { name })} className="avatarDialog" onClose={onClose}>
    <AvatarStyleEditor {...editor} />
    <div className="actions"><button type="button" className="secondary" onClick={onClose} data-autofocus>{t('avatar.close')}</button></div>
  </Modal>;
}
