// The avatar style of an agent or a team member (docs/ANUM_MINE_HQ_REALISM_ASSETS.md, "Avatar styles").
// A style is a drawing choice the owner makes for the headquarters scene. It is never a claim about a
// real person or agent and is never read from a name. The lists mirror services/api/anum_api/avatar_appearance.py
// (a test fails when they drift apart); every field is optional and unset means "the default for this one".

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

/** What the owner chose. `null` or an absent field means the neutral default derived from the record id. */
export interface AvatarAppearance {
  avatar_style?: AvatarStyle | null;
  skin_tone?: SkinTone | null;
  hair_style?: HairStyle | null;
  hair_color?: HairColor | null;
  outfit?: Outfit | null;
  accent?: Accent | null;
}
