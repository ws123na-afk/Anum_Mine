// Copies the in-app help articles (docs/help/*.md) into the Flutter app's assets, so the phone
// bundles them and help works offline (docs/help.md). Run after editing an article:
//   node scripts/sync-help.mjs
// apps/mobile/test/help/help_assets_test.dart fails when the copies differ from docs/help.
import { copyFileSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const source = join(root, 'docs', 'help');
const target = join(root, 'apps', 'mobile', 'assets', 'help');
mkdirSync(target, { recursive: true });
const articles = readdirSync(source).filter((name) => name.endsWith('.md'));
for (const name of readdirSync(target)) {
  if (!articles.includes(name)) rmSync(join(target, name));
}
for (const name of articles) copyFileSync(join(source, name), join(target, name));
console.log(`Copied ${articles.length} help articles to apps/mobile/assets/help.`);
