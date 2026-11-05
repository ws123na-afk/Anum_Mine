/**
 * Checks on a chosen logo file before anything is sent (docs/ANUM_MINE_HQ_LOGO_SIGNS.md). The server checks again and is the
 * authority; this only saves a round trip and gives a plain reason. The type comes from the first bytes, never from the file name
 * or the browser's `type`. Pure except `checkLogoFile`, which reads the first bytes of a `File`.
 */
export const MAX_LOGO_BYTES = 1_048_576;

export type LogoProblem = 'empty' | 'tooBig' | 'svg' | 'gif' | 'notPicture';
export type LogoKind = 'png' | 'jpeg' | 'webp';
export type LogoCheck = Readonly<{ ok: true; kind: LogoKind }> | Readonly<{ ok: false; problem: LogoProblem }>;

const ascii = (bytes: Uint8Array, from: number, to: number): string => String.fromCharCode(...bytes.slice(from, to));

export function sniffImage(head: Uint8Array): LogoKind | 'svg' | 'gif' | null {
  if (head.length >= 8 && head[0] === 0x89 && ascii(head, 1, 4) === 'PNG' && head[4] === 0x0d && head[5] === 0x0a && head[6] === 0x1a && head[7] === 0x0a) return 'png';
  if (head.length >= 3 && head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return 'jpeg';
  if (head.length >= 12 && ascii(head, 0, 4) === 'RIFF' && ascii(head, 8, 12) === 'WEBP') return 'webp';
  if (head.length >= 6 && (ascii(head, 0, 6) === 'GIF87a' || ascii(head, 0, 6) === 'GIF89a')) return 'gif';
  const text = ascii(head, 0, Math.min(head.length, 512)).toLowerCase();
  if (text.includes('<svg') || text.trimStart().startsWith('<?xml')) return 'svg';
  return null;
}

export function checkLogoBytes(size: number, head: Uint8Array): LogoCheck {
  if (size <= 0) return { ok: false, problem: 'empty' };
  if (size > MAX_LOGO_BYTES) return { ok: false, problem: 'tooBig' };
  const kind = sniffImage(head);
  if (kind === 'svg' || kind === 'gif') return { ok: false, problem: kind };
  if (!kind) return { ok: false, problem: 'notPicture' };
  return { ok: true, kind };
}

export async function checkLogoFile(file: Blob): Promise<LogoCheck> {
  const head = new Uint8Array(await file.slice(0, 512).arrayBuffer());
  return checkLogoBytes(file.size, head);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
