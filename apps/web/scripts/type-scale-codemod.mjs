// Rewrites raw `font-size` values in apps/web CSS to the tokens in src/typography.css.
// Usage: node scripts/type-scale-codemod.mjs [--check] <file.css>...   (no files: every src/*.css)
// Re-run it on a file after resolving a merge conflict; it only touches font-size declarations.
// Sizes snap to the nearest step (a tie rounds up); em, %, pt, inherit and var() are left alone.
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const STEPS = { 11: '2xs', 12: 'xs', 13: 'sm', 14: 'md', 15: 'base', 16: 'lg', 18: 'xl', 20: '2xl', 22: '3xl', 24: '4xl', 28: '5xl', 32: '6xl' };
const FLUID = {
  'clamp(28px, 4vw, 40px)': 'fluid-display',
  'clamp(24px, 3.4vw, 34px)': 'fluid-greeting',
  'clamp(20px, 2.4vw, 26px)': 'fluid-greeting-sm',
  'clamp(15px, 1.6vw, 19px)': 'fluid-lead',
  'clamp(18px, 2.1vw, 24px)': 'fluid-figure',
  'clamp(22px, 2.4vw, 30px)': 'fluid-figure-lg',
};
const px = Object.keys(STEPS).map(Number);

export function tokenFor(value) {
  const v = value.trim();
  if (FLUID[v]) return `var(--text-${FLUID[v]})`;
  const m = /^(\d*\.?\d+)(px|rem)$/.exec(v);
  if (!m) return null;
  const size = m[2] === 'rem' ? Number(m[1]) * 16 : Number(m[1]);
  const best = px.reduce((a, b) => (Math.abs(b - size) < Math.abs(a - size) || (Math.abs(b - size) === Math.abs(a - size) && b > a) ? b : a));
  return `var(--text-${STEPS[best]})`;
}

export function migrate(css) {
  return css.replace(/(font-size:\s*)([^;}]+)/g, (all, head, value) => {
    const token = tokenFor(value);
    return token ? head + token : all;
  });
}

const src = fileURLToPath(new URL('../src/', import.meta.url));
const args = process.argv.slice(2);
const check = args.includes('--check');
const files = args.filter((a) => !a.startsWith('--'));
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const targets = files.length ? files : readdirSync(src).filter((f) => f.endsWith('.css') && f !== 'typography.css').map((f) => src + f);
  let changed = 0;
  for (const file of targets) {
    const before = readFileSync(file, 'utf8');
    const after = migrate(before);
    if (after !== before) { changed++; if (!check) writeFileSync(file, after); }
  }
  console.log(`${check ? 'would change' : 'changed'} ${changed} of ${targets.length} files`);
  if (check && changed) process.exit(1);
}
