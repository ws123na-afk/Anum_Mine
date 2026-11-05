import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, extname, join, relative, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const documentationFiles = [join(root, 'README.md'), ...markdownFiles(join(root, 'docs'))];
const failures = [];

for (const file of documentationFiles) {
  const content = readFileSync(file, 'utf8');
  for (const match of content.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
    const target = match[1].split('#')[0];
    if (!target || /^[a-z]+:/i.test(target)) continue;
    const resolved = resolve(dirname(file), decodeURIComponent(target));
    if (!existsSync(resolved)) failures.push(`${relative(root, file)}: missing link target ${target}`);
  }
}

const readme = readFileSync(join(root, 'README.md'), 'utf8');
for (const file of markdownFiles(join(root, 'docs'))) {
  const path = relative(root, file).replaceAll('\\', '/');
  if (!path.startsWith('docs/decisions/') && !readme.includes(`(${path})`)) {
    failures.push(`README.md: documentation index is missing ${path}`);
  }
}

// The phone bundles copies of docs/help (docs/help.md); the Flutter test help_assets_test.dart checks
// them too, but this runs everywhere docs:check runs, also where Flutter is not installed.
const helpSource = join(root, 'docs', 'help');
const helpTarget = join(root, 'apps', 'mobile', 'assets', 'help');
const helpArticles = (dir) => (existsSync(dir) ? readdirSync(dir).filter((name) => name.endsWith('.md')).sort() : []);
const bundled = new Set(helpArticles(helpTarget));
for (const name of helpArticles(helpSource)) {
  if (!bundled.has(name)) failures.push(`apps/mobile/assets/help: missing ${name} (run node scripts/sync-help.mjs)`);
  else if (readFileSync(join(helpSource, name), 'utf8') !== readFileSync(join(helpTarget, name), 'utf8')) {
    failures.push(`apps/mobile/assets/help/${name} differs from docs/help (run node scripts/sync-help.mjs)`);
  }
  bundled.delete(name);
}
for (const name of bundled) failures.push(`apps/mobile/assets/help/${name} has no docs/help article (run node scripts/sync-help.mjs)`);

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}

console.log(`Documentation check passed (${documentationFiles.length} files).`);

function markdownFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return markdownFiles(path);
    return extname(entry.name) === '.md' ? [path] : [];
  });
}
