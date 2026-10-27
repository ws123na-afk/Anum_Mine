import { deflateSync } from 'node:zlib';

// A plainly fake test logo, built in code (no company's mark): a coloured disc with a white square, on a transparent background.
// Used by the logo sign specs (docs/ANUM_MINE_HQ_LOGO_SIGNS.md).
const crcTable = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (buf: Buffer): number => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
function chunk(type: string, data: Buffer): Buffer {
  const head = Buffer.alloc(4); head.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'latin1'), data]);
  const tail = Buffer.alloc(4); tail.writeUInt32BE(crc32(body));
  return Buffer.concat([head, body, tail]);
}

export function logoPng(width = 160, height = 80, rgb: readonly [number, number, number] = [226, 88, 34]): Buffer {
  const rows: Buffer[] = [];
  const radius = Math.min(width, height) / 2 - 2;
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 4);
    for (let x = 0; x < width; x += 1) {
      const dx = x - width / 2, dy = y - height / 2;
      const inDisc = Math.hypot(dx, dy) <= radius;
      const inSquare = Math.abs(dx) <= radius / 3 && Math.abs(dy) <= radius / 3;
      const at = 1 + x * 4;
      if (inSquare) { row[at] = 255; row[at + 1] = 255; row[at + 2] = 255; row[at + 3] = 255; }
      else if (inDisc) { row[at] = rgb[0]; row[at + 1] = rgb[1]; row[at + 2] = rgb[2]; row[at + 3] = 255; }
    }
    rows.push(row);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', header), chunk('IDAT', deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))]);
}
