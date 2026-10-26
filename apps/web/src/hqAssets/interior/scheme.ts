/**
 * Colour schemes for the interior assets. A scheme is a small palette: the builders pick every
 * colour from it, so one room kind can be dressed walnut, oak, slate or sand without new code.
 */
import { hex, type Rgb } from '../../lib/hqGeometry.ts';

export type InteriorScheme = Readonly<{
  name: string;
  /** Main timber for desks and tables. */
  wood: Rgb;
  /** Dark timber for feature walls and executive furniture. */
  woodDark: Rgb;
  marble: Rgb;
  metal: Rgb;
  dark: Rgb;
  paper: Rgb;
  fabric: Rgb;
  fabricAlt: Rgb;
  leather: Rgb;
  brass: Rgb;
  rug: Rgb;
  pot: Rgb;
  accent: Rgb;
}>;

export type InteriorSchemeName = 'walnut' | 'oak' | 'slate' | 'sand';

export const SCHEMES: Readonly<Record<InteriorSchemeName, InteriorScheme>> = {
  walnut: {
    name: 'walnut', wood: hex('#8a6244'), woodDark: hex('#4a2f20'), marble: hex('#ece8df'), metal: hex('#9aa5b1'), dark: hex('#26313b'),
    paper: hex('#f2f0ea'), fabric: hex('#4a6073'), fabricAlt: hex('#b58a54'), leather: hex('#5a3a2a'), brass: hex('#c9a24a'),
    rug: hex('#8f7b66'), pot: hex('#c9c2b2'), accent: hex('#45d0b5'),
  },
  oak: {
    name: 'oak', wood: hex('#c79b62'), woodDark: hex('#7a5a3a'), marble: hex('#f1eee6'), metal: hex('#aab4be'), dark: hex('#2d3640'),
    paper: hex('#f5f3ee'), fabric: hex('#5d8f78'), fabricAlt: hex('#d9b25f'), leather: hex('#8a5a3b'), brass: hex('#d3b25a'),
    rug: hex('#a9c2b4'), pot: hex('#e0d8c6'), accent: hex('#7fd39a'),
  },
  slate: {
    name: 'slate', wood: hex('#5b6670'), woodDark: hex('#2a323a'), marble: hex('#d9dde0'), metal: hex('#8a949f'), dark: hex('#1c242c'),
    paper: hex('#eceff1'), fabric: hex('#2f4b66'), fabricAlt: hex('#8a3f5c'), leather: hex('#2a2f36'), brass: hex('#b9c2cb'),
    rug: hex('#58697a'), pot: hex('#59656f'), accent: hex('#6aa7ff'),
  },
  sand: {
    name: 'sand', wood: hex('#b79a76'), woodDark: hex('#6b4e37'), marble: hex('#f3ede0'), metal: hex('#b3a999'), dark: hex('#3a3731'),
    paper: hex('#f7f2e8'), fabric: hex('#a8453b'), fabricAlt: hex('#3d6b52'), leather: hex('#7a4a30'), brass: hex('#c79b3a'),
    rug: hex('#d8c8a4'), pot: hex('#8d8777'), accent: hex('#f2b54c'),
  },
};

export function resolveScheme(input?: InteriorSchemeName | Partial<InteriorScheme>): InteriorScheme {
  if (!input) return SCHEMES.walnut;
  if (typeof input === 'string') return SCHEMES[input] ?? SCHEMES.walnut;
  return { ...SCHEMES.walnut, ...input };
}
