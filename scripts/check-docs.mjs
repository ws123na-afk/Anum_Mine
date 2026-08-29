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
