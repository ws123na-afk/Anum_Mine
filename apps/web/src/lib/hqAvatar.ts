/**
 * Avatar styles for the people of the headquarters scenes. Pure: no three.js and no DOM, so the unit runner
 * covers the rules.
 *
 * WHAT THIS IS. A figure appears only for an agent (or person) with a real desk assignment. How it is drawn is a
 * visual style: the owner may choose it on the profile, otherwise a neutral default is derived from the record id.
 * The style says nothing about the real person or agent, is not a gender, age or ethnicity claim, and is NEVER
 * derived from a name (no function in this file takes a name). The palette lists mirror
 * `services/api/anum_api/avatar_appearance.py` and `packages/contracts/src/avatar.ts` (a test fails on drift).
 */
import type { AvatarAppearance } from '@anum/contracts';

export const AVATAR_STYLES = ['feminine', 'masculine', 'neutral'] as const;
export const SKIN_TONES = ['tone_1', 'tone_2', 'tone_3', 'tone_4', 'tone_5', 'tone_6'] as const;
export const HAIR_STYLES = ['bun', 'long', 'swept', 'spiky', 'parted'] as const;
export const HAIR_COLORS = ['black', 'dark_brown', 'brown', 'auburn', 'red', 'blonde', 'grey'] as const;
export const OUTFITS = ['formal', 'smart_casual', 'uniform'] as const;
export const ACCENTS = ['navy', 'teal', 'forest', 'crimson', 'plum', 'amber', 'slate', 'brass'] as const;

export type AvatarStyle = (typeof AVATAR_STYLES)[number];
export type SkinTone = (typeof SKIN_TONES)[number];
export type HairStyle = (typeof HAIR_STYLES)[number];
export type HairColor = (typeof HAIR_COLORS)[number];
export type Outfit = (typeof OUTFITS)[number];
export type Accent = (typeof ACCENTS)[number];

/** Which hair shapes exist for which build: the drawing art decides (one hair sculpt per character model). */
export const HAIR_BY_STYLE: Record<AvatarStyle, readonly HairStyle[]> = {
  feminine: ['bun', 'long', 'swept'],
  masculine: ['spiky', 'parted'],
  neutral: ['spiky', 'parted', 'bun'],
};

/** The character model that carries each hair shape (see `hqModels.ts`: person_0 .. person_4). */
export const MODEL_OF_HAIR: Record<HairStyle, PersonModelKey> = {
  spiky: 'person_0', parted: 'person_1', bun: 'person_2', long: 'person_3', swept: 'person_4',
};
export type PersonModelKey = 'person_0' | 'person_1' | 'person_2' | 'person_3' | 'person_4';

/** Colours as sRGB hex. The skin ramp is a drawing palette from lighter to deeper, not a claim about anyone. */
export const SKIN_HEX: Record<SkinTone, string> = {
  tone_1: '#f3d2b3', tone_2: '#e8b98f', tone_3: '#d29b6c', tone_4: '#b57d52', tone_5: '#8d5b3a', tone_6: '#5f3c27',
};
export const HAIR_HEX: Record<HairColor, string> = {
  black: '#1c1a19', dark_brown: '#3b2a1f', brown: '#6b4a2b', auburn: '#8a3f26', red: '#b0502c', blonde: '#cfaa5e', grey: '#a8a299',
};
export const ACCENT_HEX: Record<Accent, string> = {
  navy: '#223f78', teal: '#16807a', forest: '#237350', crimson: '#a92a3d', plum: '#6e3280', amber: '#c98a1f', slate: '#5a6780', brass: '#b8923a',
};

/** Role families: the cue is outfit colour and one small accessory, never a likeness. */
export type RoleFamily = 'reception' | 'security' | 'operations' | 'care' | 'executive' | 'finance' | 'field' | 'office';

type RoleDefaults = Readonly<{ outfit: Outfit; accent: Accent | null; headset: boolean; badge: boolean }>;
export const ROLE_DEFAULTS: Record<RoleFamily, RoleDefaults> = {
  reception: { outfit: 'smart_casual', accent: 'brass', headset: true, badge: false },
  security: { outfit: 'uniform', accent: 'slate', headset: false, badge: true },
  operations: { outfit: 'uniform', accent: 'amber', headset: true, badge: false },
  care: { outfit: 'smart_casual', accent: 'teal', headset: true, badge: false },
  executive: { outfit: 'formal', accent: 'navy', headset: false, badge: false },
  finance: { outfit: 'formal', accent: 'forest', headset: false, badge: true },
  field: { outfit: 'uniform', accent: 'amber', headset: false, badge: false },
  office: { outfit: 'smart_casual', accent: null, headset: false, badge: false },
};

/** The role family of a desk: the department text wins when it names one, else the kind of room the desk is in. Never a name. */
export function roleFamily(input: Readonly<{ roomKind?: string | null; department?: string | null }>): RoleFamily {
  const d = (input.department ?? '').toLowerCase();
  if (/secur|guard/.test(d)) return 'security';
  if (/financ|account|treasur/.test(d)) return 'finance';
  if (/executive|leadership|director|\bceo\b|\bcoo\b|\bcfo\b/.test(d)) return 'executive';
  if (/reception|front desk|visitor/.test(d)) return 'reception';
  switch (input.roomKind) {
    case 'reception': return 'reception';
    case 'executive': return 'executive';
    case 'customer-care': return 'care';
    case 'operations': return 'operations';
    case 'warehouse': case 'workshop': return 'field';
    default: break;
  }
  if (/operat|dispatch|logistic/.test(d)) return 'operations';
  if (/support|customer|care|service desk/.test(d)) return 'care';
  return 'office';
}

/** FNV-1a (the same hash the model catalogue uses), so one id always gives one look. */
export function seedHash(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) { h ^= seed.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

/** An independent stream per attribute: the same id, a different salt, so the attributes do not move together. */
const stream = (seed: string, salt: string): number => seedHash(`${salt}\u0001${seed}`);

function weighted<T>(roll: number, items: readonly (readonly [T, number])[]): T {
  const total = items.reduce((n, [, w]) => n + w, 0);
  let at = roll % total;
  for (const [item, weight] of items) { if (at < weight) return item; at -= weight; }
  return items[items.length - 1][0];
}

export type ResolvedAvatar = Readonly<{
  avatar_style: AvatarStyle; skin_tone: SkinTone; hair_style: HairStyle; hair_color: HairColor; outfit: Outfit; accent: Accent;
}>;

/**
 * The default look of a record: the neutral build (owner rule: a build is drawn only when the owner chose one), with skin
 * tone, hair, outfit and accent deterministic from the id (and the role family for outfit cues), spread over the palette.
 * It reads nothing else: not a name, not a description.
 */
export function defaultAvatar(seed: string, role: RoleFamily = 'office'): ResolvedAvatar {
  const build: AvatarStyle = 'neutral';
  const hairs = HAIR_BY_STYLE[build];
  const defaults = ROLE_DEFAULTS[role];
  return {
    avatar_style: build,
    skin_tone: SKIN_TONES[stream(seed, 'skin') % SKIN_TONES.length],
    hair_style: hairs[stream(seed, 'hair') % hairs.length],
    hair_color: weighted<HairColor>(stream(seed, 'haircolor'), [['black', 22], ['dark_brown', 22], ['brown', 18], ['auburn', 10], ['red', 6], ['blonde', 14], ['grey', 8]]),
    outfit: role === 'office' ? weighted<Outfit>(stream(seed, 'outfit'), [['smart_casual', 50], ['formal', 30], ['uniform', 20]]) : defaults.outfit,
    accent: defaults.accent ?? ACCENTS[stream(seed, 'accent') % ACCENTS.length],
  };
}

const known = <T extends string>(list: readonly T[], value: unknown): T | null => (typeof value === 'string' && (list as readonly string[]).includes(value) ? (value as T) : null);

/** The chosen style, field by field, over the default. An unknown or missing field keeps the default for that attribute. */
export function resolveAvatar(seed: string, choice: AvatarAppearance | null | undefined, role: RoleFamily = 'office'): ResolvedAvatar {
  const base = defaultAvatar(seed, role);
  const style = known(AVATAR_STYLES, choice?.avatar_style);
  const hair = known(HAIR_STYLES, choice?.hair_style);
  // A hair shape with no chosen build takes the default build when that offers it, else the first build that does.
  const build = style ?? (hair && !HAIR_BY_STYLE[base.avatar_style].includes(hair) ? AVATAR_STYLES.find((s) => HAIR_BY_STYLE[s].includes(hair))! : base.avatar_style);
  const offered = HAIR_BY_STYLE[build];
  const hairStyle = hair && offered.includes(hair) ? hair : offered.includes(base.hair_style) ? base.hair_style : offered[stream(seed, 'hair') % offered.length];
  return {
    avatar_style: build,
    skin_tone: known(SKIN_TONES, choice?.skin_tone) ?? base.skin_tone,
    hair_style: hairStyle,
    hair_color: known(HAIR_COLORS, choice?.hair_color) ?? base.hair_color,
    outfit: known(OUTFITS, choice?.outfit) ?? base.outfit,
    accent: known(ACCENTS, choice?.accent) ?? base.accent,
  };
}

// ----------------------------------------------------------------------------- colours

const toRgb = (hex: string): [number, number, number] => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
const toHex = (c: readonly number[]): string => `#${c.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')}`;
/** `amount` of `b` mixed into `a`. */
export function mixHex(a: string, b: string, amount: number): string {
  const x = toRgb(a), y = toRgb(b);
  return toHex(x.map((v, i) => v + (y[i] - v) * amount));
}
/** WCAG relative luminance, for the text colour of a chip. */
export function luminance(hex: string): number {
  const lin = toRgb(hex).map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}
/** WCAG contrast ratio of two colours. */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
/** Dark ink or white, whichever has the higher contrast on `hex` (at least 4.5 for every accent colour of the palette). */
export const inkOn = (hex: string): '#111418' | '#ffffff' => (contrastRatio(hex, '#ffffff') >= contrastRatio(hex, '#111418') ? '#ffffff' : '#111418');

/** Everything the renderers need to draw one figure. All colours are sRGB hex. */
export type FigureLook = Readonly<{
  avatar: ResolvedAvatar;
  role: RoleFamily;
  modelKey: PersonModelKey;
  /** Per axis scale on top of the placement scale: the build's width and a small deterministic height variation. */
  scale: readonly [number, number, number];
  skin: string; hair: string; top: string; bottom: string; trim: string; accent: string;
  headset: boolean; badge: boolean;
  /** A short code for tests and the canvas dataset: build, hair, outfit, skin. Never a name. */
  code: string;
}>;

const NEUTRAL_WIDTH = 0.93;

export function figureLook(seed: string, choice: AvatarAppearance | null | undefined, role: RoleFamily = 'office'): FigureLook {
  const avatar = resolveAvatar(seed, choice, role);
  const accent = ACCENT_HEX[avatar.accent];
  let top: string, bottom: string, trim: string, trimAccent: string;
  if (avatar.outfit === 'formal') {
    top = mixHex(accent, '#12161c', 0.74); bottom = mixHex(accent, '#12161c', 0.8); trim = '#f1f2f6'; trimAccent = accent;
  } else if (avatar.outfit === 'smart_casual') {
    top = accent; bottom = mixHex(accent, '#252a33', 0.72); trim = mixHex(accent, '#ffffff', 0.78); trimAccent = mixHex(accent, '#14181d', 0.4);
  } else {
    top = accent; bottom = '#2a2f38'; trim = mixHex(accent, '#ffffff', 0.4); trimAccent = avatar.accent === 'amber' ? '#f2efe6' : '#e3b341';
  }
  const height = 0.97 + (stream(seed, 'height') % 7) / 100;
  const width = avatar.avatar_style === 'neutral' ? NEUTRAL_WIDTH : 1;
  const cues = ROLE_DEFAULTS[role];
  return {
    avatar, role, modelKey: MODEL_OF_HAIR[avatar.hair_style],
    scale: [width * (0.99 + (height - 0.97)), height, width * (0.99 + (height - 0.97))],
    skin: SKIN_HEX[avatar.skin_tone], hair: HAIR_HEX[avatar.hair_color], top, bottom, trim, accent: trimAccent,
    headset: cues.headset, badge: cues.badge,
    code: `${avatar.avatar_style}/${avatar.hair_style}/${avatar.outfit}/${avatar.skin_tone}`,
  };
}

/** Initials for a chip. The name is shown as the user wrote it; nothing is inferred from it. */
export function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  const first = [...words[0]][0] ?? '?';
  const last = words.length > 1 ? [...words[words.length - 1]][0] ?? '' : '';
  return (first + last).toLocaleUpperCase();
}

/** Colours of the small round chip used in lists and cards: the accent as background, the skin tone as ring. */
export function chipColours(seed: string, choice: AvatarAppearance | null | undefined, role: RoleFamily = 'office'): Readonly<{ background: string; ring: string; ink: string; hair: string }> {
  const a = resolveAvatar(seed, choice, role);
  const background = ACCENT_HEX[a.accent];
  return { background, ring: SKIN_HEX[a.skin_tone], ink: inkOn(background), hair: HAIR_HEX[a.hair_color] };
}
