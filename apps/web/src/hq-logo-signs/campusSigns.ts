/**
 * The brand signs of the 3D campus (docs/ANUM_MINE_HQ_LOGO_SIGNS.md): a lazy chunk the campus view loads only when a building has a
 * sign set up. It fetches the owner's logos with their own credentials (no public URL), keeps at most `MAX_LOGOS` of them as small decoded
 * bitmaps (a long edge of at most `LOGO_EDGE` pixels, about 4 MB at the very most), and hands the sign atlas a `paint` function per building.
 * Any failure (a 404, a bad picture, a network error) leaves that building on the lettered sign. No new texture is made: the logos are
 * drawn into the existing sign atlas cell.
 */
import type { BuildingSign } from '../lib/campusModel.ts';
import type { SignText } from '../lib/hqRenderTypes.ts';
import { normalizeColor, SIGN_STYLES, WORDMARK, type SignStyle } from './signModel.ts';
import { paintSign } from './signPainter.ts';

/** At most this many logos are decoded at once (the nearest buildings first). */
export const MAX_LOGOS = 16;
/** A decoded logo is scaled to at most this many pixels on its long edge: a sign cell shows it at under 120 pixels. */
export const LOGO_EDGE = 256;
const CONCURRENCY = 3;

export type Logo = Readonly<{ image: ImageBitmap; width: number; height: number }>;
/** `workspaceId` is set for another workspace's building: its logo is read in that workspace, with the person's own membership there. */
export type LogoTarget = Readonly<{ id: string; buildingId: string; sha: string; x: number; z: number; workspaceId?: string }>;

/** The logos to load: the buildings that have one, nearest to `focus` first, at most `cap`. Pure. */
export function pickLogoTargets(candidates: readonly LogoTarget[], focus: Readonly<{ x: number; z: number }>, cap: number = MAX_LOGOS): LogoTarget[] {
  return candidates
    .filter((c) => c.sha.length > 0)
    .map((c) => ({ c, d: Math.hypot(c.x - focus.x, c.z - focus.z) }))
    .sort((a, b) => a.d - b.d || (a.c.id < b.c.id ? -1 : 1))
    .slice(0, Math.max(0, cap))
    .map((e) => e.c);
}

export const logoKey = (t: Pick<LogoTarget, 'buildingId' | 'sha'>): string => `${t.buildingId}:${t.sha}`;

async function bitmapOf(blob: Blob): Promise<Logo> {
  const first = await createImageBitmap(blob);
  const long = Math.max(first.width, first.height);
  if (long <= LOGO_EDGE) return { image: first, width: first.width, height: first.height };
  const k = LOGO_EDGE / long;
  const width = Math.max(1, Math.round(first.width * k)), height = Math.max(1, Math.round(first.height * k));
  const small = await createImageBitmap(first, { resizeWidth: width, resizeHeight: height, resizeQuality: 'medium' });
  first.close();
  return { image: small, width, height };
}

export type Fetcher = (buildingId: string, signal?: AbortSignal, workspaceId?: string) => Promise<Blob | null>;
/** The real fetcher is imported on first use, so the pure parts of this module load without the API client. */
const defaultFetcher: Fetcher = async (buildingId, signal, workspaceId) => (await import('./logoClient.ts')).fetchLogo(buildingId, signal, workspaceId);

/**
 * Loads the logos of `targets`, reusing the ones already decoded in `previous` (same building and hash). Returns the new map and closes
 * the bitmaps that are no longer wanted. A target that fails is simply missing from the result.
 */
export async function loadLogos(targets: readonly LogoTarget[], previous: ReadonlyMap<string, Logo>, signal: AbortSignal, fetcher: Fetcher = defaultFetcher): Promise<Map<string, Logo>> {
  const wanted = new Map(targets.map((t) => [logoKey(t), t]));
  const next = new Map<string, Logo>();
  for (const [key, logo] of previous) if (wanted.has(key)) next.set(key, logo);
  const todo = [...wanted].filter(([key]) => !next.has(key));
  let cursor = 0;
  const worker = async (): Promise<void> => {
    while (cursor < todo.length && !signal.aborted) {
      const [key, target] = todo[cursor++];
      try {
        const blob = await fetcher(target.buildingId, signal, target.workspaceId);
        if (!blob || signal.aborted) continue;
        next.set(key, await bitmapOf(blob));
      } catch { /* the lettered sign stays: a missing or unreadable logo is never an error on the campus */ }
    }
  };
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, todo.length) }, worker));
  if (signal.aborted) { for (const [key, logo] of next) if (!previous.has(key)) logo.image.close(); return new Map(previous); }
  for (const [key, logo] of previous) if (!next.has(key)) logo.image.close();
  return next;
}

const styleOf = (value: string | null): SignStyle | null => (value && (SIGN_STYLES as readonly string[]).includes(value) ? (value as SignStyle) : null);

/** The sign text entry for a branded building: the key that moves when anything on the sign changes, and the painter. */
export function signFor(sign: BuildingSign, name: string, rtl: boolean, logos: ReadonlyMap<string, Logo>): Pick<SignText, 'brand' | 'paint'> {
  const style = styleOf(sign.style);
  const color = normalizeColor(sign.color);
  const logo = sign.logoSha256 ? logos.get(logoKey({ buildingId: sign.buildingId, sha: sign.logoSha256 })) ?? null : null;
  return {
    brand: `${style ?? ''}|${color ?? ''}|${logo ? sign.logoSha256 : ''}`,
    paint: (ctx, x, y, k) => { paintSign(ctx as never, x, y, k, { name, rtl, style, color, logo: logo?.image ?? null, logoWidth: logo?.width, logoHeight: logo?.height, wordmark: WORDMARK }); },
  };
}

/** What the stage reports for QA: how many signs carry a logo and how many are branded at all. */
export function signStats(buildings: readonly Readonly<{ sign?: BuildingSign }>[], logos: ReadonlyMap<string, Logo>): Readonly<{ branded: number; logos: number }> {
  let branded = 0, withLogo = 0;
  for (const b of buildings) {
    if (!b.sign) continue;
    branded += 1;
    if (b.sign.logoSha256 && logos.has(logoKey({ buildingId: b.sign.buildingId, sha: b.sign.logoSha256 }))) withLogo += 1;
  }
  return { branded, logos: withLogo };
}
